from __future__ import annotations

import threading
import webbrowser

import uvicorn

from .config import settings


def run() -> None:
    if settings.open_browser:
        threading.Timer(
            1.5,
            lambda: webbrowser.open(f"http://{settings.web_host}:{settings.web_port}"),
        ).start()
    uvicorn.run(
        "pitwall.app:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run()
