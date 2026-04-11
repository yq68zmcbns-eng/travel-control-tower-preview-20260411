from __future__ import annotations

from pathlib import Path

from .render_html import render_plan_file


BASE_DIR = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = BASE_DIR / "examples"


def main() -> None:
    input_path = EXAMPLES_DIR / "japan_osaka_weekend.plan.json"
    output_path = EXAMPLES_DIR / "japan_osaka_weekend.preview.html"
    render_plan_file(input_path, output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
