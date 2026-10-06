"""Legacy module paths share their implementations and runtime patch points."""
import importlib
import unittest


class AppLayoutCompatibilityTests(unittest.TestCase):
    def test_legacy_modules_share_canonical_module_objects(self):
        mapping = {
            "ollama": "inference.ollama", "live_models": "inference.live", "llm": "inference.llm",
            "camera_capture": "camera.capture", "camera_recordings": "camera.recordings",
            "live_camera": "camera.live", "image_matching": "camera.matching", "image_encoding": "camera.encoding",
            "llm_replay": "replay.llm", "llm_replay_inputs": "replay.llm_inputs",
            "vlm_replay": "replay.vlm", "vlm_inputs": "replay.vlm_inputs", "image_match": "replay.image_match",
            "track": "replay.track", "social": "replay.social", "lock": "replay.lock", "command": "replay.command",
        }
        for legacy, canonical in mapping.items():
            with self.subTest(legacy=legacy):
                self.assertIs(importlib.import_module("app." + legacy),
                              importlib.import_module("app." + canonical))

    def test_raw_replay_keeps_its_existing_package_bindings(self):
        package = importlib.import_module("app.replay")
        self.assertTrue(hasattr(package, "__path__"))
        self.assertIs(package.main.__globals__, vars(package))
        self.assertIs(package.replay.__globals__, vars(package))
