"""Compatibility alias for app.camera.live."""
import sys
from app.camera import live as _implementation

sys.modules[__name__] = _implementation
