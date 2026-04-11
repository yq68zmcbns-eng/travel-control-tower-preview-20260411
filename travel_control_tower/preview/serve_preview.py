from __future__ import annotations

import functools
import http.server
import socketserver
import webbrowser
from pathlib import Path

from ..runtime_config import load_runtime_config


BASE_DIR = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = BASE_DIR / "examples"


def main() -> None:
    runtime = load_runtime_config()
    port = runtime.preview_port or 8766
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(EXAMPLES_DIR))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/japan_osaka_weekend.preview.html"
        print(f"Preview server running at {url}")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("Preview server stopped.")


if __name__ == "__main__":
    main()
