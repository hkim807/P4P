"""Compatibility module for app.replay.vlm_inputs."""
import sys
from app.replay import vlm_inputs as _implementation

sys.modules[__name__] = _implementation
