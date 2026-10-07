"""Compatibility entry point for app.inference.llm."""
import sys
from app.inference import llm as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
