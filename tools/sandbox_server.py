"""Run Your Pit Box against a throwaway data root, for verifying UI changes.

Same idea as ``tools.demo_server`` and for the same reason: a driver's saved
sessions are not something to run experiments against. This differs only in
using its own port, its own UDP port and a data root outside the repository, so
it can be started alongside a real Your Pit Box without either one adopting the
other's captures or fighting for the telemetry socket.

    python -m tools.sandbox_server            # 127.0.0.1:8011, UDP 20790
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_ROOT = Path(
    os.environ.get("PITWALL_SANDBOX_ROOT")
    or Path(tempfile.gettempdir()) / "pitwall-sandbox"
)


def main() -> int:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["PITWALL_DATA_DIR"] = str(SANDBOX_ROOT)
    os.environ["PITWALL_OPEN_BROWSER"] = "false"
    os.environ.setdefault("PITWALL_WEB_HOST", "127.0.0.1")
    os.environ.setdefault("PITWALL_WEB_PORT", "8011")
    # A different socket from the real app's 20777, so starting this never
    # steals the telemetry stream from a session actually being recorded.
    os.environ.setdefault("PITWALL_UDP_PORT", "20790")
    sys.path.insert(0, str(ROOT / "src"))

    import uvicorn

    print(f"sandbox data root: {SANDBOX_ROOT}")
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
