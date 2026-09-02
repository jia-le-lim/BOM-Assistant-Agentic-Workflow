"""Put analysis/ on sys.path so the s* modules import by their bare name,
the same way the notebooks reach them."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
