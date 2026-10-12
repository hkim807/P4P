"""Compatibility module for app.replay.lock."""
import sys
from app.replay import lock as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
