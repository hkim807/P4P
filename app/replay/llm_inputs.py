"""Focused offline inputs for SocialState LLM replay, retaining source provenance.

SDK reconstruction uses receipt time and the existing adapter. It cannot recover
the live collector's queue drops or the small delay between receipt and collection.
Only tracker/estimator state is created here; no rule policy or robot service runs.
Inputs are closed recordings: the content hash is computed before their replay.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from app.pipeline import TrackingPipeline
from app.recording import read_frame_records
from app.sdk_capture import validate_sdk_capture
from app.state.estimator import SocialStateEstimator
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TRACKER_VERSION, TrackConfig
from robot.navel_client.adapter import NavelObservationAdapter


class RecordingInputError(ValueError):
    """An unsupported, malformed, or out-of-order source recording."""


@dataclass(frozen=True)
class ReplayState:
    state: SocialState
    source: dict[str, Any]
    processing: dict[str, Any]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _historical_record(record: Any) -> bool:
    # Historical trace envelopes/intent objects have these top-level fields.
    # Unrelated SDK packet metadata must remain uninterpreted source data.
    return isinstance(record, dict) and (
        "behavior_intent" in record or "behaviour_intent" in record
        or record.get("action") == "MONITOR"
    )


def _error(path: Path, line_number: int | None, message: str) -> RecordingInputError:
    location = f"{path}:{line_number}" if line_number is not None else str(path)
    return RecordingInputError(f"{location}: {message}")


def _json_records(path: Path) -> Iterator[tuple[int, int, dict[str, Any]]]:
    count = 0
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                    parse_constant=_reject_constant)
            except (ValueError, RecursionError) as error:
                raise _error(path, line_number, f"invalid JSON: {error}") from error
            if not isinstance(record, dict):
                raise _error(path, line_number, "record must be a JSON object")
            if _historical_record(record):
                raise _error(path, line_number,
                             "incompatible historical BehaviourIntent/MONITOR recording; "
                             "current raw, SDK capture, or SocialState records are required")
            count += 1
            yield line_number, count, record
    if not count:
        raise _error(path, None, "recording contains no records")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _session_id(digest: str, boundary: int, source_session: str | None) -> str:
    identity = json.dumps([digest, boundary, source_session], separators=(",", ":"))
    return "replay-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _source(path: Path, digest: str, input_format: str, line: int, record_number: int,
            record: dict[str, Any], processing_id: str,
            source_session: str | None) -> dict[str, Any]:
    return {
        "input_path": str(path.resolve()),
        "input_format": input_format,
        "reconstructed": input_format == "sdk",
        "file_sha256": digest,
        "line_number": line,
        "record_number": record_number,
        "original_capture_session": source_session if input_format == "sdk" else None,
        "source_session_id": source_session,
        "processing_session_id": processing_id,
        "source_record": record,
    }


def _processing(state: SocialState, tracking: TrackConfig | None,
                max_locomotion_age_s: float | None = None) -> dict[str, Any]:
    return {
        "track_config": asdict(tracking) if tracking is not None else None,
        "tracker_version": TRACKER_VERSION if tracking is not None else None,
        "temporal_config": state.config.model_dump(mode="json"),
        "temporal_config_version": state.config_version,
        "estimator_version": state.estimator_version,
        "max_locomotion_age_s": max_locomotion_age_s,
    }


def _estimate(pipeline: TrackingPipeline, estimator: SocialStateEstimator,
              raw: dict[str, Any], path: Path, line: int) -> SocialState:
    try:
        return estimator.update(pipeline.process(raw))
    except (ValueError, RuntimeError) as error:
        detail = str(error.__cause__) if error.__cause__ is not None else str(error)
        raise _error(path, line, f"cannot estimate SocialState: {detail}") from error


def _raw_states(path: Path, digest: str, tracking: TrackConfig,
                temporal: TemporalConfig) -> Iterator[ReplayState]:
    processing_id = _session_id(digest, 1, None)
    pipeline = TrackingPipeline(processing_id, tracking)
    estimator = SocialStateEstimator(temporal)
    count = 0
    try:
        for line, record in read_frame_records(path, strict_json=True):
            count += 1
            state = _estimate(pipeline, estimator, record, path, line)
            source = _source(path, digest, "raw", line, count, record, processing_id, None)
            source["timestamps"] = {
                "estimation_timestamp_us": record["timestamp"],
                "estimation_clock": "robot-host monotonic microseconds",
                "estimation_origin": "recorded RawObservationFrame.timestamp at collection",
                "received_monotonic_us": None,
                "received_unix_us": None,
                "sdk_perception_timestamp": None,
                "sdk_perception_clock": None,
            }
            yield ReplayState(state, source, _processing(state, tracking))
    except RecordingInputError:
        raise
    except ValueError as error:
        # The compatible reader already supplies its exact path and line.
        message = str(error)
        if "invalid raw frame" in message:
            message += "; requires current RawObservationFrame, not historical BehaviourIntent/MONITOR"
        raise RecordingInputError(message) from error


def _social_states(path: Path, digest: str) -> Iterator[ReplayState]:
    current_session: str | None = None
    seen_sessions: set[str] = set()
    boundary = 0
    last_time: int | None = None
    last_sequence: int | None = None
    processing_id = ""
    for line, count, record in _json_records(path):
        try:
            state = SocialState.model_validate(record)
        except ValueError as error:
            raise _error(path, line, f"invalid current SocialState: {error}") from error
        if state.session_id != current_session:
            if state.session_id in seen_sessions:
                raise _error(path, line, "source session reappeared after its boundary; interleaved sessions unsupported")
            seen_sessions.add(state.session_id)
            current_session = state.session_id
            boundary += 1
            processing_id = _session_id(digest, boundary, current_session)
            last_time = last_sequence = None
        if (last_time is not None and state.robot_timestamp_us <= last_time
                or last_sequence is not None and state.ingest_sequence <= last_sequence):
            raise _error(path, line, "SocialState source timestamp and ingest sequence must strictly increase within a session")
        last_time, last_sequence = state.robot_timestamp_us, state.ingest_sequence
        source = _source(path, digest, "social", line, count, record, processing_id,
                         state.session_id)
        source["timestamps"] = {
            "estimation_timestamp_us": state.robot_timestamp_us,
            "estimation_clock": "recorded robot-host monotonic microseconds",
            "estimation_origin": "saved SocialState.robot_timestamp_us; snapshot used directly",
            "received_monotonic_us": None,
            "received_unix_us": None,
            "sdk_perception_timestamp": None,
            "sdk_perception_clock": None,
        }
        yield ReplayState(state, source, _processing(state, None))


def _object(value: Any, name: str) -> SimpleNamespace | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"SDK {name} must be an object or null")
    return SimpleNamespace(**value)


def _perception(packet: dict[str, Any]) -> SimpleNamespace:
    persons = packet.get("persons")
    if persons is not None and not isinstance(persons, list):
        raise ValueError("SDK perception.persons must be an array or null")
    converted = []
    for person in persons or []:
        if not isinstance(person, dict):
            raise ValueError("SDK perception person must be an object")
        item = dict(person)
        positions = item.get("g_head_position")
        if positions is not None and not isinstance(positions, list):
            raise ValueError("SDK person.g_head_position must be an array or null")
        if positions is not None:
            item["g_head_position"] = []
            for position in positions:
                if not isinstance(position, dict):
                    raise ValueError("SDK g_head_position entry must be an object")
                item["g_head_position"].append(SimpleNamespace(**position))
        converted.append(SimpleNamespace(**item))
    return SimpleNamespace(persons=converted)


def _locomotion(packet: dict[str, Any]) -> SimpleNamespace:
    odometry = _object(packet.get("odometry"), "locomotion.odometry")
    if odometry is not None:
        odometry.velocity = _object(getattr(odometry, "velocity", None),
                                    "locomotion.odometry.velocity")
    distances = packet.get("distances")
    if distances is not None and not isinstance(distances, dict):
        raise ValueError("SDK locomotion.distances must be an object or null")
    return SimpleNamespace(odometry=odometry, distances=distances)


def _sdk_states(path: Path, digest: str, tracking: TrackConfig,
                temporal: TemporalConfig, max_age_s: float) -> Iterator[ReplayState]:
    session: str | None = None
    seen_sessions: set[str] = set()
    boundary = 0
    last_receipt: int | None = None
    last_perception: int | None = None
    sequences: dict[str, int] = {}
    latest: tuple[SimpleNamespace, dict[str, Any], int, int] | None = None
    pipeline: TrackingPipeline | None = None
    estimator: SocialStateEstimator | None = None
    processing_id = ""
    observations = 0
    for line, count, record in _json_records(path):
        try:
            validate_sdk_capture(record)
            if record["session_id"] != session:
                if record["session_id"] in seen_sessions:
                    raise ValueError("capture session reappeared after its boundary; interleaved sessions unsupported")
                seen_sessions.add(record["session_id"])
                session = record["session_id"]
                boundary += 1
                processing_id = _session_id(digest, boundary, session)
                pipeline = TrackingPipeline(processing_id, tracking)
                estimator = SocialStateEstimator(temporal)
                last_receipt = last_perception = None
                sequences = {}
                latest = None
            receipt = record["received_monotonic_us"]
            if last_receipt is not None and receipt < last_receipt:
                raise ValueError(f"receipt timestamp {receipt} must not precede {last_receipt}")
            stream = record["stream"]
            expected_sequence = sequences.get(stream, 0) + 1
            if record["sequence"] != expected_sequence:
                raise ValueError(f"{stream} sequence {record['sequence']} must be {expected_sequence}")
            sequences[stream] = record["sequence"]
            last_receipt = receipt
            if stream == "locomotion":
                latest = (_locomotion(record["packet"]), record, line, count)
                continue
            if last_perception is not None and receipt <= last_perception:
                raise ValueError(f"perception receipt timestamp {receipt} must be greater than {last_perception}")
            last_perception = receipt
            perception = _perception(record["packet"])
            locomotion = None
            provenance: dict[str, Any] = {"status": "missing", "age_s": None,
                                          "line_number": None, "record_number": None,
                                          "source_record": None}
            if latest is not None:
                packet, loco_record, loco_line, loco_count = latest
                age_s = (receipt - loco_record["received_monotonic_us"]) / 1_000_000
                # File order and receipt order guarantee no future packet is seen.
                fresh = age_s <= max_age_s
                locomotion = packet if fresh else None
                provenance = {"status": "used" if fresh else "stale", "age_s": age_s,
                              "line_number": loco_line, "record_number": loco_count,
                              "source_record": loco_record,
                              "sdk_odometry_timestamp": loco_record["packet"].get("odometry", {}).get("time")
                              if isinstance(loco_record["packet"].get("odometry"), dict) else None,
                              "sdk_odometry_clock": "SDK Odometry.time; units and clock mapping unverified"}
            adapter = NavelObservationAdapter(monotonic_ns=lambda: receipt * 1000)
            raw = adapter.convert(perception, locomotion)
        except (ValueError, RecursionError) as error:
            raise _error(path, line, f"invalid SDK capture: {error}") from error
        assert pipeline is not None and estimator is not None
        state = _estimate(pipeline, estimator, raw, path, line)
        observations += 1
        source = _source(path, digest, "sdk", line, count, record, processing_id, session)
        source["timestamps"] = {
            "estimation_timestamp_us": receipt,
            "estimation_clock": "robot-host monotonic microseconds",
            "estimation_origin": "SDK perception received_monotonic_us, used as reconstructed collection time",
            "received_monotonic_us": receipt,
            "received_unix_us": record["received_unix_us"],
            "received_unix_clock": "capture-host Unix UTC microseconds at receipt; not used for estimation",
            "sdk_perception_timestamp": record["packet"].get("time"),
            "sdk_perception_clock": "SDK PerceptionData.time; units and clock mapping unverified; not used for estimation",
        }
        source["locomotion"] = provenance
        source["reconstructed_raw_observation"] = raw
        processing = _processing(state, tracking, max_age_s)
        processing["sdk_reconstruction"] = {
            "adapter": "NavelObservationAdapter",
            "freshness": "latest earlier-or-equal receipt already seen in file; age <= max_locomotion_age_s",
            "live_frame_equivalence": "not guaranteed; live collection delay and outgoing queue drops cannot be recovered",
        }
        yield ReplayState(state, source, processing)
    if not observations:
        raise _error(path, None, "SDK recording contains no perception observations")


def iter_replay_states(path: Path | str, input_format: str, *,
                       track_config: TrackConfig | None = None,
                       temporal_config: TemporalConfig | None = None,
                       max_locomotion_age_s: float = 1.0) -> Iterator[ReplayState]:
    """Validate/replay one explicit format without reordering or contacting services.

    Each yielded state follows every prior observation in its processing session.
    Consumers may sample these states, but must keep consuming unsampled states.
    """
    path = Path(path)
    if input_format not in {"sdk", "raw", "social"}:
        raise _error(path, None, "input format must be sdk, raw, or social")
    if (isinstance(max_locomotion_age_s, bool)
            or not isinstance(max_locomotion_age_s, (int, float))
            or not math.isfinite(max_locomotion_age_s) or max_locomotion_age_s < 0):
        raise _error(path, None, "max_locomotion_age_s must be finite and nonnegative")
    if input_format == "social" and (track_config is not None or temporal_config is not None):
        raise _error(path, None, "saved SocialState configuration cannot be overridden")
    try:
        digest = _sha256(path)
        if input_format == "social":
            yield from _social_states(path, digest)
        elif input_format == "raw":
            yield from _raw_states(path, digest, track_config or TrackConfig(),
                                   temporal_config or TemporalConfig())
        else:
            yield from _sdk_states(path, digest, track_config or TrackConfig(),
                                   temporal_config or TemporalConfig(), max_locomotion_age_s)
    except OSError as error:
        raise _error(path, None, f"cannot read recording: {error}") from error
