"""Async collection, latest-frame delivery, failures, and dependency boundary."""

import asyncio
import contextlib
import io
import json
import math
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from robot.navel_client.main import (
    LatestLocomotion, _collect_perception, _replace_queued, _send_observations,
    _execute_trial, collect_and_stream, parse_args,
)
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.transport import ObservationResponse, ObservationTransport, TransportError
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.behaviour_dispatch import BehaviourContext, BehaviourDispatcher, BehaviourNotImplemented, HANDLERS
from robot.navel_client.approach import (
    ApproachConfig, ApproachRuntime, approach_human, arc_plan,
    associate, body_point, nose_position, read_pose, sample_target, world_point,
)
from robot.navel_client.straight_route import StraightRoute
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
        with self.assertRaisesRegex(ValueError, "base_vel"):
            await collect_and_stream(object(), args)
        robot = NS(base_vel=lambda x, r: self.fail("preflight moved the base"),
                   move_base=lambda *a, **kw: self.fail("preflight started the route"))
        with self.assertRaisesRegex(ValueError, "robot.say"):
            await collect_and_stream(robot, args)
        args.route_trial = False
        with self.assertRaisesRegex(ValueError, "--route-trial"):
            await collect_and_stream(robot, args)
        observation = frame()
        for scene in ("missing_handler", "absent", "ambiguous", "stale"):
            with self.subTest(scene=scene):
                trial = SingleTrial(monotonic_us=lambda: observation["timestamp"])
                action = "YIELD" if scene == "missing_handler" else "ENGAGE"
                payload = response_for(observation, 1, action, action in ('APPROACH', 'ENGAGE'))
                payload["final_decision"] = {"action": action, "reason": "Test decision."}
                self.assertTrue(trial.accept_rule_response(payload, observation))
                if scene != "missing_handler":
                    current = {**observation, "people": [] if scene == "absent" else observation["people"] * 2}
                    if scene == "stale":
                        current = {**observation, "timestamp": 0}
                        trial.monotonic_us = lambda: 2_000_000
                    trial.note_observation(current)

                async def unexpected(context):
                    self.fail("Unavailable execution must not call a handler")

                async def unavailable(context):
                    raise BehaviourNotImplemented("test handler unavailable")

                dispatcher = BehaviourDispatcher(trial, NS(base_vel=lambda x, r: None),
                    handlers={"YIELD": unavailable} if scene == "missing_handler" else {"ENGAGE": unexpected})
                self.assertFalse(await dispatcher.dispatch())
                self.assertEqual(trial.failure_reason, "BEHAVIOUR_NOT_IMPLEMENTED" if scene == "missing_handler"
                                 else "CURRENT_PERSON_UNAVAILABLE")
                self.assertIsNotNone(trial.decision)
                self.assertEqual(trial.phase, "FAILED")

    async def test_behaviour_handoff_and_failure_cleanup_on_shared_client(self):
        for outcome, action in (("success", "CONTINUE"), ("success", "ENGAGE"),
                                ("error", "APPROACH"), ("timeout", "YIELD"),
                                ("cancel", "ENGAGE"), ("collector", "CONTINUE"),
                                ("timeout", "ENGAGE"), ("cancel", "CONTINUE"),
                                ("unsupported", "YIELD"),):
            with self.subTest(outcome=outcome, action=action):
                events, readers = [], set()
                baseline_started, handler_started = asyncio.Event(), asyncio.Event()
                finish_route, finish_handler = asyncio.Event(), asyncio.Event()
                active_route = active_behaviour = False
                reads = 0
                clock = [0.0]
                trial = SingleTrial(monotonic=lambda: clock[0])
                production_handler = HANDLERS[action]

                class Robot:
                    def move_base(self, distance, *, speed, acceleration):
                        events.append("baseline_start")
                        events.append(("move", distance, speed, acceleration))

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

                    def say(self, text):
                        assert not active_route, "speech before baseline settled"
                        events.append(("say", text))

                        async def speech():
                            nonlocal active_behaviour
                            active_behaviour = True
                            try:
                                await finish_handler.wait()
                            finally:
                                await asyncio.sleep(0)
                                active_behaviour = False
                                events.append("behaviour_settled")
                        return asyncio.create_task(speech())

                    def move_and_rotate_base(self, *args, **kwargs):
                        raise AssertionError("unexpected approach motion")

                    def rotate_base(self, *args, **kwargs):
                        raise AssertionError("unexpected heading correction")

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
                        retained_task = context.route.task
                        await production_handler(context)
                        self.assertIs(context.route.task, retained_task)
                        self.assertFalse(context.head.suspended)
                    else:
                        self.assertTrue(context.route.stopped)
                        self.assertTrue(context.route.task.done())
                        self.assertTrue(context.head.suspended)
                        self.assertLess(events.index("baseline_settled"), events.index("handler_start"))

                        if action == "ENGAGE":
                            handler_started.set()
                            await production_handler(context)
                            events.append("handler_finish")
                            return

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
                async def unavailable(context):
                    raise BehaviourNotImplemented("test handler unavailable")

                with patch.dict(HANDLERS, {action: unavailable if outcome == "unsupported" else handler}), \
                        patch("robot.navel_client.main.SingleTrial", return_value=trial), \
                        patch.object(ObservationTransport, "send", side_effect=send), \
                        contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(collect_and_stream(robot, args))
                    if outcome != "unsupported":
                        await asyncio.wait_for(handler_started.wait(), 1)
                    if outcome == "success":
                        await asyncio.sleep(0.015)
                        self.assertEqual(trial.phase, "EXECUTING")
                        self.assertNotIn("handler_finish", events)
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
                self.assertEqual([e for e in events if isinstance(e, tuple) and e[0] == "move"],
                                 [("move", 10.0, 0.1, 0.2)])
                self.assertEqual(events.count("handler_start"), 0 if outcome == "unsupported" else 1)
                self.assertEqual([e for e in events if isinstance(e, tuple) and e[0] == "say"],
                                 [("say", "Hello! Do you need any guidance in the lab?")] if action == "ENGAGE" else [])
                self.assertEqual(len(readers), 1)
                self.assertEqual(events[-1][0], "zero")
                self.assertEqual(trial.phase, "COMPLETED" if outcome == "success" else "FAILED")
                if outcome == "timeout":
                    self.assertEqual(trial.failure_reason, "BEHAVIOUR_TIMEOUT")
                if outcome == "error":
                    self.assertEqual(trial.failure_reason, "BEHAVIOUR_FAILED")
                if outcome == "unsupported":
                    self.assertEqual(trial.failure_reason, "BEHAVIOUR_NOT_IMPLEMENTED")

    async def test_continue_route_completion_race_and_failure(self):
        for outcome in ("accepted", "undecided", "failed", "handler_failed"):
            with self.subTest(outcome=outcome):
                observation = frame()
                trial = SingleTrial(monotonic_us=lambda: observation["timestamp"])
                zeros = []

                async def move(distance, *, speed, acceleration):
                    if outcome in ("failed", "handler_failed"):
                        raise OSError("movement failed")

                robot = NS(move_base=move, base_vel=lambda x, r: zeros.append(trial.phase),
                           say=lambda text: self.fail("CONTINUE must not speak"),
                           move_and_rotate_base=lambda *a, **kw: self.fail("CONTINUE must not approach"),
                           rotate_base=lambda *a, **kw: self.fail("CONTINUE must not rotate"))
                route = StraightRoute(robot, 0.5, 0.1, 0.2)
                route.start()
                # Complete the original route and accept the decision before the
                # supervisor observes either, exercising the completion race.
                await asyncio.wait([route.task])
                if outcome != "undecided":
                    payload = response_for(observation, 1, "CONTINUE", False)
                    payload["final_decision"] = {"action": "CONTINUE", "reason": "Keep going."}
                    self.assertTrue(trial.accept_rule_response(payload, observation))
                dispatcher = BehaviourDispatcher(trial, robot, route=route)
                dispatcher.preflight()
                tasks = [route.task]
                if outcome == "failed":
                    with self.assertRaisesRegex(OSError, "movement failed"):
                        await _execute_trial(trial, dispatcher, tasks, [], route)
                    trial.fail("CLIENT_ERROR")  # collect_and_stream's failure path
                    self.assertFalse(await dispatcher.dispatch())
                elif outcome == "handler_failed":
                    self.assertFalse(await dispatcher.dispatch())
                    self.assertEqual(trial.failure_reason, "BEHAVIOUR_FAILED")
                else:
                    await _execute_trial(trial, dispatcher, tasks, [], route)
                await dispatcher.close()
                if outcome == "accepted":
                    self.assertEqual((trial.phase, zeros), ("COMPLETED", ["EXECUTING"]))
                    self.assertFalse(await dispatcher.dispatch())
                else:
                    self.assertEqual(trial.phase, "FAILED")
                    if outcome == "undecided":
                        self.assertEqual(trial.failure_reason, "ROUTE_FINISHED_WITHOUT_DECISION")
                self.assertEqual(len(zeros), 1)

    async def test_approach_geometry_and_timestamped_uid_association(self):
        for x, y in ((2., 0.), (1.8, .5), (1.8, -.5), (1., .6), (.1, .8)):
            plan = arc_plan(x, y)
            theta = math.radians(plan['angle'])
            gx = plan['distance'] if abs(theta) < 1e-9 else plan['distance']/theta*math.sin(theta)
            gy = 0. if abs(theta) < 1e-9 else plan['distance']/theta*(1-math.cos(theta))
            self.assertAlmostEqual(math.hypot(x-gx, y-gy), .7)
            self.assertAlmostEqual(math.atan2(y-gy, x-gx), theta)
            self.assertEqual(plan['speed'], min(.25, math.radians(70)*plan['distance']/max(abs(theta), 1e-9)))
            self.assertEqual(plan['acceleration'], 1.)
        self.assertIsNone(arc_plan(.75, 0.))
        with self.assertRaises(ValueError):
            arc_plan(5., 0.)
        packet = NS(odometry=NS(position=NS(x=2., y=3.),
                    orientation=NS(x=math.sin(.4), y=math.cos(.4), z=0., w=0.),
                    velocity=NS(linear_x=0., linear_y=.3, angular_z=0.), time=1_000_000))
        pose = read_pose(packet)
        self.assertAlmostEqual(pose['yaw'], .8)
        self.assertEqual(pose['w'], .3)
        p = person(17, g_nose=[NS(sys=3, x=1., y=.2, z=.1)])
        self.assertIsNone(nose_position(person(), ApproachConfig()))
        calibrated = nose_position(p, ApproachConfig(head_x=.1, head_y=-.2, frame_yaw_deg=10.))
        self.assertAlmostEqual(calibrated['x'], .1+math.cos(math.radians(10))-.2*math.sin(math.radians(10)))
        self.assertAlmostEqual(calibrated['y'], -.2+math.sin(math.radians(10))+.2*math.cos(math.radians(10)))
        self.assertIsNone(nose_position(p, ApproachConfig(frame_yaw_deg=90.)))
        target = world_point(nose_position(p, ApproachConfig()), pose)
        x, y = body_point(target, pose)
        self.assertAlmostEqual(x, 1.)
        self.assertAlmostEqual(y, .2)
        rt = ApproachRuntime(BehaviourContext(object(), None, None, lambda: None))
        rt.ingest_locomotion(packet)
        rt.ingest_locomotion(packet)
        self.assertEqual(len(rt.history), 1)
        self.assertIsNone(rt.frame_pose(1_180_001))
        rt.target = dict(target, seen_at=time.monotonic(), frame_seq=0)
        p.uid = 18
        rt.ingest_perception(NS(time=1_000_001, persons=[p]))
        self.assertEqual(rt.target['uid'], 18)
        rt.ingest_perception(NS(time=1_000_001, persons=[p]))
        self.assertEqual(rt.frame_seq, 1)
        previous = rt.target.copy()
        rt.ingest_perception(NS(time=1_000_002, persons=[p, person(19, g_nose=p.g_nose)]))
        self.assertEqual(rt.target, previous)
        self.assertIsNone(associate([target, {**target, 'uid': 20}], target))
        rt.target['seen_at'] -= 2.1
        p.uid = 21
        rt.ingest_perception(NS(time=1_000_003, persons=[p]))
        self.assertEqual(rt.target['uid'], 18)
        packet.odometry.orientation = NS(x=1., y=1., z=1., w=1.)
        with self.assertRaises(ValueError):
            read_pose(packet)
        rt.ingest_locomotion(packet)
        rt.ingest_perception(NS(time=1_000_004, persons=[p]))
        errors = rt.errors.copy()
        for stamp in range(1_000_005, 1_000_015):
            rt.ingest_perception(NS(time=stamp, persons=[p]))
        self.assertEqual(rt.errors, errors)  # Failed stream does not grow recursive diagnostics.
        sampled = NS(target=None, check=lambda: None)

        async def feed_samples():
            for seq, offset in enumerate((0., .5, .01, .02, .03, .04, .05), 1):
                sampled.target = dict(wx=1.+offset, wy=0., frame_seq=seq, seen_at=time.monotonic())
                await asyncio.sleep(.025)

        feeder = asyncio.create_task(feed_samples())
        try:
            median = await sample_target(sampled, timeout=.4)
            self.assertIsNotNone(median)
            self.assertAlmostEqual(median['wx'], 1.03)
            self.assertEqual(median['frame_seq'], 7)  # Outlier prevented earlier acceptance.
        finally:
            await feeder

    async def test_approach_bounded_corrections_and_unsuccessful_results(self):
        target = dict(wx=1.5, wy=.3, uid=18, seen_at=time.monotonic(), frame_seq=1)
        for fresh, status in ((True, 'OUTSIDE_TOLERANCE'), (False, 'APPROACHED_UNVERIFIED')):
            with self.subTest(status=status):
                rt = NS(target=target, cfg=ApproachConfig(),
                        pose=lambda: dict(x=0., y=0., yaw=0.), log=NS(emit=lambda *a, **kw: None))
                with patch('robot.navel_client.approach.sample_target', new=AsyncMock(return_value=target if fresh else None)), \
                        patch('robot.navel_client.approach.run_arc', new=AsyncMock()) as arcs, \
                        patch('robot.navel_client.approach.turn_to_target', new=AsyncMock()) as turns:
                    result = await approach_human(rt)
                self.assertEqual(result.status, status)
                self.assertEqual(arcs.await_count, 2 if fresh else 1)
                self.assertEqual(turns.await_count, 2 if fresh else 1)
                observation = frame()
                trial = SingleTrial(monotonic_us=lambda: observation['timestamp'])
                payload = response_for(observation, 1, 'APPROACH')
                payload['final_decision'] = {'action': 'APPROACH', 'reason': 'Approach.'}
                trial.accept_rule_response(payload, observation)
                robot = NS(base_vel=lambda *a: None, say=lambda text: self.fail('Unverified arrival spoke'))
                dispatcher = BehaviourDispatcher(trial, robot)
                with patch.object(dispatcher.context.approach, 'wait_ready', new=AsyncMock()), \
                        patch.object(dispatcher.context.approach, 'settle', new=AsyncMock()), \
                        patch('robot.navel_client.behaviour_dispatch.approach_human', new=AsyncMock(return_value=result)):
                    self.assertFalse(await dispatcher.dispatch())
                self.assertEqual((trial.phase, trial.failure_reason), ('FAILED', status))
                self.assertEqual(trial.approach_result, vars(result))
                self.assertEqual(trial.decision['action'], 'APPROACH')

    async def test_production_approach_on_shared_readers_and_cancellation(self):
        # Accelerated SDK-shaped motion; geometry follows the reference demo.
        for outcome in ('verified', 'cancel_during_braking', 'stale', 'speech_failure'):
            with self.subTest(outcome=outcome):
                calls, readers = [], {'perception': set(), 'odometry': set()}
                arc_started, braking = asyncio.Event(), asyncio.Event()

                class Robot:
                    x = y = yaw = v = w = 0.
                    active = seq = arcs = 0
                    odometry_on = True

                    async def next_locomotion(self, timeout):
                        readers['odometry'].add(asyncio.current_task())
                        await asyncio.sleep(.015)
                        if not self.odometry_on:
                            raise TimeoutError
                        return NS(odometry=NS(position=NS(x=self.x, y=self.y),
                            orientation=NS(x=math.sin(self.yaw/2), y=math.cos(self.yaw/2), z=0., w=0.),
                            velocity=NS(linear_x=self.v, linear_y=self.w, angular_z=0.),
                            time=int(time.monotonic()*1e6)))

                    async def next_frame(self, timeout):
                        readers['perception'].add(asyncio.current_task())
                        await asyncio.sleep(.04)
                        self.seq += 1
                        dx, dy = 1.4-self.x, .3-self.y
                        c, s = math.cos(self.yaw), math.sin(self.yaw)
                        p = person(100+self.seq % 3, g_nose=[NS(sys=3, x=c*dx+s*dy, y=-s*dx+c*dy, z=.1)])
                        return NS(time=int(time.monotonic()*1e6), persons=[p])

                    async def look_at_person(self, uid, head):
                        calls.append(('head', uid))

                    def send_motion(self, name, distance, angle, speed, acceleration):
                        calls.append((name, distance, angle, speed, acceleration))

                        async def sender():
                            self.active += 1
                            assert self.active == 1, 'overlapping SDK senders'
                            try:
                                if name == 'baseline':
                                    await asyncio.Event().wait()
                                else:
                                    if name == 'arc':
                                        self.arcs += 1
                                        arc_started.set()
                                    duration = abs(distance)/speed if distance else abs(angle)/speed
                                    elapsed = 0.
                                    while elapsed < duration:
                                        dt = min(.02, duration-elapsed)
                                        da = math.radians(angle)*dt/duration*.8
                                        ds = distance*dt/duration
                                        self.x += ds*math.cos(self.yaw+da/2)
                                        self.y += ds*math.sin(self.yaw+da/2)
                                        self.yaw += da
                                        self.v, self.w = ds/dt, da/dt
                                        elapsed += dt
                                        await asyncio.sleep(.005)
                            finally:
                                if name == 'arc':
                                    braking.set()
                                await asyncio.sleep(.08)
                                self.active -= 1
                                self.v = self.w = 0.
                                calls.append((name+'_settled',))
                        return asyncio.create_task(sender())

                    def move_base(self, distance, *, speed, acceleration):
                        return self.send_motion('baseline', distance, 0., speed, acceleration)

                    def move_and_rotate_base(self, distance, angle, *, speed, acceleration):
                        return self.send_motion('arc', distance, angle, speed, acceleration)

                    def rotate_base(self, angle, *, speed, acceleration):
                        return self.send_motion('turn', 0., angle, speed, acceleration)

                    def base_vel(self, x, r):
                        assert self.active == 0, 'zero before sender settled'
                        calls.append(('zero',))

                    def say(self, text):
                        assert self.active == 0
                        calls.append(('say', text))

                        async def speech():
                            before = self.seq
                            await asyncio.sleep(.09)
                            assert self.seq > before, 'sensor collection stopped during speech'
                            if outcome == 'speech_failure':
                                raise OSError('speech failed')
                            calls.append(('speech_finished',))
                        return asyncio.create_task(speech())

                robot = Robot()

                def send(observation):
                    payload = response_for(observation, 1, 'APPROACH')
                    uid = observation['people'][0]['uid']
                    payload['social_state']['people'][0]['uid'] = uid
                    payload['policy_decision']['target_uid'] = uid
                    payload['final_decision'] = {'action': 'APPROACH', 'reason': 'Approach.'}
                    return ObservationResponse(200, payload)

                args = parse_args(['--single-trial', '--single-trial-execute', '--route-trial',
                                   '--minimum-send-interval', '0'])
                with patch.object(ObservationTransport, 'send', side_effect=send), \
                        contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(collect_and_stream(robot, args))
                    await asyncio.wait_for(arc_started.wait(), 3)
                    if outcome == 'cancel_during_braking':
                        await asyncio.wait_for(braking.wait(), 3)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 3)
                    else:
                        if outcome == 'stale':
                            robot.odometry_on = False
                        trial = await asyncio.wait_for(task, 8)
                        self.assertEqual(trial.phase, 'COMPLETED' if outcome == 'verified' else 'FAILED')
                        if outcome != 'stale':
                            self.assertEqual(trial.approach_result['status'], 'APPROACHED_VERIFIED')
                            self.assertLessEqual(abs(trial.approach_result['distance_m']-.7), .1)
                            self.assertLessEqual(abs(trial.approach_result['heading_error_deg']), 4.)
                self.assertEqual(robot.active, 0)
                self.assertEqual([len(value) for value in readers.values()], [1, 1])
                self.assertEqual(sum(c[0] == 'baseline' for c in calls), 1)
                self.assertLess(calls.index(('baseline_settled',)), next(i for i, c in enumerate(calls) if c[0] == 'arc'))
                self.assertEqual([c for c in calls if c[0] == 'say'],
                                 [('say', 'Approach complete!')] if outcome in ('verified', 'speech_failure') else [])
                self.assertLessEqual(sum(c[0] == 'arc' for c in calls), 2)
                self.assertLessEqual(sum(c[0] == 'turn' for c in calls), 2)
                for call in calls:
                    if call[0] == 'arc':
                        self.assertEqual(call[3], min(.25, math.radians(70)*call[1]/max(abs(math.radians(call[2])), 1e-9)))
                        self.assertEqual(call[4], 1.)
                    if call[0] == 'turn':
                        self.assertEqual(call[3], min(70., math.sqrt(abs(call[2])*60.)))
                        self.assertEqual(call[4], 60.)
                self.assertEqual(calls[-1], ('zero',))

    async def test_production_yield_sequence_and_failure_cleanup(self):
        expected = [('baseline', 10., .1, .2), ('rotate', 100., 30., 35.),
                    ('move', -.6, .25, .35), ('move', .6, .12, .15),
                    ('rotate', -100., 30., 35.), ('move', .15, .25, .35)]
        for outcome in ('success', 'cancel_escape', 'cancel_wait', 'cancel_return',
                        'motion_failure', 'speech_failure', 'final_speech_failure', 'timeout'):
            with self.subTest(outcome=outcome):
                events, sdk_tasks = [], []
                readers = {'perception': set(), 'odometry': set()}
                reached = {name: asyncio.Event() for name in ('escape', 'wait', 'return')}
                baseline_started = asyncio.Event()
                trial = SingleTrial()

                class Robot:
                    active = reads = 0

                    async def next_locomotion(self, timeout):
                        readers['odometry'].add(asyncio.current_task())
                        await asyncio.sleep(.01)
                        return NS(odometry=NS(position=NS(x=0., y=0.),
                            orientation=NS(x=0., y=1., z=0., w=0.),
                            velocity=NS(linear_x=.1 if self.active else 0., linear_y=0., angular_z=0.),
                            time=int(time.monotonic()*1e6)))

                    async def next_frame(self, timeout):
                        readers['perception'].add(asyncio.current_task())
                        await baseline_started.wait()
                        await asyncio.sleep(.01)
                        self.reads += 1
                        # No usable nose and no person after acceptance. YIELD
                        # must not acquire or retain the decision source UID.
                        return perception(person(17)) if trial.phase == 'OBSERVING' else perception()

                    async def look_at_person(self, uid, head):
                        events.append(('head', uid))

                    def movement(self, name, amount, speed, acceleration):
                        assert self.active == 0, 'overlapping movement senders'
                        events.append((name, amount, speed, acceleration))

                        async def sender():
                            self.active += 1
                            try:
                                if name == 'baseline':
                                    baseline_started.set()
                                    await asyncio.Event().wait()
                                stage = 'escape' if amount == -.6 else 'return' if amount == .6 else None
                                if stage:
                                    reached[stage].set()
                                if (stage and outcome == 'cancel_'+stage) or (stage == 'escape' and outcome == 'timeout'):
                                    await asyncio.Event().wait()
                                await asyncio.sleep(.01)
                                if stage == 'escape' and outcome == 'motion_failure':
                                    raise OSError('escape failed')
                            finally:
                                await asyncio.sleep(.03)
                                self.active -= 1
                                events.append(('settled', name, amount))
                        task = asyncio.create_task(sender())
                        sdk_tasks.append(task)
                        return task

                    def move_base(self, distance, *, speed, acceleration):
                        return self.movement('baseline' if distance == 10. else 'move', distance, speed, acceleration)

                    def rotate_base(self, angle, *, speed, acceleration):
                        return self.movement('rotate', angle, speed, acceleration)

                    def move_and_rotate_base(self, *args, **kwargs):
                        raise AssertionError('YIELD must not approach')

                    def base_vel(self, x, r):
                        assert self.active == 0, 'zero before sender settled'
                        events.append(('zero',))

                    def say(self, text):
                        assert self.active == 0
                        assert events[-1][0] == 'margin', 'speech before stop/margin finished'
                        events.append(('say', text))

                        async def speech():
                            before = self.reads
                            await asyncio.sleep(.03)
                            assert self.reads > before, 'collectors stopped during speech'
                            if (outcome == 'speech_failure' and text == 'Please go ahead.') or (
                                    outcome == 'final_speech_failure' and text == 'Yielding complete!'):
                                raise OSError('speech failed')
                            events.append(('speech_finished', text))
                        task = asyncio.create_task(speech())
                        sdk_tasks.append(task)
                        return task

                async def timed_sleep(seconds):
                    if seconds == 3.:
                        self.assertEqual(events[-1], ('speech_finished', 'Please go ahead.'))
                        reached['wait'].set()
                        events.append(('wait', seconds))
                        if outcome == 'cancel_wait':
                            await asyncio.Event().wait()
                    else:
                        self.assertEqual(events[-1], ('zero',))
                        events.append(('margin', seconds))
                    await asyncio.sleep(.005)

                def send(observation):
                    payload = response_for(observation, 1, 'YIELD', False)
                    payload['final_decision'] = {'action': 'YIELD', 'reason': 'Give room.'}
                    return ObservationResponse(200, payload)

                robot = Robot()
                yield_asyncio = NS(**vars(asyncio))
                yield_asyncio.sleep = timed_sleep  # Keep real sensor/motion polling intact.
                args = parse_args(['--single-trial', '--single-trial-execute', '--route-trial',
                                   '--minimum-send-interval', '0', '--behaviour-timeout',
                                   '1' if outcome == 'timeout' else '120'])
                with patch('robot.navel_client.behaviour_dispatch.asyncio', yield_asyncio), \
                        patch('robot.navel_client.main.SingleTrial', return_value=trial), \
                        patch.object(ObservationTransport, 'send', side_effect=send), \
                        patch.object(ApproachRuntime, 'detect', side_effect=AssertionError('YIELD acquired a target')), \
                        contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(collect_and_stream(robot, args))
                    if outcome.startswith('cancel_'):
                        await asyncio.wait_for(reached[outcome.removeprefix('cancel_')].wait(), 3)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 3)
                    else:
                        self.assertIs(await asyncio.wait_for(task, 5), trial)
                self.assertEqual(trial.phase, 'COMPLETED' if outcome == 'success' else 'FAILED')
                if outcome == 'timeout':
                    self.assertEqual(trial.failure_reason, 'BEHAVIOUR_TIMEOUT')
                self.assertTrue(all(task.done() and task.cancelling() <= 1 for task in sdk_tasks))
                self.assertEqual(robot.active, 0)
                self.assertEqual([len(value) for value in readers.values()], [1, 1])
                motions = [e for e in events if e[0] in ('baseline', 'rotate', 'move')]
                expected_count = 6 if outcome in ('success', 'final_speech_failure') else 4 if outcome == 'cancel_return' else 3
                self.assertEqual(motions, expected[:expected_count])
                speeches = [e[1] for e in events if e[0] == 'say']
                expected_speech = (['Please go ahead.', 'Yielding complete!'] if expected_count == 6
                                   else ['Please go ahead.'] if outcome in ('cancel_wait', 'cancel_return', 'speech_failure') else [])
                self.assertEqual(speeches, expected_speech)
                self.assertEqual(sum(e[0] == 'wait' for e in events),
                                 1 if outcome in ('success', 'cancel_wait', 'cancel_return', 'final_speech_failure') else 0)
                if outcome == 'success':
                    self.assertEqual([e[1] for e in events if e[0] == 'margin'], [.5, .3, .3, .5, .04])
                    self.assertIn(('speech_finished', 'Yielding complete!'), events)
                baseline_end = events.index(('settled', 'baseline', 10.))
                self.assertFalse(any(e[0] == 'head' for e in events[baseline_end:]))
                self.assertEqual(events[-1], ('zero',))



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
