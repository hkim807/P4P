"""Read stored camera manifests and validate their unchanged P6 RGB images.

The manifest is the version-1 output of CameraCaptureWriter, not the live
base64 transport envelope. Events retain their original records and locations;
selection and duplicate ambiguity belong to the matching component.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Iterable

from app.camera.capture import MAX_RGB_BYTES


MAX_PPM_HEADER_BYTES = 4096
_WHITESPACE = b" \t\r\n\v\f"


class CameraManifestError(ValueError):
    """A malformed or unreadable stored camera manifest, with its location."""


class ImageValidationError(ValueError):
    """A selected frame cannot supply a usable stored image."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class CameraEvent:
    manifest_path: Path
    line_number: int
    record: dict[str, Any]
    image_path: Path | None


def _location(path: Path, line: int | None, message: str) -> CameraManifestError:
    prefix = str(path) if line is None else f"{path}:{line}"
    return CameraManifestError(f"{prefix}: {message}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate JSON key: {key}")
        record[key] = value
    return record


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _validate_manifest_record(record: Any) -> dict[str, Any]:
    common = {"capture_version", "session_id", "camera", "sequence", "event",
              "received_monotonic_us", "received_unix_us"}
    if not isinstance(record, dict) or not common <= set(record):
        raise ValueError("invalid stored camera envelope")
    if type(record["capture_version"]) is not int or record["capture_version"] != 1:
        raise ValueError("unsupported camera capture version")
    if (not isinstance(record["session_id"], str) or not record["session_id"].strip()
            or len(record["session_id"]) > 128):
        raise ValueError("invalid session_id")
    if record["camera"] not in ("head", "chest"):
        raise ValueError("invalid camera")
    for name in ("sequence", "received_monotonic_us", "received_unix_us"):
        if type(record[name]) is not int or record[name] < (1 if name == "sequence" else 0):
            raise ValueError(f"invalid {name}")
    if record["event"] == "unavailable":
        if set(record) != common | {"reason", "file"} or record["file"] is not None:
            raise ValueError("invalid stored unavailable event")
        if (not isinstance(record["reason"], str) or not record["reason"].strip()
                or len(record["reason"]) > 500):
            raise ValueError("invalid unavailable reason")
        return record
    if record["event"] != "frame" or set(record) != common | {
            "timestamp_us", "width", "height", "file"}:
        raise ValueError("invalid stored camera frame event")
    for name in ("timestamp_us", "width", "height"):
        if type(record[name]) is not int or record[name] < (0 if name == "timestamp_us" else 1):
            raise ValueError(f"invalid {name}")
    if record["width"] * record["height"] * 3 > MAX_RGB_BYTES:
        raise ValueError("camera frame dimensions exceed the capture RGB byte limit")
    if not isinstance(record["file"], str) or not record["file"].strip():
        raise ValueError("invalid stored camera image file")
    return record


def _image_path(manifest: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\x00" in relative:
        raise ValueError("camera image path must remain relative to the manifest directory")
    directory = manifest.parent.resolve()
    resolved = (directory / path).resolve()
    if not resolved.is_relative_to(directory) or resolved == directory:
        raise ValueError("camera image path escapes the manifest directory")
    return resolved


def load_camera_events(paths: Iterable[Path | str]) -> list[CameraEvent]:
    """Validate explicit manifests, preserving every event including duplicates.

    Images are validated only after selection. Missing files therefore remain
    available as source-located matching failures instead of aborting a pass.
    """
    events: list[CameraEvent] = []
    for supplied in paths:
        path = Path(supplied)
        try:
            path = path.resolve()
        except (OSError, RuntimeError, ValueError) as error:
            raise _location(path, None, f"cannot resolve camera manifest: {error}") from error
        count = 0
        line_number: int | None = None
        try:
            with path.open("rb") as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                            parse_constant=_reject_constant)
                        record = _validate_manifest_record(record)
                        image = (_image_path(path, record["file"])
                                 if record["event"] == "frame" else None)
                    except (ValueError, RecursionError, OSError, RuntimeError) as error:
                        raise _location(path, line_number, f"invalid camera manifest: {error}") from error
                    events.append(CameraEvent(path, line_number, record, image))
                    count += 1
        except CameraManifestError:
            raise
        except OSError as error:
            raise _location(path, line_number, f"cannot read camera manifest: {error}") from error
        if not count:
            raise _location(path, None, "camera manifest contains no events")
    return events


def read_selected_camera_event(path: Path | str, line_number: int) -> CameraEvent | None:
    """Revalidate one exact recorded event without resolving unrelated images.

    This performs no frame selection or timestamp matching. An absent/blank
    original line returns None so callers can report changed provenance.
    """
    path = Path(path)
    if type(line_number) is not int or line_number < 1:
        raise _location(path, None, "selected line_number must be a positive integer")
    try:
        path = path.resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise _location(path, line_number, f"cannot resolve camera manifest: {error}") from error
    try:
        with path.open("rb") as stream:
            for actual_line, line in enumerate(stream, 1):
                if actual_line != line_number:
                    continue
                if not line.strip():
                    return None
                try:
                    record = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                        parse_constant=_reject_constant)
                    record = _validate_manifest_record(record)
                    image = _image_path(path, record["file"]) if record["event"] == "frame" else None
                except (ValueError, RecursionError, OSError, RuntimeError) as error:
                    raise _location(path, line_number, f"invalid selected camera event: {error}") from error
                return CameraEvent(path, line_number, record, image)
    except CameraManifestError:
        raise
    except OSError as error:
        raise _location(path, line_number, f"cannot read camera manifest: {error}") from error
    return None


