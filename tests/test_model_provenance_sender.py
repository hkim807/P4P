"""Opt-in provenance stays paired with raw frames without changing dispatch."""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.main import (
    LatestLocomotion, ModelObservation, _collect_perception, _replace_queued,
    _send_observations, collect_and_stream, parse_args,
)
from robot.navel_client.sdk_capture import SdkCapture
from robot.navel_client.transport import ObservationResponse, ObservationTransport
from tests.fixtures import frame, perception, person


def capture_record(sequence, timestamp, *, sdk_time=None):
    return {
        "capture_version": 1, "session_id": "capture-session", "stream": "perception",
        "sequence": sequence, "received_monotonic_us": timestamp - 1,
        "received_unix_us": 1_790_000_000_000_000 + timestamp,
        "packet": {"time": sdk_time if sdk_time is not None else sequence * 1_000, "persons": []},
    }


class SenderConfigurationTests(unittest.TestCase):
    def test_default_is_disabled_and_explicit_capture_prerequisites_are_required(self):
        self.assertFalse(parse_args([]).model_provenance)
        flags = ["--model-provenance", "--sdk-capture", "--camera-capture"]
        args = parse_args(flags)
        self.assertTrue(args.model_provenance)
        self.assertTrue(args.sdk_capture)
        self.assertTrue(args.camera_capture)
        for invalid in (["--model-provenance"], ["--model-provenance", "--sdk-capture"],
                        ["--model-provenance", "--camera-capture"],
                        [*flags, "--sdk-capture-only"], [*flags, "--print-only"]):
            with self.subTest(flags=invalid), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse_args(invalid)
                self.assertEqual(error.exception.code, 2)

    def test_plain_send_contract_is_unchanged_and_opt_in_envelope_is_exact(self):
        transport = ObservationTransport("http://127.0.0.1:6060")
        observation = frame()
        source = capture_record(1, observation["timestamp"])
        original = deepcopy(observation)

        class Reply:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"accepted":true}'

        with patch.object(transport._opener, "open", return_value=Reply()) as post:
            transport.send(observation)
            raw_request = post.call_args.args[0]
            self.assertEqual(json.loads(raw_request.data), observation)
            transport.send_model_observation(observation, source)
            model_request = post.call_args.args[0]
        self.assertEqual(model_request.full_url, raw_request.full_url)
        self.assertEqual(model_request.full_url, "http://127.0.0.1:6060/api/v1/observations")
        self.assertEqual(json.loads(model_request.data), {
            "observation": observation,
            "model_source": {"version": 1, "clock": "robot-host-monotonic-us", "capture": source},
        })
        self.assertEqual(observation, original)

    def test_capture_returns_a_full_detached_snapshot(self):
        incoming = perception(person(uid=17))
        incoming.time = 123456
        capture = SdkCapture(NS())
        with patch("robot.navel_client.sdk_capture.time.monotonic_ns", return_value=200_000):
            with patch("robot.navel_client.sdk_capture.time.time_ns", return_value=3_000_000):
                returned = capture.record("perception", incoming)
        queued = capture.queue.get_nowait()
        capture.queue.task_done()
        self.assertEqual(returned, queued)
        self.assertEqual(set(returned), {"capture_version", "session_id", "stream", "sequence",
                                         "received_monotonic_us", "received_unix_us", "packet"})
        self.assertEqual(returned["session_id"], capture.session_id)
        self.assertEqual(returned["received_monotonic_us"], 200)
        self.assertEqual(returned["received_unix_us"], 3000)
        self.assertEqual(returned["packet"]["time"], 123456)
        incoming.persons[0].uid = 99
        returned["packet"]["persons"][0]["uid"] = 88
        returned["sequence"] = 10
        self.assertEqual(queued["packet"]["persons"][0]["uid"], 17)
        self.assertEqual(queued["sequence"], 1)
        self.assertEqual(capture.sequences["perception"], 1)


