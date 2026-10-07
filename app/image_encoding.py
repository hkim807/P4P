"""Compatibility alias for app.camera.encoding."""
import sys
from app.camera import encoding as _implementation

sys.modules[__name__] = _implementation
