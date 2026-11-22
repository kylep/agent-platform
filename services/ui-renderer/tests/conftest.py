import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))                 # app.py
sys.path.insert(0, str(HERE.parents[2] / "backend"))     # agentplatform (the image copies only part of it)
