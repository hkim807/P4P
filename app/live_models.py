"""Compatibility entry point for app.inference.live."""
import sys
from app.inference import live as _implementation

sys.modules[__name__] = _implementation
