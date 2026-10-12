"""Compatibility module for app.replay.image_match."""
import sys
from app.replay import image_match as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