class ModelProvenanceSenderTests(unittest.IsolatedAsyncioTestCase):
    async def collect_two(self, *, model_provenance):
        ready = asyncio.Event()
        received = [perception(person(uid=index)) for index in (11, 22)]
        for index, packet in enumerate(received, 1):
            packet.time = index * 123456

        class Robot:
            async def next_frame(self, timeout):
                if received:
                    return received.pop(0)
                ready.set()
                await asyncio.Event().wait()

        capture = SdkCapture(NS())
        queue = asyncio.Queue(maxsize=1)
        adapter_clock = iter((150_000, 250_000))
        sdk_clock = iter((100_000, 200_000))
        adapter = NavelObservationAdapter(monotonic_ns=lambda: next(adapter_clock))
        with patch("robot.navel_client.sdk_capture.time.monotonic_ns", side_effect=lambda: next(sdk_clock)):
            task = asyncio.create_task(_collect_perception(
                Robot(), adapter, LatestLocomotion(), queue, max_locomotion_age_s=1,
                sdk_capture=capture, model_provenance=model_provenance,
            ))
            try:
                await asyncio.wait_for(ready.wait(), 1)
                latest = queue.get_nowait()
                queue.task_done()
                await asyncio.wait_for(queue.join(), 1)
                records = []
                while not capture.queue.empty():
                    records.append(capture.queue.get_nowait())
                    capture.queue.task_done()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return latest, records

    async def test_collector_keeps_default_raw_shape_even_with_sdk_capture_enabled(self):
        latest, records = await self.collect_two(model_provenance=False)
        self.assertIsInstance(latest, dict)
        self.assertEqual(set(latest), {"timestamp", "people", "robot", "safety"})
        self.assertEqual(latest["timestamp"], 250)
        self.assertEqual(latest["people"][0]["uid"], 22)
        self.assertEqual([record["sequence"] for record in records], [1, 2])

    async def test_collector_pairs_latest_raw_with_its_exact_full_capture_record(self):
        latest, records = await self.collect_two(model_provenance=True)
        self.assertIsInstance(latest, ModelObservation)
        self.assertEqual(latest.capture, records[-1])
        self.assertEqual(latest.capture["sequence"], 2)
        self.assertEqual(latest.capture["packet"]["time"], 246912)
        self.assertEqual(latest.capture["packet"]["persons"][0]["uid"], 22)
        self.assertEqual(latest.observation["people"][0]["uid"], 22)
        self.assertEqual(latest.observation["timestamp"], 250)
        self.assertLessEqual(latest.capture["received_monotonic_us"], latest.observation["timestamp"])
        self.assertNotIn("model_source", latest.observation)

    async def test_provenance_collection_without_capture_fails_before_robot_read(self):
        robot = NS(next_frame=lambda **kwargs: self.fail("capture is a prerequisite"))
        with self.assertRaisesRegex(ValueError, "requires SDK capture"):
            await _collect_perception(robot, NavelObservationAdapter(), LatestLocomotion(),
                                      asyncio.Queue(maxsize=1), max_locomotion_age_s=1,
                                      model_provenance=True)

    async def test_latest_replacement_preserves_raw_and_capture_pair_during_slow_send(self):
        queue = asyncio.Queue(maxsize=1)
        started = threading.Event()
        release = threading.Event()
        sent = []
        owner = self

        class Transport:
            def send(self, observation):
                owner.fail("provenance uses its explicit transport method")

            def send_model_observation(self, observation, capture):
                sent.append((deepcopy(observation), deepcopy(capture)))
                started.set()
                if not release.wait(2):
                    raise AssertionError("synchronisation timed out")
                return ObservationResponse(200, {"accepted": True})

        def paired(index):
            raw = {**frame(), "timestamp": index}
            return ModelObservation(raw, capture_record(index, index))

        queue.put_nowait(paired(1))
        output = io.StringIO()
        with redirect_stdout(output):
            task = asyncio.create_task(_send_observations(
                queue, Transport(), minimum_send_interval_s=0, print_only=False,
            ))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                _replace_queued(queue, paired(2))
                _replace_queued(queue, paired(3))
                release.set()
                await asyncio.wait_for(queue.join(), 2)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual([raw["timestamp"] for raw, _ in sent], [1, 3])
        self.assertEqual([capture["sequence"] for _, capture in sent], [1, 3])
        self.assertEqual([capture["packet"]["time"] for _, capture in sent], [1000, 3000])
        self.assertNotIn("capture_version", output.getvalue())
        self.assertNotIn("model_source", output.getvalue())

    async def test_all_existing_handlers_receive_only_the_unchanged_raw_observation(self):
        raw = frame()
        source = capture_record(1, raw["timestamp"])
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(ModelObservation(raw, source))
        dispatched, commands, physical, locks, feedback = [], [], [], [], []
        payload = {"accepted": True, "target_lock": {"uid": 17},
                   "model_decision": {"action": "ENGAGE", "reason": "Output only."}}

        async def accept_decision(response, observation):
            dispatched.append((response, observation))

        def accept_command(response, observation):
            commands.append((response, observation))
            return [{"event_id": "event-1"}]

        async def accept_physical(response, observation):
            physical.append((response, observation))

        def send_model(observation, capture):
            self.assertIs(observation, raw)
            self.assertEqual(capture, source)
            return ObservationResponse(200, payload)

        def send_event(event):
            feedback.append(event)
            return ObservationResponse(200, {"accepted": True, "event_id": event["event_id"]})

        transport = NS(send_model_observation=send_model, send_event=send_event)
        with patch("robot.navel_client.main.parse_decision") as parse:
            with redirect_stdout(io.StringIO()):
                task = asyncio.create_task(_send_observations(
                    queue, transport, minimum_send_interval_s=0, print_only=False,
                    decision_dispatcher=NS(accept=accept_decision),
                    command_executor=NS(accept=accept_command),
                    physical_executor=NS(accept=accept_physical),
                    head_focus=NS(apply_server_lock=locks.append),
                ))
                try:
                    await asyncio.wait_for(queue.join(), 1)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertIs(parse.call_args.args[1], raw)
        for delivered in (dispatched, commands, physical):
            self.assertEqual(len(delivered), 1)
            self.assertIs(delivered[0][1], raw)
            self.assertNotIn("model_source", delivered[0][1])
        self.assertEqual(locks, [{"uid": 17}])
        self.assertEqual(feedback, [{"event_id": "event-1"}])
        self.assertEqual(raw, frame())

    async def test_print_only_still_prints_raw_and_never_posts(self):
        raw = frame()
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(ModelObservation(raw, capture_record(1, raw["timestamp"])))
        output = io.StringIO()
        with redirect_stdout(output):
            task = asyncio.create_task(_send_observations(
                queue, NS(), minimum_send_interval_s=0, print_only=True,
            ))
            try:
                await asyncio.wait_for(queue.join(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(json.loads(output.getvalue()), raw)

    async def test_sdk_and_camera_captures_share_one_session_when_provenance_enabled(self):
        ready = asyncio.Event()
        captures = []
        camera_sessions = []

        def new_sdk(transport):
            capture = SdkCapture(transport)
            captures.append(capture)
            return capture

        class Camera:
            def __init__(self, transport, *, session_id):
                camera_sessions.append(session_id)
                self.queue = asyncio.Queue()

            async def collect(self, *args):
                ready.set()
                await asyncio.Event().wait()

            async def send(self):
                await asyncio.Event().wait()

        class Robot:
            async def next_frame(self, timeout):
                await asyncio.Event().wait()

            async def next_locomotion(self, timeout):
                await asyncio.Event().wait()

        args = parse_args(["--model-provenance", "--sdk-capture", "--camera-capture"])
        with patch("robot.navel_client.main.SdkCapture", side_effect=new_sdk):
            with patch("robot.navel_client.main.CameraCapture", Camera):
                task = asyncio.create_task(collect_and_stream(Robot(), args))
                try:
                    await asyncio.wait_for(ready.wait(), 1)
                    self.assertEqual(camera_sessions, [captures[0].session_id])
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
