"""EventPulse AI - desktop application entry point.

    .venv/bin/python main.py
or double-click "EventPulse AI.app".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
