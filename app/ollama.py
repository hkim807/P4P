"""Compatibility entry point for app.inference.ollama."""
import sys
from app.inference import ollama as _implementation

sys.modules[__name__] = _implementation
