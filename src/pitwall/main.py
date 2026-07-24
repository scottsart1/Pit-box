from __future__ import annotations

import logging
import threading
import webbrowser
from logging.handlers import RotatingFileHandler

import uvicorn

from .config import settings


def configure_logging() -> None:
    """Send Pit Wall logs to a rotating file as well as the console.

    Without this, output only ever reaches the console window that
    ``start_pitwall.bat`` opens, so nothing survives once it is closed and a
    problem reported after a session cannot be investigated.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    if any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        return
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            settings.data_dir / "pitwall.log",
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    except OSError:
        # Logging to disk is a diagnostic aid, never a startup dependency.
        return
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)


def run() -> None:
    configure_logging()
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
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
