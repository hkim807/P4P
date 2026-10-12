"""Compatibility module for app.replay.social."""
import sys
from app.replay import social as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
