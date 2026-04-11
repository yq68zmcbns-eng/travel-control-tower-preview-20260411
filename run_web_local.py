from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
root_text = str(ROOT)
if sys.path[0] != root_text:
    sys.path.insert(0, root_text)

from travel_control_tower.run_web import main


if __name__ == "__main__":
    main()
