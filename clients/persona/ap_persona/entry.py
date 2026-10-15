"""Absolute hook entry point. Python -I ignores repo imports and PYTHONPATH."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ap_persona.cli import main
raise SystemExit(main())
