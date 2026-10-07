"""Compatibility module for app.replay.llm."""
import sys
from app.replay import llm as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
