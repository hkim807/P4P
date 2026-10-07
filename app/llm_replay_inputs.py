"""Compatibility module for app.replay.llm_inputs."""
import sys
from app.replay import llm_inputs as _implementation

sys.modules[__name__] = _implementation
