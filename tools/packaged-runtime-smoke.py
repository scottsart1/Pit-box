"""Test an existing frozen Windows app without installing over the user's app.

Uses a new disposable data directory, isolated ports and no API credentials.
Does not install, uninstall or change the real app's saved configuration.
The first-run dialog and installer lifecycle remain separate CI/manual checks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distribution.packaging import smoke_installer as smoke


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--capture-min-free-gb", type=float, default=2.0,
                        help="Test-process-only recording reserve; product default is 2 GiB")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The packaged Windows check requires Windows")
    executable = args.executable.resolve(strict=True)
    parent = args.output_parent.resolve(strict=True)
    if shutil.disk_usage(parent).free < 250 * 1024**2:
        parser.error("At least 250 MiB of free space is required for isolated QA evidence")
    root = Path(tempfile.mkdtemp(prefix="packaged-qa-", dir=parent)).resolve()
    assert root.is_relative_to(parent)
    data, diagnostics = root / "data", root / "diagnostics"
    (data / "license").mkdir(parents=True)
    diagnostics.mkdir()
    # Equivalent to choosing Skip on the free-edition welcome dialog; the
    # actual frozen integrity checks still execute on both launches.
    (data / "license" / "welcome_shown").write_text("shown\n", encoding="utf-8")
    reservations = [smoke.reserve_port(socket.SOCK_STREAM), smoke.reserve_port(socket.SOCK_DGRAM)]
    web_port, udp_port = [sock.getsockname()[1] for sock in reservations]
    env = smoke.isolated_environment(data, web_port, udp_port)
    env.pop("VIRTUAL_ENV", None)
    env["PITWALL_DB_MAINTENANCE_ON_START"] = "false"
    if args.capture_min_free_gb <= 0:
        parser.error("The capture reserve must remain positive")
    env["PITWALL_CAPTURE_MIN_FREE_GB"] = str(args.capture_min_free_gb)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = None
    summary = {"result": "running", "version": args.version,
               "executable": str(executable), "executable_sha256": smoke.file_digest(executable),
               "isolated_data": str(data), "installer_tested": False, "checks": []}
    summary["capture_min_free_gb"] = args.capture_min_free_gb
    print(f"QA evidence: {diagnostics}", flush=True)
    try:
        for sock in reservations:
            sock.close()
        for iteration in range(2):
            process = subprocess.Popen([str(executable)], cwd=root, env=env,
                                       startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
            smoke.wait_for_health(process, web_port, args.version, data, 120)
            summary["checks"].append("frozen_startup" if iteration == 0 else "frozen_restart")
            if iteration == 0:
                summary["transfer"] = smoke.exercise_transfers(web_port)
                smoke.exercise_telemetry(process, web_port, udp_port)
                summary["checks"].append("real_udp_live_state")
                if args.stress:
                    stress = smoke.exercise_stress_telemetry(
                        process, web_port, udp_port, args.version, data, diagnostics)
                    summary["stress"] = {key: value for key, value in stress.items() if key != "samples"}
                    summary["checks"].append("synthetic_25_lap_race")
            else:
                reopened = smoke.request_json(web_port, f"/api/v1/sessions/{recorded_id}")
                if str(reopened.get("session", {}).get("game_session_uid")) != str(smoke.SESSION_UID):
                    raise smoke.SmokeFailure("The recorded fixture did not survive restart")
                summary["checks"].append("recorded_session_reopened")
                summary["transfer_after_restart"] = smoke.exercise_transfers(
                    web_port, summary["transfer"].get("certificate_sha256"))
            smoke.stop_owned_server(process, web_port, args.version, data)
            summary["checks"].append("graceful_shutdown")
            recorded_id, _ = smoke.verify_persistence(data, diagnostics=diagnostics)
            if args.stress:
                smoke.verify_persistence(data, int(stress["session_uid"]), require_strategy=True,
                                         diagnostics=diagnostics,
                                         expected_classification=stress["final_classification"])
        summary["result"] = "passed"
    except Exception as exc:
        summary.update(result="failed", error=str(exc))
        raise
    finally:
        for sock in reservations:
            sock.close()
        if process is not None and process.poll() is None:
            try:
                smoke.assert_health(smoke.request_json(web_port, "/api/health"), args.version, data)
                smoke.request_json(web_port, "/api/shutdown", method="POST")
                process.wait(timeout=30)
            except Exception:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        smoke.save_json(diagnostics / "summary.json", summary)
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
