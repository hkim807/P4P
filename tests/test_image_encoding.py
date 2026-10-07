"""One lossless RGB PNG encoder is shared by replay and live inference."""
import base64
import hashlib
from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image

from app.camera_capture import MAX_RGB_BYTES
from app.image_encoding import EncodedVLMImage, encode_rgb_png
from app.vlm_inputs import EncodedVLMImage as ReplayEncodedVLMImage


class ImageEncodingTests(unittest.TestCase):
    def encode(self, rgb=b"\n# \xff\x00\x01", width=2, height=1, **overrides):
        settings = {"source_image_sha256": hashlib.sha256(rgb).hexdigest(), "source_byte_count": len(rgb)}
        settings.update(overrides)
        return encode_rgb_png(rgb, width, height, **settings)

    def test_rgb_pixels_dimensions_hashes_and_raw_base64(self):
        rgb = b"\n# \xff\x00\x01"
        encoded = self.encode(rgb)
        data = base64.b64decode(encoded.image_base64, validate=True)
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (2, 1))
            self.assertEqual(image.tobytes(), rgb)
        self.assertEqual(encoded.png_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(encoded.source_image_sha256, hashlib.sha256(rgb).hexdigest())
        self.assertEqual(encoded.png_byte_count, len(data))
        self.assertEqual(encoded.source_byte_count, len(rgb))
        self.assertNotIn("image_base64", encoded.to_dict())
        self.assertEqual(encoded.to_dict()["encoding_format"], "PNG")

    def test_replay_encoded_class_import_remains_identical(self):
        self.assertIs(EncodedVLMImage, ReplayEncodedVLMImage)

    def test_original_container_hash_and_size_remain_caller_supplied(self):
        encoded = self.encode(source_image_sha256="a" * 64, source_byte_count=21)
        self.assertEqual(encoded.source_image_sha256, "a" * 64)
        self.assertEqual(encoded.source_byte_count, 21)

    def test_rejects_invalid_or_excessive_dimensions_before_codec(self):
        with patch("app.image_encoding.Image.frombytes") as codec:
            for width, height in ((True, 1), (0, 1), (1, -1), (1.0, 1), (1, "2"), (MAX_RGB_BYTES, 1)):
                with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                    self.encode(width=width, height=height)
            codec.assert_not_called()

    def test_rejects_wrong_rgb_type_or_byte_count(self):
        for rgb in (None, "abcdef", b"12345", b"1234567"):
            with self.subTest(rgb=rgb), self.assertRaises(ValueError):
                encode_rgb_png(rgb, 2, 1, source_image_sha256="a" * 64, source_byte_count=6)

    def test_rejects_invalid_source_digest_or_size(self):
        for field, value in (("source_image_sha256", "A" * 64), ("source_image_sha256", "a" * 63),
                             ("source_image_sha256", None), ("source_byte_count", True),
                             ("source_byte_count", 0), ("source_byte_count", 1.5)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.encode(**{field: value})

    def test_bytearray_is_copied_and_pixels_preserved(self):
        rgb = bytearray(b"123456")
        encoded = self.encode(rgb)
        rgb[:] = b"abcdef"
        with Image.open(BytesIO(base64.b64decode(encoded.image_base64))) as image:
            self.assertEqual(image.tobytes(), b"123456")

    def test_codec_failure_remains_an_error_without_output(self):
        with patch("app.image_encoding.Image.frombytes", side_effect=OSError("codec failed")):
            with self.assertRaisesRegex(OSError, "codec failed"):
                self.encode()


if __name__ == "__main__":
    unittest.main()
