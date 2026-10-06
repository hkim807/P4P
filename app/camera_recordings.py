"""Compatibility alias for app.camera.recordings."""
import sys
from app.camera import recordings as _implementation

sys.modules[__name__] = _implementation
