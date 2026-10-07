"""Compatibility alias for app.camera.capture."""
import sys
from app.camera import capture as _implementation

sys.modules[__name__] = _implementation
