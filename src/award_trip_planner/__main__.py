from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .server import create_app

PORT = 8722


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(root / ".env")
    key = os.environ.get("SEATS_AERO_KEY")
    if not key:
        raise SystemExit("SEATS_AERO_KEY missing — put it in .env at the repo root")
    app = create_app(root, key)
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
