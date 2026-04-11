from __future__ import annotations

import os

from travel_control_tower.web.app import serve


def main() -> None:
    host = os.environ.get("TRAVEL_WEB_HOST", "").strip() or "0.0.0.0"
    serve(host=host, open_browser=False)


if __name__ == "__main__":
    main()
