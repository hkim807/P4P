"""Collect Navel sensors concurrently and stream raw frames over HTTP."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Any

from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.camera_capture import CameraCapture
from robot.navel_client.command_dispatch import CommandRejected, FakeCommandExecutor
from robot.navel_client.decision_dispatch import (
    DecisionDispatcher, DecisionRejected, DryRunHandlers, parse_decision,
)
from robot.navel_client.head_focus import HeadFocusController
from robot.navel_client.physical_executor import PhysicalCommandExecutor, ScriptPaths
from robot.navel_client.sdk_capture import SdkCapture
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.straight_route import StraightRoute
from robot.navel_client.transport import ObservationTransport, TransportError


logger = logging.getLogger(__name__)


@dataclass
class LatestLocomotion:
    packet: tuple[Any, float] | None = None

    def fresh_value(self, max_age_s: float) -> Any | None:
        if self.packet is None or time.monotonic() - self.packet[1] > max_age_s:
            return None
        return self.packet[0]


@dataclass(frozen=True)
class ModelObservation:
    """Keep one raw observation paired with its exact captured perception."""

    observation: dict[str, Any]
    capture: dict[str, Any]


QueuedObservation = dict[str, Any] | ModelObservation


def _replace_queued(queue: asyncio.Queue[QueuedObservation], observation: QueuedObservation) -> None:
    if queue.full():
        queue.get_nowait()
        queue.task_done()
    queue.put_nowait(observation)


async def _collect_locomotion(robot: Any, latest: LatestLocomotion,
                              sdk_capture: SdkCapture | None = None) -> None:
    while True:
        try:
            packet = await robot.next_locomotion(timeout=1.0)
        except TimeoutError:
            continue
        if sdk_capture is not None:
            sdk_capture.record("locomotion", packet)
        latest.packet = (packet, time.monotonic())


async def _collect_perception(
    robot: Any,
    adapter: NavelObservationAdapter,
    latest: LatestLocomotion,
    queue: asyncio.Queue[QueuedObservation],
    *,
    max_locomotion_age_s: float,
    head_focus: HeadFocusController | None = None,
    physical_executor: PhysicalCommandExecutor | None = None,
    sdk_capture: SdkCapture | None = None,
    model_provenance: bool = False,
    perception_loss_timeout_s: float | None = None,
) -> None:
    if model_provenance and sdk_capture is None:
        raise ValueError("model provenance requires SDK capture")
    last_frame_at = time.monotonic()
    while True:
        try:
            perception = await robot.next_frame(timeout=1.0)
        except TimeoutError:
            if head_focus is not None:
                head_focus.tick()
            if (perception_loss_timeout_s is not None
                    and time.monotonic() - last_frame_at >= perception_loss_timeout_s):
                raise RuntimeError("No perception frames for 5 seconds")
            continue
        last_frame_at = time.monotonic()
        captured = sdk_capture.record("perception", perception) if sdk_capture is not None else None
        if head_focus is not None and (physical_executor is None or physical_executor.active is None):
            command = head_focus.observe(perception)
            if inspect.isawaitable(command):
                await command
        observation = adapter.convert(perception, latest.fresh_value(max_locomotion_age_s))
        _replace_queued(queue, ModelObservation(observation, captured)
                        if model_provenance else observation)


async def _send_observations(
    queue: asyncio.Queue[QueuedObservation],
    transport: ObservationTransport,
    *,
    minimum_send_interval_s: float,
    print_only: bool,
    decision_dispatcher: DecisionDispatcher | None = None,
    head_focus: HeadFocusController | None = None,
    max_decision_age_s: float = 1.0,
    command_executor: FakeCommandExecutor | None = None,
    physical_executor: PhysicalCommandExecutor | None = None,
    trial: SingleTrial | None = None,
    route_trial: bool = False,
) -> None:
    last_sent_at = -math.inf
    while True:
        queued = await queue.get()
        try:
            delay = minimum_send_interval_s - (time.monotonic() - last_sent_at)
            if delay > 0:
                await asyncio.sleep(delay)
            while not queue.empty():
                queue.task_done()
                queued = queue.get_nowait()
            observation = queued.observation if isinstance(queued, ModelObservation) else queued
            last_sent_at = time.monotonic()
            if print_only:
                print(json.dumps(observation, allow_nan=False, separators=(",", ":")), flush=True)
                continue
            try:
                response = (await asyncio.to_thread(transport.send_model_observation,
                                                     observation, queued.capture)
                            if isinstance(queued, ModelObservation)
                            else await asyncio.to_thread(transport.send, observation))
            except TransportError as error:
                logger.warning("timestamp=%s transport_error=%s", observation["timestamp"], error)
                if route_trial:
                    trial.fail("TRANSPORT_INVALIDATED")
                    return
                if decision_dispatcher is not None:
                    await decision_dispatcher.invalidate("transport_error")
                if physical_executor is not None:
                    await physical_executor.invalidate("transport_error")
                continue
            if 200 <= response.status_code < 300 and response.payload.get("accepted") is True:
                if head_focus is not None and response.payload.get("target_lock") is not None:
                    try:
                        parse_decision(response.payload, observation, time.monotonic_ns() // 1000,
                                       round(max_decision_age_s * 1_000_000))
                    except DecisionRejected as error:
                        logger.warning("head_focus_lock_rejected=%s", error)
                    else:
                        head_focus.apply_server_lock(response.payload["target_lock"])
                print(json.dumps(observation, allow_nan=False, indent=2), flush=True)
                logger.info("timestamp=%s people=%s accepted=true",
                            observation["timestamp"], len(observation["people"]))
                if decision_dispatcher is not None:
                    await decision_dispatcher.accept(response.payload, observation)
                if trial is not None:
                    trial.accept_rule_response(response.payload, observation)
                    if route_trial and (trial.phase == "DECIDED" or trial.terminal):
                        return
                if command_executor is not None:
                    try:
                        events = command_executor.accept(response.payload, observation)
                    except CommandRejected as error:
                        logger.warning("command_rejected=%s", error)
                    else:
                        for event in events:
                            try:
                                feedback = await asyncio.to_thread(transport.send_event, event)
                            except TransportError as error:
                                logger.warning("execution_feedback_transport_error=%s", error)
                                break
                            if not (200 <= feedback.status_code < 300
                                    and feedback.payload.get("accepted") is True
                                    and feedback.payload.get("event_id") == event["event_id"]):
                                logger.warning("execution_feedback_rejected=%s", feedback.payload)
                                break
                if physical_executor is not None:
                    try:
                        await physical_executor.accept(response.payload, observation)
                    except CommandRejected as error:
                        logger.warning("physical_command_rejected=%s", error)
            else:
                logger.warning("timestamp=%s status=%s response=%s",
                               observation["timestamp"], response.status_code, response.payload)
                if route_trial:
                    trial.fail("TRANSPORT_INVALIDATED")
                    return
                if decision_dispatcher is not None:
                    await decision_dispatcher.invalidate("observation_not_accepted")
                if physical_executor is not None:
                    await physical_executor.invalidate("observation_not_accepted")
        finally:
            queue.task_done()


async def _send_execution_events(executor: PhysicalCommandExecutor,
                                 transport: ObservationTransport) -> None:
    """Keep ordered feedback pending through transient HTTP failures."""
    while True:
        event = await executor.event_queue.get()
        try:
            while True:
                try:
                    response = await asyncio.to_thread(transport.send_event, event)
                except TransportError as error:
                    logger.warning("execution_feedback_transport_error=%s", error)
                    await asyncio.sleep(0.25)
                    continue
                if (200 <= response.status_code < 300
                        and response.payload.get("accepted") is True
                        and response.payload.get("event_id") == event["event_id"]):
                    break
                if 500 <= response.status_code < 600:
                    await asyncio.sleep(0.25)
                    continue
                logger.error("execution_feedback_rejected=%s", response.payload)
                await executor.feedback_failed("FEEDBACK_REJECTED")
                break
        finally:
            executor.event_queue.task_done()


async def collect_and_stream(robot: Any, args: argparse.Namespace,
                             camera_types: dict[str, Any] | None = None) -> SingleTrial | None:
    transport = ObservationTransport(args.server, timeout_seconds=args.request_timeout)
    sdk_capture = SdkCapture(transport) if args.sdk_capture else None
    camera_capture = (CameraCapture(transport, session_id=sdk_capture.session_id if sdk_capture else None)
                      if args.camera_capture else None)
    adapter = NavelObservationAdapter()
    queue: asyncio.Queue[QueuedObservation] = asyncio.Queue(maxsize=1)
    latest = LatestLocomotion()
    route = (StraightRoute(robot, args.route_distance, args.route_speed, args.route_acceleration)
             if args.route_trial else None)
    head_focus = (HeadFocusController(robot,
                                     magnitude=1.0 if route else args.head_focus_magnitude,
                                     grace_s=args.head_focus_grace,
                                     command_interval_s=0.6 if route else None,
                                     raise_on_error=route is not None,
                                     select_first_visible=route is not None)
                  if args.head_focus or route else None)
    trial = (SingleTrial(args.single_trial_policy, wait_timeout_s=args.decision_wait_timeout,
                         max_age_s=args.max_decision_age) if args.single_trial else None)
    decision_dispatcher = (DecisionDispatcher(DryRunHandlers(robot),
                           max_age_s=args.max_decision_age,
                           timeout_s=args.decision_timeout)
                           if args.decision_dry_run and trial is None else None)
    command_executor = (FakeCommandExecutor(max_age_s=args.max_decision_age)
                        if args.command_dry_run else None)
    physical_executor = (PhysicalCommandExecutor(
        ScriptPaths(args.approach_script, args.engage_script, args.pause_route_script,
                    args.resume_route_script, args.stop_script),
        max_age_s=args.max_decision_age, response_timeout_s=args.response_timeout,
        hook_timeout_s=args.hook_timeout, stop_timeout_s=args.stop_timeout)
        if args.physical_executor else None)
    collectors = [
        asyncio.create_task(_collect_locomotion(robot, latest, sdk_capture)),
        asyncio.create_task(_collect_perception(
            robot, adapter, latest, queue, max_locomotion_age_s=args.max_locomotion_age,
            head_focus=head_focus, physical_executor=physical_executor,
            sdk_capture=sdk_capture,
            model_provenance=args.model_provenance,
            perception_loss_timeout_s=5.0 if route else None,
        )),
    ]
    if camera_capture is not None:
        collectors.extend(asyncio.create_task(camera_capture.collect(
            name, (camera_types or {}).get(name), args.camera_interval))
            for name in ("head", "chest"))
    tasks = collectors + [
        asyncio.create_task(_send_observations(
            queue, transport,
            minimum_send_interval_s=args.minimum_send_interval,
            print_only=args.print_only,
            decision_dispatcher=decision_dispatcher,
            head_focus=None if route else head_focus,
            max_decision_age_s=args.max_decision_age,
            command_executor=command_executor,
            physical_executor=physical_executor,
            trial=trial,
            route_trial=route is not None,
        )),
    ]
    sdk_sender = asyncio.create_task(sdk_capture.send()) if sdk_capture is not None else None
    if sdk_sender is not None:
        tasks.append(sdk_sender)
    camera_sender = (asyncio.create_task(camera_capture.send())
                     if camera_capture is not None else None)
    if camera_sender is not None:
        tasks.append(camera_sender)
    if decision_dispatcher is not None:
        tasks.append(asyncio.create_task(decision_dispatcher.watchdog()))
    if trial is not None:
        tasks.append(asyncio.create_task(trial.watchdog(stop_on_decision=route is not None)))
    if physical_executor is not None:
        tasks.append(asyncio.create_task(physical_executor.watchdog()))
        tasks.append(asyncio.create_task(_send_execution_events(physical_executor, transport)))
    group = None
    try:
        if route is not None:
            tasks.append(route.start())
        group = asyncio.gather(*tasks)
        if trial is None:
            await asyncio.shield(group)
        else:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if route is not None and trial.phase == "OBSERVING" and route.task in done:
                trial.fail("ROUTE_FINISHED_WITHOUT_DECISION")
    except asyncio.CancelledError:
        if trial is not None:
            trial.fail("INTERRUPTED")
        raise
    except Exception:
        if trial is not None:
            trial.fail("CLIENT_ERROR")
        raise
    finally:
        if trial is not None and not trial.terminal and not (route and trial.phase == "DECIDED"):
            trial.fail("CLIENT_STOPPED")
        for task in collectors:
            task.cancel()
        if route is not None:
            # Stop locally before waiting for head, capture queues or HTTP.
            cleanup = asyncio.create_task(route.stop())
            try:
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
            except Exception:
                trial.fail("ROUTE_STOP_FAILED")
                logger.exception("route_trial stop_failed physical_stop_verified=false")
            head_focus.suspend()
        await asyncio.gather(*collectors, return_exceptions=True)
        if sdk_capture is not None and sdk_sender is not None and not sdk_sender.done():
            try:
                await asyncio.wait_for(sdk_capture.queue.join(), timeout=args.request_timeout)
            except asyncio.TimeoutError:
                logger.warning("sdk_capture_pending_on_shutdown=%s", sdk_capture.queue.qsize())
        if camera_capture is not None and camera_sender is not None and not camera_sender.done():
            try:
                await asyncio.wait_for(camera_capture.queue.join(), timeout=args.request_timeout)
            except asyncio.TimeoutError:
                logger.warning("camera_capture_pending_on_shutdown=%s", camera_capture.queue.qsize())
        if physical_executor is not None:
            await physical_executor.close()
            try:
                await asyncio.wait_for(physical_executor.event_queue.join(), 2.0)
            except asyncio.TimeoutError:
                logger.warning("execution_feedback_pending_on_shutdown=%s",
                               physical_executor.event_queue.qsize())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if group is not None and group.done() and not group.cancelled():
            group.exception()
        if decision_dispatcher is not None:
            await decision_dispatcher.invalidate("client_stopped")
        if head_focus is not None:
            head_focus.stop()
    return trial


async def run(args: argparse.Namespace) -> SingleTrial | None:
    # Delay the robot-only dependency so --help and offline tests work anywhere.
    import navel

    async with navel.Robot() as robot:
        camera_types = ({"head": getattr(navel, "HeadCamera", None),
                         "chest": getattr(navel, "ChestCamera", None)}
                        if args.camera_capture else None)
        if args.sdk_capture_only:
            await collect_sdk_only(robot, args, camera_types)
        else:
            return await collect_and_stream(robot, args, camera_types)


async def collect_sdk_only(robot: Any, args: argparse.Namespace,
                           camera_types: dict[str, Any] | None = None) -> None:
    """Probe SDK availability without running the observation policy pipeline."""
    transport = ObservationTransport(args.server, timeout_seconds=args.request_timeout)
    capture = SdkCapture(transport)
    camera_capture = (CameraCapture(transport, session_id=capture.session_id)
                      if args.camera_capture else None)

    async def perception_packets() -> None:
        while True:
            try:
                packet = await robot.next_frame(timeout=1.0)
            except TimeoutError:
                continue
            capture.record("perception", packet)

    async def locomotion_packets() -> None:
        while True:
            try:
                packet = await robot.next_locomotion(timeout=1.0)
            except TimeoutError:
                continue
            capture.record("locomotion", packet)

    collectors = [
        asyncio.create_task(locomotion_packets()),
        asyncio.create_task(perception_packets()),
    ]
    if camera_capture is not None:
        collectors.extend(asyncio.create_task(camera_capture.collect(
            name, (camera_types or {}).get(name), args.camera_interval))
            for name in ("head", "chest"))
    sender = asyncio.create_task(capture.send())
    camera_sender = (asyncio.create_task(camera_capture.send())
                     if camera_capture is not None else None)
    tasks = collectors + [sender] + ([camera_sender] if camera_sender is not None else [])
    group = asyncio.gather(*tasks)
    try:
        await asyncio.shield(group)
    finally:
        for task in collectors:
            task.cancel()
        await asyncio.gather(*collectors, return_exceptions=True)
        if not sender.done():
            try:
                await asyncio.wait_for(capture.queue.join(), timeout=args.request_timeout)
            except asyncio.TimeoutError:
                logger.warning("sdk_capture_pending_on_shutdown=%s", capture.queue.qsize())
        if camera_capture is not None and camera_sender is not None and not camera_sender.done():
            try:
                await asyncio.wait_for(camera_capture.queue.join(), timeout=args.request_timeout)
            except asyncio.TimeoutError:
                logger.warning("camera_capture_pending_on_shutdown=%s", camera_capture.queue.qsize())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if group.done() and not group.cancelled():
            group.exception()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream raw Navel sensors to a computer over HTTP.")
    parser.add_argument("--server", default=os.getenv("NAVEL_SENSOR_SERVER", "http://127.0.0.1:6060"),
                        help="Computer's HTTP base URL; use its LAN IP on Navel")
    parser.add_argument("--request-timeout", type=float, default=5.0)
    parser.add_argument("--minimum-send-interval", type=float, default=0.1,
                        help="Minimum seconds between POST starts (default: at most 10 Hz)")
    parser.add_argument("--max-locomotion-age", type=float, default=1.0,
                        help="Seconds before cached robot velocity/ranges become unavailable")
    parser.add_argument("--print-only", action="store_true", help="Print JSON frames without HTTP")
    parser.add_argument("--sdk-capture", action="store_true",
                        help="Also stream complete SDK perception and locomotion packets to --sdk-output on receiver")
    parser.add_argument("--sdk-capture-only", action="store_true",
                        help="Stream only SDK packets; no policy observations or robot commands")
    parser.add_argument("--camera-capture", action="store_true",
                        help="Also stream head and chest RGB frames; receiver needs --camera-output-dir")
    parser.add_argument("--model-provenance", action="store_true",
                        help="Attach captured perception provenance to observation POSTs; requires SDK and camera capture")
    parser.add_argument("--camera-interval", type=float, default=1.0,
                        help="Seconds between camera capture attempts (default: 1.0)")
    parser.add_argument("--decision-dry-run", action="store_true",
                        help="Log validated policy handler calls without executing policy actions")
    parser.add_argument("--single-trial", action="store_true",
                        help="Latch one final decision in decision dry-run mode; no action execution")
    parser.add_argument("--single-trial-policy", choices=("rules", "llm", "vlm"), default="rules")
    parser.add_argument("--decision-wait-timeout", type=float, default=30.0,
                        help="Single-trial decision deadline in local monotonic seconds (default: 30)")
    parser.add_argument("--route-trial", action="store_true",
                        help="Enable REAL straight base movement and head tracking; requires single-trial decision dry-run")
    parser.add_argument("--route-distance", type=float, default=10.0, help="SDK forward distance request in metres (default: 10)")
    parser.add_argument("--route-speed", type=float, default=0.1, help="Route peak speed in m/s (default: 0.1; max: 1.6)")
    parser.add_argument("--route-acceleration", type=float, default=0.2, help="Route acceleration in m/s² (default: 0.2; max: 1.2)")
    parser.add_argument("--command-dry-run", action="store_true",
                        help="Validate correlated commands and POST simulated execution feedback")
    parser.add_argument("--physical-executor", action="store_true",
                        help="Run configured action and route scripts for validated commands")
    parser.add_argument("--approach-script", help="Executable approach script or Python .py file")
    parser.add_argument("--engage-script", help="Executable engage script or Python .py file")
    parser.add_argument("--pause-route-script", help="Executable route pause hook")
    parser.add_argument("--resume-route-script", help="Executable route resume hook")
    parser.add_argument("--stop-script", help="Executable hardware stop hook")
    parser.add_argument("--response-timeout", type=float, default=1.0,
                        help="Cancel a running action after this many seconds without a valid response")
    parser.add_argument("--hook-timeout", type=float, default=2.0)
    parser.add_argument("--stop-timeout", type=float, default=2.0)
    parser.add_argument("--head-focus", action="store_true",
                        help="Move the robot head to follow the first unambiguous visible person")
    parser.add_argument("--head-focus-magnitude", type=float, default=0.5,
                        help="Head motion magnitude for look_at_person, 0..1 (default: 0.5)")
    parser.add_argument("--head-focus-grace", type=float, default=0.75,
                        help="Seconds to hold the current UID through perception loss (default: 0.75)")
    parser.add_argument("--max-decision-age", type=float, default=1.0,
                        help="Maximum age of a source frame when its decision arrives (seconds)")
    parser.add_argument("--decision-timeout", type=float, default=2.0,
                        help="Expire a dry-run decision after this long without a valid response (seconds)")
    args = parser.parse_args(argv)
    if args.route_trial and not args.single_trial:
        parser.error("--route-trial requires --single-trial and --decision-dry-run")
    try:
        StraightRoute(None, args.route_distance, args.route_speed, args.route_acceleration)
    except ValueError as error:
        parser.error(str(error))
    if args.single_trial and (not args.decision_dry_run or args.sdk_capture_only):
        parser.error("--single-trial requires --decision-dry-run and excludes --sdk-capture-only")
    if args.model_provenance:
        if args.sdk_capture_only:
            parser.error("--model-provenance excludes --sdk-capture-only")
        if not args.sdk_capture or not args.camera_capture:
            parser.error("--model-provenance requires --sdk-capture and --camera-capture")
    if args.sdk_capture_only:
        args.sdk_capture = True
        if (args.print_only or args.decision_dry_run or args.command_dry_run
                or args.physical_executor or args.head_focus):
            parser.error("--sdk-capture-only excludes print-only, policy modes, physical executor, and head focus")
    if args.sdk_capture and args.print_only:
        parser.error("--sdk-capture requires HTTP; remove --print-only")
    if args.camera_capture and args.print_only:
        parser.error("--camera-capture requires HTTP; remove --print-only")
    if args.print_only and args.decision_dry_run:
        parser.error("--decision-dry-run requires HTTP; remove --print-only")
    if args.print_only and args.command_dry_run:
        parser.error("--command-dry-run requires HTTP; remove --print-only")
    if args.decision_dry_run and args.command_dry_run:
        parser.error("choose one of --decision-dry-run or --command-dry-run")
    if args.physical_executor and (args.print_only or args.decision_dry_run or args.command_dry_run):
        parser.error("--physical-executor requires HTTP and excludes both dry-run modes")
    script_names = ("approach_script", "engage_script", "pause_route_script",
                    "resume_route_script", "stop_script")
    if args.physical_executor:
        if any(getattr(args, name) is None for name in script_names):
            parser.error("--physical-executor requires approach, engage, pause-route, resume-route, and stop scripts")
        try:
            ScriptPaths(*(getattr(args, name) for name in script_names))
        except ValueError as error:
            parser.error(str(error))
    elif any(getattr(args, name) is not None for name in script_names):
        parser.error("script paths require --physical-executor")
    for name in ("request_timeout", "max_locomotion_age", "minimum_send_interval",
                 "max_decision_age", "decision_timeout", "head_focus_grace",
                 "response_timeout", "hook_timeout", "stop_timeout"):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0 or (name != "minimum_send_interval" and value == 0):
            parser.error("timeouts/maximum ages must be positive and finite; send interval may be zero")
    if not math.isfinite(args.decision_wait_timeout) or args.decision_wait_timeout <= 0:
        parser.error("--decision-wait-timeout must be positive and finite")
    if not math.isfinite(args.head_focus_magnitude) or not 0 <= args.head_focus_magnitude <= 1:
        parser.error("--head-focus-magnitude must be finite and between 0 and 1")
    if not math.isfinite(args.camera_interval) or args.camera_interval <= 0:
        parser.error("--camera-interval must be positive and finite")
    try:
        ObservationTransport(args.server, timeout_seconds=args.request_timeout)
    except ValueError as error:
        parser.error(str(error))
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        trial = asyncio.run(run(args))
        if trial is not None and trial.phase == "FAILED":
            return 1
    except KeyboardInterrupt:
        logger.info("Navel sensor client stopped")
        if args.single_trial:
            return 130
    except ModuleNotFoundError as error:
        if error.name != "navel":
            raise
        logger.error("Navel SDK is required to collect sensors; run this client on the robot.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
