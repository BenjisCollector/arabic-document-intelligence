"""python -m gulf_idp [--port 8000] [--load-samples]"""
import argparse
import threading
import time
import webbrowser

import httpx
import uvicorn

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--load-samples", action="store_true", help="queue the 14 synthetic sample documents on start")
ap.add_argument("--open", action="store_true", help="open the browser")
a = ap.parse_args()


def kick():
    for _ in range(50):
        try:
            if a.load_samples:
                httpx.post(f"http://127.0.0.1:{a.port}/api/samples/load", timeout=5)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    if a.open:
        webbrowser.open(f"http://127.0.0.1:{a.port}")


threading.Thread(target=kick, daemon=True).start()
uvicorn.run("gulf_idp.api:app", host="127.0.0.1", port=a.port, log_level="warning")
