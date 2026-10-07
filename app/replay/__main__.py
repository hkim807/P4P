"""Preserve the python -m app.replay entry point."""
import sys
from app.replay import main

if __name__ == "__main__":
    sys.argv[0] = "replay.py"
    raise SystemExit(main())