def _ppm_dimensions(data: bytes) -> tuple[int, int, int]:
    """Parse four P6 header tokens without skipping any raster bytes.

    Comments and whitespace are accepted between header tokens. After maxval,
    consume the required single separator; a CRLF separator is accepted when
    the exact raster length establishes that both bytes belong to the header.
    """
    cursor = 0
    tokens: list[bytes] = []
    for _ in range(4):
        while cursor < len(data):
            if cursor >= MAX_PPM_HEADER_BYTES:
                raise ValueError("PPM header exceeds the supported limit")
            if data[cursor] in _WHITESPACE:
                cursor += 1
            elif data[cursor] == ord("#"):
                newline = data.find(b"\n", cursor, MAX_PPM_HEADER_BYTES)
                if newline < 0:
                    raise ValueError("unterminated PPM header comment")
                cursor = newline + 1
            else:
                break
        start = cursor
        while (cursor < len(data) and data[cursor] not in _WHITESPACE
               and data[cursor] != ord("#")):
            cursor += 1
            if cursor >= MAX_PPM_HEADER_BYTES:
                raise ValueError("PPM header exceeds the supported limit")
        if start == cursor:
            raise ValueError("incomplete PPM header")
        tokens.append(data[start:cursor])
    if tokens[0] != b"P6":
        raise ValueError("stored camera image must have PPM P6 format")
    if any(not token.isdigit() for token in tokens[1:]):
        raise ValueError("PPM dimensions and maxval must be unsigned integers")
    width, height, maxval = (int(token) for token in tokens[1:])
    if width <= 0 or height <= 0 or width * height * 3 > MAX_RGB_BYTES:
        raise ValueError("invalid or excessive PPM dimensions")
    if maxval != 255:
        raise ValueError("stored camera P6 image must use RGB8 maxval 255")
    if cursor >= len(data) or data[cursor] not in _WHITESPACE:
        raise ValueError("PPM maxval must be followed by a whitespace separator")
    separator = data[cursor]
    cursor += 1
    expected = width * height * 3
    if (separator == ord("\r") and data[cursor:cursor + 1] == b"\n"
            and len(data) - cursor == expected + 1):
        cursor += 1
    if len(data) - cursor != expected:
        raise ValueError("PPM RGB payload byte count does not match its dimensions")
    return width, height, cursor


def read_validated_stored_image(event: CameraEvent) -> tuple[dict[str, Any], bytes]:
    """Validate one bounded read and return those same bytes with their digest."""
    location = f"{event.manifest_path}:{event.line_number}"
    if event.record.get("event") != "frame" or event.image_path is None:
        raise ImageValidationError("invalid_image", f"{location}: event has no stored frame image")
    try:
        # Recheck confinement at use time, including a symlink changed since load.
        expected_path = _image_path(event.manifest_path, event.record["file"])
        path = event.image_path.resolve()
        if path != expected_path:
            raise ValueError("stored image path changed since the manifest was loaded")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        raise ImageValidationError("invalid_image", f"{location}: invalid image path: {error}") from error
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ImageValidationError("unreadable_image", f"{location}: image is not a regular file: {path}")
        if metadata.st_size > MAX_RGB_BYTES + MAX_PPM_HEADER_BYTES:
            raise ImageValidationError("invalid_image", f"{location}: stored image exceeds the capture size limit")
        with path.open("rb") as stream:
            data = stream.read(MAX_RGB_BYTES + MAX_PPM_HEADER_BYTES + 1)
    except FileNotFoundError as error:
        raise ImageValidationError("missing_image", f"{location}: stored image is missing: {path}") from error
    except OSError as error:
        raise ImageValidationError("unreadable_image", f"{location}: cannot read image {path}: {error}") from error
    try:
        width, height, _ = _ppm_dimensions(data)
    except ValueError as error:
        raise ImageValidationError("invalid_image", f"{location}: {path}: {error}") from error
    if width != event.record["width"] or height != event.record["height"]:
        raise ImageValidationError("image_dimension_mismatch",
                                   f"{location}: PPM dimensions {width}x{height} disagree with "
                                   f"manifest {event.record['width']}x{event.record['height']}")
    return ({"format": "PPM P6", "width": width, "height": height,
             "byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest()}, data)


def validate_stored_image(event: CameraEvent) -> dict[str, Any]:
    """Establish a selected image's format, dimensions, size and content digest."""
    metadata, _ = read_validated_stored_image(event)
    return metadata
