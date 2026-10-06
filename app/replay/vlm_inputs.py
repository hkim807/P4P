"""Read associated replay rows and encode their already-selected RGB images."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterator

from app.camera.capture import MAX_RGB_BYTES
from app.camera.recordings import (
    MAX_PPM_HEADER_BYTES, CameraManifestError, ImageValidationError,
    _ppm_dimensions, _validate_manifest_record, read_selected_camera_event,
    read_validated_stored_image,
)
from app.camera.matching import (
    _recorded_perception, _reject_constant, _unique_object, _validate_replay_row,
)
from app.camera.encoding import EncodedVLMImage, encode_rgb_png


class AssociatedReplayError(ValueError):
    """A malformed associated replay input, with the original file and line."""


class VLMImageInputError(ValueError):
    """Image/provenance failures, separate from model request failures."""

    def __init__(self, category: str, message: str) -> None:
        self.category, self.message = category, message
        super().__init__(message)


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a nonblank string without NUL")
    return value


def _absolute_reference(reference: Any, name: str) -> None:
    if not isinstance(reference, dict):
        raise ValueError(f"{name} must contain manifest_path and line_number")
    path = _text(reference.get("manifest_path"), f"{name}.manifest_path")
    if not Path(path).is_absolute():
        raise ValueError(f"{name}.manifest_path must be absolute")
    _integer(reference.get("line_number"), f"{name}.line_number", 1)


def _validate_matching(matching: Any, source: dict[str, Any]) -> None:
    if not isinstance(matching, dict) or type(matching.get("schema_version")) is not int or matching["schema_version"] != 1:
        raise ValueError("requires image_matching object with schema_version 1")
    required = {"status", "ok", "method", "timestamp_basis", "time_difference",
                "reception_time_difference_us", "frame", "error", "policy", "limitations"}
    if not required <= set(matching):
        raise ValueError("requires complete recorded image_matching result fields")
    policy = matching.get("policy")
    if not isinstance(policy, dict) or set(policy) != {"camera", "allow_receipt", "max_age_us"}:
        raise ValueError("requires recorded image_matching policy")
    if policy["camera"] not in ("head", "chest") or type(policy["allow_receipt"]) is not bool:
        raise ValueError("invalid image_matching camera or receipt policy")
    if policy["allow_receipt"]:
        _integer(policy["max_age_us"], "image_matching.policy.max_age_us")
    elif policy["max_age_us"] is not None:
        raise ValueError("image_matching max_age_us requires receipt opt-in")
    if not isinstance(matching.get("limitations"), list) or any(
            not isinstance(value, str) for value in matching["limitations"]):
        raise ValueError("requires recorded image_matching limitations")
    if matching.get("status") == "unmatched":
        error = matching.get("error")
        if matching.get("ok") is not False or matching.get("method") is not None or matching.get("frame") is not None:
            raise ValueError("unmatched image_matching must have ok false and no method/frame")
        if not isinstance(error, dict) or not isinstance(error.get("details"), dict):
            raise ValueError("unmatched image_matching requires an error with details")
        _text(error.get("category"), "image_matching.error.category")
        _text(error.get("message"), "image_matching.error.message")
        details = error["details"]
        references = []
        if "candidate" in details:
            references.append(details["candidate"])
        for key in ("candidates", "events"):
            if key in details:
                if not isinstance(details[key], list):
                    raise ValueError(f"image_matching.error.details.{key} must be a list")
                references.extend(details[key])
        for reference in references:
            _absolute_reference(reference, "image_matching.error manifest reference")
            if "record" in reference:
                _validate_manifest_record(reference["record"])
        return
    if matching.get("status") != "matched" or matching.get("ok") is not True or matching.get("error") is not None:
        raise ValueError("matched image_matching must have ok true and no error")
    method = matching.get("method")
    if method not in ("exact_recorded_sdk_timestamp", "reception_monotonic"):
        raise ValueError("unsupported recorded image_matching method")
    frame = matching.get("frame")
    if not isinstance(frame, dict):
        raise ValueError("matched image_matching requires selected frame provenance")
    _absolute_reference(frame, "image_matching.frame")
    image_path = _text(frame.get("image_path"), "image_matching.frame.image_path")
    if not Path(image_path).is_absolute():
        raise ValueError("selected image_path must be absolute")
    relative = _text(frame.get("relative_image_path"), "image_matching.frame.relative_image_path")
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("selected relative_image_path must remain within its manifest directory")
    references = frame.get("equivalent_manifest_references")
    if not isinstance(references, list) or not references:
        raise ValueError("requires equivalent_manifest_references including the selected reference")
    for reference in references:
        _absolute_reference(reference, "equivalent_manifest_reference")
    if {"manifest_path": frame["manifest_path"], "line_number": frame["line_number"]} not in references:
        raise ValueError("selected manifest reference missing from equivalent_manifest_references")
    camera = _validate_manifest_record(frame.get("camera_manifest_record"))
    if camera["event"] != "frame":
        raise ValueError("selected camera_manifest_record must be a frame")
    sdk_time, receipt = _recorded_perception(source)
    if "sdk_perception_timestamp" not in source["timestamps"]:
        raise ValueError("requires original SDK perception timestamp provenance, including null")
    expected = {
        "original_capture_session": camera["session_id"], "camera": camera["camera"],
        "sequence": camera["sequence"], "relative_image_path": camera["file"],
        "width": camera["width"], "height": camera["height"],
        "sdk_camera_timestamp_us": camera["timestamp_us"],
        "sdk_perception_timestamp": source["timestamps"]["sdk_perception_timestamp"],
        "camera_received_monotonic_us": camera["received_monotonic_us"],
        "camera_received_unix_us": camera["received_unix_us"],
        "perception_received_monotonic_us": receipt,
        "perception_received_unix_us": source["timestamps"]["received_unix_us"],
    }
    for key, value in expected.items():
        if type(frame.get(key)) is not type(value) or frame.get(key) != value:
            raise ValueError(f"selected frame {key} disagrees with recorded provenance")
    if (matching.get("original_capture_session") != source["original_capture_session"]
            or camera["session_id"] != source["original_capture_session"]
            or camera["camera"] != policy["camera"]):
        raise ValueError("selected frame capture session/camera disagrees with source or policy")
    delta = camera["received_monotonic_us"] - receipt
    if type(matching.get("reception_time_difference_us")) is not int or matching["reception_time_difference_us"] != delta:
        raise ValueError("recorded signed reception_time_difference_us is inconsistent")
    if matching.get("reception_clock_provenance") != "SDK/camera capture v1, same original session, same robot-host time.monotonic_ns()":
        raise ValueError("unsupported reception clock provenance")
    if method == "exact_recorded_sdk_timestamp":
        if sdk_time is None or camera["timestamp_us"] != sdk_time:
            raise ValueError("exact matching requires equal recorded SDK timestamps")
        basis = "recorded SDK PerceptionData.time == camera frame.timestamp_us, without scaling"
        difference = {"value": 0, "units": "recorded SDK timestamp units (mapping unverified)",
                      "sign_convention": "camera recorded value minus perception recorded value"}
    else:
        if not policy["allow_receipt"] or delta > 0 or -delta > policy["max_age_us"]:
            raise ValueError("receipt matching violates opt-in, past-frame or maximum-age policy")
        basis = "shared robot-host monotonic reception microseconds in capture v1 co-capture session"
        difference = {"value": delta, "units": "microseconds",
                      "sign_convention": "camera receipt minus perception receipt; negative means earlier camera"}
    if (matching.get("timestamp_basis") != basis or matching.get("time_difference") != difference
            or type(matching.get("time_difference", {}).get("value")) is not int):
        raise ValueError("recorded matching timestamp basis/time_difference is inconsistent")
    validation = frame.get("image_validation")
    if not isinstance(validation, dict) or set(validation) != {"format", "width", "height", "byte_count", "sha256"}:
        raise ValueError("requires recorded image_validation format/dimensions/size/hash")
    if validation["format"] != "PPM P6":
        raise ValueError("selected image_validation must specify PPM P6")
    for key in ("width", "height"):
        if _integer(validation[key], f"image_validation.{key}", 1) != camera[key]:
            raise ValueError("image_validation dimensions disagree with selected camera frame")
    if _integer(validation["byte_count"], "image_validation.byte_count", 1) > MAX_RGB_BYTES + MAX_PPM_HEADER_BYTES:
        raise ValueError("image_validation byte_count exceeds the capture limit")
    if not isinstance(validation["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", validation["sha256"]) is None:
        raise ValueError("image_validation sha256 must be a lowercase SHA-256 digest")


def iter_associated_rows(path: Path | str) -> Iterator[dict[str, Any]]:
    """Validate lazily without normalizing, reconstructing or changing rows."""
    path = Path(path)
    count = 0
    try:
        with path.open("rb") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                     parse_constant=_reject_constant)
                    _validate_replay_row(row, allow_image_matching=True)
                    if "vlm_inference" in row:
                        raise ValueError("input already contains vlm_inference")
                    source_path = _text(row["source"].get("input_path"), "source.input_path")
                    if not Path(source_path).is_absolute():
                        raise ValueError("source.input_path must be absolute")
                    source = row["source"]
                    timestamps = source["timestamps"]
                    estimation = _integer(timestamps.get("estimation_timestamp_us"), "source estimation_timestamp_us")
                    if estimation != row["source_robot_timestamp_us"]:
                        raise ValueError("source estimation timestamp disagrees with frozen SocialState")
                    reconstructed = source.get("reconstructed")
                    if type(reconstructed) is not bool or reconstructed != (source["input_format"] == "sdk"):
                        raise ValueError("source reconstructed flag disagrees with input format")
                    processing_session = _text(source.get("processing_session_id"), "source.processing_session_id")
                    if source["input_format"] != "social" and processing_session != row["session_id"]:
                        raise ValueError("source processing session disagrees with reconstructed SocialState")
                    if source["input_format"] == "sdk":
                        receipt = _integer(timestamps.get("received_monotonic_us"), "source received_monotonic_us")
                        if receipt != estimation:
                            raise ValueError("SDK reconstructed estimation timestamp must equal recorded receipt")
                    _validate_matching(row.get("image_matching"), row["source"])
                except (ValueError, RecursionError) as error:
                    raise AssociatedReplayError(f"{path}:{line_number}: invalid associated replay: {error}") from error
                count += 1
                yield row
    except OSError as error:
        raise AssociatedReplayError(f"{path}: cannot read associated replay: {error}") from error
    if not count:
        raise AssociatedReplayError(f"{path}: associated replay contains no rows")


def load_vlm_image(matching: dict[str, Any]) -> EncodedVLMImage:
    """Revalidate and encode exactly the selected event, with no frame search."""
    if matching.get("status") != "matched" or matching.get("ok") is not True:
        raise VLMImageInputError("image_unavailable", "Recorded image_matching did not select a usable image")
    frame = matching["frame"]
    location = f"{frame['manifest_path']}:{frame['line_number']}"
    try:
        selected = read_selected_camera_event(frame["manifest_path"], frame["line_number"])
    except CameraManifestError as error:
        raise VLMImageInputError("manifest_unavailable", str(error)) from error
    if selected is None or selected.record != frame["camera_manifest_record"] or selected.image_path is None:
        raise VLMImageInputError("manifest_changed", f"{location}: selected manifest record changed or disappeared")
    try:
        if str(selected.image_path.resolve()) != frame["image_path"]:
            raise VLMImageInputError("manifest_changed", f"{location}: selected image path changed")
        validation, data = read_validated_stored_image(selected)
    except ImageValidationError as error:
        raise VLMImageInputError(error.category, error.message) from error
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, VLMImageInputError):
            raise
        raise VLMImageInputError("invalid_image", f"{location}: invalid selected image path: {error}") from error
    if validation != frame["image_validation"]:
        raise VLMImageInputError("image_changed", f"{location}: stored image differs from Step 4 image_validation")
    try:
        # Reuse the already-validated P6 raster boundary. Pillow's direct PPM
        # decoder consumes a CRLF maxval separator differently, shifting pixels
        # for otherwise valid stored images; frombytes preserves the exact RGB8
        # raster while Pillow supplies PNG encoding, without another file read.
        width, height, raster = _ppm_dimensions(data)
        return encode_rgb_png(data[raster:], width, height,
                              source_image_sha256=validation["sha256"], source_byte_count=len(data))
    except (OSError, ValueError) as error:
        raise VLMImageInputError("encoding_error", f"{location}: cannot encode validated RGB image as PNG: {error}") from error
