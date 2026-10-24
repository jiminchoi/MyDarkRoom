"""Start the local editor: python run.py [--port 8765] [--open-browser]."""
import argparse
import json
import os
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

os.environ.setdefault("OMP_NUM_THREADS", "4")


def open_when_ready(url: str):
    """Open the browser only after the local app answers its readiness check."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(url + "/api/health", timeout=1) as response:
                health = json.load(response)
            if health.get("status") == "ok" and health.get("rawpy"):
                webbrowser.open(url)
                return
        except (URLError, OSError, ValueError):
            pass
        time.sleep(0.2)


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="MyDarkRoom local NEF editor")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="Open the default browser after startup")
    args = parser.parse_args()
    if args.open_browser:
        threading.Thread(target=open_when_ready, args=(f"http://127.0.0.1:{args.port}",), daemon=True).start()
    uvicorn.run("darkroom.app:app", host="127.0.0.1", port=args.port)
