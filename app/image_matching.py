"""Compatibility alias for app.camera.matching."""
import sys
from app.camera import matching as _implementation

sys.modules[__name__] = _implementation
