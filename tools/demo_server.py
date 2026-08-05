"""Run Pit Wall against an isolated demo data root.

Documentation screenshots must not contain a real driver's saved sessions,
so the demo runs on its own database and capture directory rather than the
user's. Nothing here changes application behaviour; it only points the data
root somewhere disposable before the app starts.

    python -m tools.demo_server            # 127.0.0.1:8010
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / ".demo-data"


def main() -> int:
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["PITWALL_DATA_DIR"] = str(DEMO_ROOT)
    # Keep the demo from opening a browser tab or touching the real database.
    os.environ["PITWALL_OPEN_BROWSER"] = "false"
    os.environ.setdefault("PITWALL_WEB_HOST", "127.0.0.1")
    os.environ.setdefault("PITWALL_WEB_PORT", "8010")
    sys.path.insert(0, str(ROOT / "src"))

    import uvicorn

    print(f"demo data root: {DEMO_ROOT}")
    uvicorn.run(
        "pitwall.app:app",
        host=os.environ["PITWALL_WEB_HOST"],
        port=int(os.environ["PITWALL_WEB_PORT"]),
        reload=False,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
