from __future__ import annotations

import json
from pathlib import Path

from travel_control_tower.adapters.route_google import GoogleRouteAdapter
from travel_control_tower.adapters.search_flyai import FlyAISearchAdapter

from .models import TripRequest
from .pipeline import build_plan_stub


BASE_DIR = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = BASE_DIR / "examples"


def main() -> None:
    request_path = EXAMPLES_DIR / "japan_osaka_weekend.request.json"
    output_path = EXAMPLES_DIR / "japan_osaka_weekend.plan.json"

    request_data = json.loads(request_path.read_text(encoding="utf-8"))
    request = TripRequest(**request_data)
    route_adapter = GoogleRouteAdapter()
    search_adapter = FlyAISearchAdapter()
    plan = build_plan_stub(
        request,
        route_adapter=route_adapter if route_adapter.is_available else None,
        search_adapter=search_adapter if search_adapter.is_available else None,
    )

    output_path.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
