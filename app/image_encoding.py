"""Lossless, bounded in-memory RGB8 to PNG encoding for model images."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from io import BytesIO
import re
from typing import Any

from PIL import Image

from app.camera_capture import MAX_RGB_BYTES


@dataclass(frozen=True)
class EncodedVLMImage:
    image_base64: str
    source_image_sha256: str
    png_sha256: str
    width: int
    height: int
    source_byte_count: int
    png_byte_count: int

    def to_dict(self) -> dict[str, Any]:
        """Output metadata deliberately excludes the encoded image payload."""
        return {"encoding_format": "PNG", "width": self.width, "height": self.height,
                "source_image_sha256": self.source_image_sha256,
                "png_sha256": self.png_sha256, "source_byte_count": self.source_byte_count,
                "png_byte_count": self.png_byte_count}


def encode_rgb_png(rgb: bytes, width: int, height: int, *, source_image_sha256: str,
                   source_byte_count: int) -> EncodedVLMImage:
    """Preserve exactly the provided RGB raster and original source hash metadata.

    Source bytes can be a validated P6 file or raw camera RGB bytes. The caller
    establishes their digest; encoding changes neither pixels nor dimensions.
    """
    if (type(width) is not int or type(height) is not int or width < 1 or height < 1
            or width * height * 3 > MAX_RGB_BYTES):
        raise ValueError("RGB image dimensions must be positive and within the capture byte limit")
    if not isinstance(rgb, (bytes, bytearray, memoryview)) or len(rgb) != width * height * 3:
        raise ValueError("RGB image byte count does not match dimensions")
    if (not isinstance(source_image_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_image_sha256) is None):
        raise ValueError("source_image_sha256 must be a lowercase SHA-256 digest")
    if type(source_byte_count) is not int or source_byte_count < 1:
        raise ValueError("source_byte_count must be a positive integer")
    with Image.frombytes("RGB", (width, height), bytes(rgb)) as image:
        encoded = BytesIO()
        image.save(encoded, format="PNG")
        png = encoded.getvalue()
    return EncodedVLMImage(
        image_base64=base64.b64encode(png).decode("ascii"),
        source_image_sha256=source_image_sha256, png_sha256=hashlib.sha256(png).hexdigest(),
        width=width, height=height, source_byte_count=source_byte_count, png_byte_count=len(png),
    )
