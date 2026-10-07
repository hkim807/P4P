"""Async collection, latest-frame delivery, failures, and dependency boundary."""

import asyncio
import contextlib
import io
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from robot.navel_client.main import (
    LatestLocomotion, _collect_perception, _replace_queued, _send_observations,
    collect_and_stream, parse_args,
)
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.transport import ObservationResponse, ObservationTransport, TransportError
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.behaviour_dispatch import BehaviourDispatcher, HANDLERS
from tests.test_decision_dispatch import response_for
from tests.fixtures import frame, locomotion, perception, person


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_frame_replaces_pending_frame(self):
        queue = asyncio.Queue(maxsize=1)
        _replace_queued(queue, {**frame(), "timestamp": 1})
        _replace_queued(queue, {**frame(), "timestamp": 2})
        self.assertEqual(queue.get_nowait()["timestamp"], 2)
        queue.task_done()
        await asyncio.wait_for(queue.join(), 1)

    async def test_stale_locomotion_becomes_unavailable(self):
        latest = LatestLocomotion((locomotion(), time.monotonic() - 2))
        queue = asyncio.Queue(maxsize=1)
        calls = 0

        class Robot:
            async def next_frame(self, timeout):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise TimeoutError
                if calls == 2:
                    return perception()
                await asyncio.Event().wait()

        task = asyncio.create_task(_collect_perception(
            Robot(), NavelObservationAdapter(), latest, queue, max_locomotion_age_s=1,
        ))
        try:
            observation = await asyncio.wait_for(queue.get(), 1)
            self.assertEqual(observation["robot"], {"linear_velocity": None, "angular_velocity": None})
            self.assertEqual(observation["safety"], {"lidar": None, "sonar": None})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_print_only_outputs_json_without_http(self):
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(frame())
        transport = NS(send=lambda _: self.fail("HTTP should not be called"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            task = asyncio.create_task(_send_observations(
                queue, transport, minimum_send_interval_s=0, print_only=True,
            ))
            try:
                await asyncio.wait_for(queue.join(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(json.loads(output.getvalue()), frame())

    async def test_transport_failure_does_not_stop_sender(self):
        queue = asyncio.Queue(maxsize=1)
        calls = []

        class Transport:
            def send(self, observation):
                calls.append(observation["timestamp"])
                if len(calls) == 1:
                    raise TransportError("offline")
                return ObservationResponse(200, {"accepted": True})

        queue.put_nowait({**frame(), "timestamp": 1})
        with self.assertLogs("robot.navel_client.main", level="WARNING"):
            task = asyncio.create_task(_send_observations(
                queue, Transport(), minimum_send_interval_s=0, print_only=False,
            ))
            try:
                await asyncio.wait_for(queue.join(), 1)
                queue.put_nowait({**frame(), "timestamp": 2})
                await asyncio.wait_for(queue.join(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(calls, [1, 2])

    async def test_http_runs_in_worker_and_newest_frame_survives_slow_request(self):
        queue = asyncio.Queue(maxsize=1)
        started = threading.Event()
        release = threading.Event()
        observations = []
        thread_ids = []

        class Transport:
            def send(self, observation):
                observations.append(observation["timestamp"])
                thread_ids.append(threading.get_ident())
                started.set()
                release.wait(2)
                return ObservationResponse(200, {"accepted": True})

        queue.put_nowait({**frame(), "timestamp": 1})
        task = asyncio.create_task(_send_observations(
            queue, Transport(), minimum_send_interval_s=0, print_only=False,
        ))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            _replace_queued(queue, {**frame(), "timestamp": 2})
            _replace_queued(queue, {**frame(), "timestamp": 3})
            release.set()
            await asyncio.wait_for(queue.join(), 1)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(observations, [1, 3])
        self.assertTrue(all(t != threading.get_ident() for t in thread_ids))

    async def test_sdk_disconnect_cancels_other_tasks(self):
        stopped = asyncio.Event()

        class Robot:
            async def next_locomotion(self, timeout):
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            async def next_frame(self, timeout):
                await asyncio.sleep(0)
                raise ConnectionAbortedError("robot disconnected")

        with self.assertRaises(ConnectionAbortedError):
            await collect_and_stream(Robot(), parse_args(["--print-only"]))
        self.assertTrue(stopped.is_set())

    async def test_trial_timeout_and_interruption_use_collector_cleanup(self):
        class Robot:
            async def next_locomotion(self, timeout):
                await asyncio.Event().wait()

            async def next_frame(self, timeout):
                await asyncio.Event().wait()

        args = parse_args(["--decision-dry-run", "--single-trial"])
        for interrupt in (False, True):
            trial = SingleTrial(wait_timeout_s=0.01 if not interrupt else 30)
            with patch("robot.navel_client.main.SingleTrial", return_value=trial):
                task = asyncio.create_task(collect_and_stream(Robot(), args))
                if interrupt:
                    await asyncio.sleep(0)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertIs(await asyncio.wait_for(task, 1), trial)
            self.assertEqual(trial.failure_reason, "INTERRUPTED" if interrupt else "NO_DECISION_TIMEOUT")

    async def test_route_lifecycle_stops_one_sender_before_zero(self):
        # SDK-shaped tasks, including an asynchronously settling cancellation.
        for outcome in ("decision", "finished", "timeout", "slow_http", "transport", "rejected", "collector", "head", "interrupt", "model_stall", "poll_stall"):
            with self.subTest(outcome=outcome):
                events = []
                readers = set()
                started = asyncio.Event()
                finish = asyncio.Event()
                tracking = asyncio.Event()
                release_http = threading.Event()
                http_done = threading.Event()
                poll_started = threading.Event()
                packets = asyncio.Queue()
                packets.put_nowait(perception(person(17)))
                packets.put_nowait(perception(person(17), person(18)))
                packets.put_nowait(perception())
                active = False

                class Robot:
                    def move_base(self, distance, *, speed, acceleration):
                        events.append(("move", distance, speed, acceleration))

                        async def movement():
                            nonlocal active
                            active = True
                            started.set()
                            try:
                                await finish.wait()
                            finally:
                                await asyncio.sleep(0)
                                active = False
                                events.append("settled")
                        return asyncio.create_task(movement())

                    def base_vel(self, x, r):
                        assert not active, "zero velocity before movement sender settled"
                        if model_trial:
                            assert poll_started.is_set(), "poll never ran during trial"
                        if outcome in ("slow_http", "poll_stall"):
                            assert not http_done.is_set(), "local stop waited for HTTP"
                        events.append(("zero", x, r))
                        release_http.set()

                    async def look_at_person(self, uid, head):
                        events.append(("head", uid, head))
                        tracking.set()
                        if outcome == "head":
                            raise OSError("head failed")

                    async def next_frame(self, timeout):
                        readers.add(asyncio.current_task())
                        await started.wait()
                        if outcome == "collector":
                            raise ConnectionAbortedError("perception failed")
                        packet = await packets.get()
                        await asyncio.sleep(0.001)
                        return packet

                    async def next_locomotion(self, timeout):
                        await asyncio.Event().wait()

                model_trial = outcome in ("model_stall", "poll_stall")
                trial = SingleTrial("llm" if model_trial else "rules",
                    wait_timeout_s=0.15 if model_trial else 0.01 if outcome in ("timeout", "slow_http") else 30)

                def send(observation):
                    if outcome == "slow_http":
                        release_http.wait(1)
                        http_done.set()
                    if outcome == "transport":
                        raise TransportError("offline")
                    if outcome == "rejected":
                        return ObservationResponse(503, {"accepted": False})
                    state_id = "session-a:1"
                    return ObservationResponse(200, {
                        "accepted": True, "processing_status": "complete",
                        "timestamp": observation["timestamp"],
                        "social_state": {
                            "state_id": state_id, "session_id": "session-a",
                            "robot_timestamp_us": observation["timestamp"],
                            "people": [{"visibility": "OBSERVED", "gaze_state": "SUSTAINED", "evidence": {
                                "latest_distance_valid": True, "gaze_valid": True}}]},
                        "policy_decision": {
                            "decision_id": f"{state_id}:social-rules-v2", "source_state_id": state_id,
                            "session_id": "session-a", "policy_version": "social-rules-v2",
                            "decision": "CONTINUE",
                            "reason_code": "TEST", "target_uid": None, "target_track_epoch": None} if outcome == "decision" else None,
                        "final_decision": ({"action": "CONTINUE", "reason": "Keep going."}
                                           if outcome == "decision" else None),
                    })

                pending = None

                def send_trial(observation, capture, identity):
                    nonlocal pending
                    response = send(observation)
                    if pending is None:
                        pending = {**trial.model_identity(), "request_id": "request",
                            "source_state_id": "session-a:1", "source_robot_timestamp_us": observation["timestamp"],
                            "status": "pending", "decision": None}
                    return ObservationResponse(200, {**response.payload,
                        "model_trial": {"image_ready": False, "result": pending}})

                def poll(identity):
                    poll_started.set()
                    if outcome == "poll_stall":
                        release_http.wait(1)
                        http_done.set()
                    return ObservationResponse(200, {"accepted": True, "result": pending})

                args = parse_args(["--decision-dry-run", "--single-trial", "--route-trial",
                                   "--route-distance", "0.5", "--minimum-send-interval", "0"])
                args.single_trial_policy = trial.policy
                with patch("robot.navel_client.main.SingleTrial", return_value=trial), \
                        patch.object(BehaviourDispatcher, "dispatch", side_effect=AssertionError("dry-run dispatched behaviour")), \
                        patch.object(ObservationTransport, "open_model_trial", return_value=ObservationResponse(200,
                            {"accepted": True, "trial_id": trial.trial_id, "session_id": "session-a", "policy": trial.policy})), \
                        patch.object(ObservationTransport, "send_trial_observation", side_effect=send_trial), \
                        patch.object(ObservationTransport, "poll_model_trial", side_effect=poll), \
                        patch.object(ObservationTransport, "close_model_trial", return_value=ObservationResponse(200, {"accepted": True})), \
                        patch.object(ObservationTransport, "send", side_effect=send), \
                        contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(collect_and_stream(Robot(), args))
                    await asyncio.wait_for(started.wait(), 1)
                    if outcome == "finished":
                        await asyncio.wait_for(tracking.wait(), 1)
                        finish.set()
                    elif outcome == "interrupt":
                        task.cancel()
                    if outcome in ("collector", "head", "interrupt"):
                        with self.assertRaises(asyncio.CancelledError if outcome == "interrupt" else OSError):
                            await asyncio.wait_for(task, 1)
                    else:
                        self.assertIs(await asyncio.wait_for(task, 1), trial)
                self.assertEqual([e for e in events if isinstance(e, tuple) and e[0] == "move"],
                                 [("move", 0.5, 0.1, 0.2)])
                self.assertEqual(events[-2:], ["settled", ("zero", 0.0, 0.0)])
                self.assertEqual(len(readers), 1)
                if outcome == "decision":
                    self.assertEqual(trial.phase, "DECIDED")
                    self.assertEqual(dict(trial.decision), {"action": "CONTINUE", "reason": "Keep going."})
                else:
                    self.assertEqual(trial.phase, "FAILED")
                    if outcome == "finished":
                        self.assertEqual(trial.failure_reason, "ROUTE_FINISHED_WITHOUT_DECISION")
                if outcome not in ("collector", "interrupt"):
                    self.assertIn(("head", 17, 1.0), events)

    async def test_behaviour_dispatch_calls_selected_handler_once_and_freezes_lifecycle(self):
        for action in HANDLERS:
            with self.subTest(action=action):
                observation = frame()
                trial = SingleTrial(monotonic_us=lambda: observation["timestamp"])
                payload = response_for(observation, 1, action, action in ("APPROACH", "ENGAGE"))
                payload["final_decision"] = {"action": action, "reason": "Test decision."}
                self.assertTrue(trial.accept_rule_response(payload, observation))
                self.assertFalse(trial.accept_rule_response(payload, observation))
                calls, zeros = [], []
                started, finish = asyncio.Event(), asyncio.Event()
                robot = NS(base_vel=lambda x, r: zeros.append(trial.phase))

                async def selected(context):
                    calls.append(context.decision["action"])
                    self.assertEqual(trial.phase, "EXECUTING")
                    self.assertIs(context.robot, robot)
                    self.assertIs(context.decision, trial.decision)
                    self.assertIs(context.source, trial.source)
                    self.assertEqual(context.current_observation()["people"][0]["uid"], 18)
                    started.set()
                    await finish.wait()

                async def wrong(context):
                    self.fail("Dispatcher called another action")

                # Current UID can change; the frozen policy source stays UID 17.
                trial.note_observation({**observation, "people": [{**observation["people"][0], "uid": 18}]})
                dispatcher = BehaviourDispatcher(trial, robot,
                    handlers={key: selected if key == action else wrong for key in HANDLERS})
                execution = asyncio.create_task(dispatcher.dispatch())
                await asyncio.wait_for(started.wait(), 1)
                self.assertFalse(await dispatcher.dispatch())
                self.assertEqual(trial.phase, "EXECUTING")
                self.assertEqual(zeros, [])
                finish.set()
                self.assertTrue(await asyncio.wait_for(execution, 1))
                self.assertEqual((trial.phase, calls, zeros), ("COMPLETED", [action], ["EXECUTING"]))
                self.assertFalse(await dispatcher.dispatch())

    async def test_execution_preflight_and_current_person_fail_honestly(self):
        args = parse_args(["--single-trial", "--single-trial-execute", "--route-trial"])
        # No SDK call (including baseline movement) is possible before preflight.
        result = await collect_and_stream(object(), args)
        self.assertEqual((result.phase, result.failure_reason), ("FAILED", "BEHAVIOUR_NOT_IMPLEMENTED"))
        observation = frame()
        for scene in ("missing_handler", "absent", "ambiguous", "stale"):
            with self.subTest(scene=scene):
                trial = SingleTrial(monotonic_us=lambda: observation["timestamp"])
                payload = response_for(observation, 1, "ENGAGE")
                payload["final_decision"] = {"action": "ENGAGE", "reason": "Test decision."}
                self.assertTrue(trial.accept_rule_response(payload, observation))
                if scene != "missing_handler":
                    current = {**observation, "people": [] if scene == "absent" else observation["people"] * 2}
                    if scene == "stale":
                        current = {**observation, "timestamp": 0}
                        trial.monotonic_us = lambda: 2_000_000
                    trial.note_observation(current)

                async def unexpected(context):
                    self.fail("Unavailable execution must not call a handler")

                dispatcher = BehaviourDispatcher(trial, NS(base_vel=lambda x, r: None),
                    handlers={} if scene == "missing_handler" else {"ENGAGE": unexpected})
                self.assertFalse(await dispatcher.dispatch())
                self.assertEqual(trial.failure_reason, "BEHAVIOUR_NOT_IMPLEMENTED" if scene == "missing_handler"
                                 else "CURRENT_PERSON_UNAVAILABLE")
                self.assertIsNotNone(trial.decision)
                self.assertEqual(trial.phase, "FAILED")

    async def test_behaviour_handoff_and_failure_cleanup_on_shared_client(self):
        for outcome, action in (("success", "CONTINUE"), ("success", "ENGAGE"),
                                ("error", "APPROACH"), ("timeout", "YIELD"),
                                ("cancel", "ENGAGE"), ("collector", "CONTINUE")):
            with self.subTest(outcome=outcome, action=action):
                events, readers = [], set()
                baseline_started, handler_started = asyncio.Event(), asyncio.Event()
                finish_route, finish_handler = asyncio.Event(), asyncio.Event()
                active_route = active_behaviour = False
                reads = 0
                clock = [0.0]
                trial = SingleTrial(monotonic=lambda: clock[0])

                class Robot:
                    def move_base(self, distance, *, speed, acceleration):
                        events.append("baseline_start")

                        async def sender():
                            nonlocal active_route
                            active_route = True
                            baseline_started.set()
                            try:
                                await finish_route.wait()
                            finally:
                                await asyncio.sleep(0)
                                active_route = False
                                events.append("baseline_settled")
                        return asyncio.create_task(sender())

                    def base_vel(self, x, r):
                        assert not active_route and not active_behaviour, "zero before senders settled"
                        events.append(("zero", trial.phase))

                    async def look_at_person(self, uid, head):
                        events.append("baseline_head")

                    async def next_frame(self, timeout):
                        nonlocal reads
                        readers.add(asyncio.current_task())
                        await baseline_started.wait()
                        if reads:
                            await handler_started.wait()
                            await asyncio.sleep(0.005)
                            if outcome == "collector":
                                raise ConnectionAbortedError("collector failed during execution")
                        reads += 1
                        return perception(person(17 if reads == 1 else 18))

                    async def next_locomotion(self, timeout):
                        await asyncio.Event().wait()

                robot = Robot()

                async def handler(context):
                    nonlocal active_behaviour
                    self.assertIs(context.robot, robot)
                    self.assertEqual(trial.phase, "EXECUTING")
                    events.append("handler_start")
                    clock[0] = 100.0  # Decision deadline cannot expire execution.
                    trial.tick()
                    self.assertEqual(trial.phase, "EXECUTING")
                    if action == "CONTINUE":
                        self.assertFalse(context.route.stopped)
                        self.assertFalse(context.head.suspended)
                        handler_started.set()
                        await context.route.task
                    else:
                        self.assertTrue(context.route.stopped)
                        self.assertTrue(context.route.task.done())
                        self.assertTrue(context.head.suspended)
                        self.assertLess(events.index("baseline_settled"), events.index("handler_start"))

                        async def owned_sender():
                            nonlocal active_behaviour
                            active_behaviour = True
                            try:
                                await finish_handler.wait()
                            finally:
                                await asyncio.sleep(0)
                                active_behaviour = False
                                events.append("behaviour_settled")
                        sender = context.own_task(owned_sender())
                        await asyncio.sleep(0)
                        handler_started.set()
                        if outcome == "error":
                            raise RuntimeError("mock handler failed")
                        await sender
                    events.append("handler_finish")

                def send(observation):
                    payload = response_for(observation, 1, action, action in ("APPROACH", "ENGAGE"))
                    payload["final_decision"] = {"action": action, "reason": "Test decision."}
                    return ObservationResponse(200, payload)

                args = parse_args(["--single-trial", "--single-trial-execute", "--route-trial",
                                   "--behaviour-timeout", "0.04", "--minimum-send-interval", "0"])
                with patch.dict(HANDLERS, {action: handler}), \
                        patch("robot.navel_client.main.SingleTrial", return_value=trial), \
                        patch.object(ObservationTransport, "send", side_effect=send), \
                        contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(collect_and_stream(robot, args))
                    await asyncio.wait_for(handler_started.wait(), 1)
                    if outcome == "success":
                        await asyncio.sleep(0.015)
                        self.assertGreater(reads, 1)
                        (finish_route if action == "CONTINUE" else finish_handler).set()
                    elif outcome == "cancel":
                        task.cancel()
                    if outcome in ("cancel", "collector"):
                        with self.assertRaises(asyncio.CancelledError if outcome == "cancel" else OSError):
                            await asyncio.wait_for(task, 1)
                    else:
                        self.assertIs(await asyncio.wait_for(task, 1), trial)
                self.assertEqual(events.count("baseline_start"), 1)
                self.assertEqual(events.count("handler_start"), 1)
                self.assertEqual(len(readers), 1)
                self.assertEqual(events[-1][0], "zero")
                self.assertEqual(trial.phase, "COMPLETED" if outcome == "success" else "FAILED")
                if outcome == "timeout":
                    self.assertEqual(trial.failure_reason, "BEHAVIOUR_TIMEOUT")
                if outcome == "error":
                    self.assertEqual(trial.failure_reason, "BEHAVIOUR_FAILED")



class TransportTests(unittest.TestCase):
    def test_invalid_urls_and_timeout_values_are_rejected(self):
        for url in ["file:///tmp/data", "http://", "http://localhost/api", "http://localhost?x=1"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                ObservationTransport(url)
        for timeout in [0, -1, float("nan"), float("inf")]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ObservationTransport("http://localhost", timeout_seconds=timeout)

    def test_nonfinite_json_is_not_sent(self):
        with self.assertRaisesRegex(TransportError, "finite JSON"):
            ObservationTransport("http://localhost").send({"timestamp": float("nan")})

    def test_malformed_server_responses_are_reported(self):
        for body in [b"not-json", b"[]", b"\\xff"]:
            with self.subTest(body=body), self.assertRaises(TransportError):
                ObservationTransport._response(200, body)

    def test_url_failure_is_reported(self):
        transport = ObservationTransport("http://localhost")
        with patch.object(transport._opener, "open", side_effect=TimeoutError):
            with self.assertRaises(TransportError):
                transport.send(frame())

    def test_robot_imports_do_not_need_server_packages_or_sdk(self):
        # An isolated process blocks server/SDK imports, avoiding module-cache effects.
        code = """
import sys
class Blocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'navel', 'flask', 'pydantic', 'openai', 'dotenv', 'app'}:
            raise AssertionError('robot import reached forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Blocker())
import robot.navel_client.main
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonfinite_cli_settings_are_rejected(self):
        for option in ["--request-timeout", "--max-locomotion-age", "--minimum-send-interval", "--decision-wait-timeout", "--behaviour-timeout"]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args([option, "nan"])

    def test_route_requires_explicit_opt_in_and_valid_settings(self):
        self.assertFalse(parse_args(["--decision-dry-run", "--single-trial"]).route_trial)
        for argv in (["--route-trial"], ["--single-trial", "--route-trial"],
                     ["--route-speed", "1.7"], ["--route-acceleration", "1.3"],
                     ["--route-distance", "0"], ["--route-speed", "nan"],
                     ["--route-acceleration", "inf"], ["--single-trial-execute"],
                     ["--single-trial", "--single-trial-execute", "--decision-dry-run"],
                     ["--single-trial", "--single-trial-execute", "--command-dry-run"],
                     ["--single-trial", "--single-trial-execute", "--physical-executor"],
                     ["--behaviour-timeout", "0"], ["--behaviour-timeout", "3601"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(argv)


if __name__ == "__main__":
    unittest.main()
