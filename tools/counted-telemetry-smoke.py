"""Bounded, counted real-time synthetic field test of the candidate EXE only."""
import argparse
import json
import os
import math
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from distribution.packaging import smoke_installer as smoke
from tools import replay_demo as replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--executable", type=Path)
    target.add_argument("--source", action="store_true")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    args = parser.parse_args()
    version = args.version
    parent = args.output_parent.resolve(strict=True)
    assert sys.platform == "win32"
    assert shutil.disk_usage(parent).free > 2.3 * 1024**3
    command = [str(args.executable.resolve(strict=True))] if args.executable else [sys.executable, "-m", "pitwall.main"]
    root = Path(tempfile.mkdtemp(prefix="counted-windows-qa-", dir=parent)).resolve()
    data, diagnostics = root / "data", root / "diagnostics"
    (data / "license").mkdir(parents=True)
    diagnostics.mkdir()
    (data / "license/welcome_shown").write_text("shown\n")
    reservations = [smoke.reserve_port(socket.SOCK_STREAM), smoke.reserve_port(socket.SOCK_DGRAM)]
    web_port, udp_port = [item.getsockname()[1] for item in reservations]
    env = smoke.isolated_environment(data, web_port, udp_port)
    env.pop("VIRTUAL_ENV", None)
    if args.source:
        env["PYTHONPATH"] = str(REPO / "src")
        env["PITWALL_STATIC_DIR"] = str(REPO / "static")
    env["PITWALL_DB_MAINTENANCE_ON_START"] = "false"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"diagnostics={diagnostics}\n")
    process, emitter = None, None
    stop = threading.Event()
    sent = [0]
    emitter_errors = []
    report = {"result": "running", "version": version, "source_process": args.source, "duration_simulated_seconds": 100,
              "nominal_packets_per_second": 186, "full_field_cars": len(replay.GRID),
              "capture_min_free_gb": 2.0, "sample_timeouts": [], "samples": [],
              "full_race_endurance": False, "checks": []}
    print(f"Counted Windows evidence: {diagnostics}", flush=True)
    try:
        for item in reservations:
            item.close()
        process = subprocess.Popen(command, cwd=root, env=env,
                                   startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
        smoke.wait_for_health(process, web_port, version, data, 120)
        before = smoke.request_json(web_port, "/api/v1/network/status")["datagrams"]
        assert smoke.request_json(web_port, "/api/v1/usage")["enabled"] is False
        replay.select_circuit("monza")
        random.seed(7)
        cars = [replay.Car(i, spec) for i, spec in enumerate(replay.GRID)]

        def emit():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    def send(packet):
                        sender.sendto(packet, ("127.0.0.1", udp_port))
                        sent[0] += 1
                    send(replay.build_participants(cars, 0, 0))
                    send(replay.build_session(0, 0, 25, 1))
                    send(replay.build_car_status(cars, 0, 0))
                    send(replay.build_car_damage(cars, 0, 0))
                    for frame in range(1, 6001):
                        if stop.is_set():
                            break
                        tick = time.monotonic()
                        session_time = frame / 60
                        for car in cars:
                            car.race_time = session_time
                            car.total_distance = session_time / car.lap_time_s * replay.TRACK_LENGTH
                            car.distance = car.total_distance % replay.TRACK_LENGTH
                            lap = int(car.total_distance // replay.TRACK_LENGTH) + 1
                            if lap != car.lap:
                                car.last_lap_ms = int(car.lap_time_s * 1000)
                                car.best_lap_ms = car.last_lap_ms
                                car.lap_times.append(car.last_lap_ms)
                                first, second = int(car.last_lap_ms * .34), int(car.last_lap_ms * .37)
                                car.sectors.append((first, second, car.last_lap_ms - first - second))
                                car.position_history.append(car.position)
                                car.lap = lap
                            car.speed = int(300 - 140 * abs(math.sin(car.distance / replay.TRACK_LENGTH * 6 * math.pi)))
                        send(replay.build_lap_data(cars, session_time, frame))
                        send(replay.build_motion(cars, session_time, frame))
                        send(replay.build_telemetry(cars, session_time, frame))
                        if frame % 60 == 0:
                            send(replay.build_car_status(cars, session_time, frame))
                            send(replay.build_car_damage(cars, session_time, frame))
                            send(replay.build_session(session_time, frame, 25, cars[replay.PLAYER_INDEX].lap))
                            send(replay.build_participants(cars, session_time, frame))
                            send(replay.build_lap_positions(cars, session_time, frame))
                            send(replay.build_history(cars[(frame // 60) % len(cars)], session_time, frame))
                        # Windows Event.wait uses a coarser timeout clock than
                        # Python 3.12 time.sleep; do not accidentally halve the
                        # intended packet rate through timeout quantization.
                        time.sleep(max(0, 1 / 60 - (time.monotonic() - tick)))
            except Exception as exc:
                emitter_errors.append(str(exc))

        started = time.monotonic()
        emitter = threading.Thread(target=emit, daemon=True)
        emitter.start()
        while emitter.is_alive():
            timings = {}
            for route in ("/api/health", "/api/state"):
                tick = time.monotonic()
                try:
                    payload = smoke.request_json(web_port, route, timeout=3)
                    if route.endswith("health"):
                        smoke.assert_health(payload, version, data)
                    timings[route] = round(time.monotonic() - tick, 4)
                except TimeoutError:
                    report["sample_timeouts"].append({"route": route, "elapsed_s": round(time.monotonic() - started, 3)})
            report["samples"].append(timings)
            emitter.join(timeout=.5)
        report["emission_elapsed_s"] = round(time.monotonic() - started, 3)
        report["measured_packets_per_second"] = round(sent[0] / report["emission_elapsed_s"], 2)
        assert not emitter_errors, emitter_errors
        assert sent[0] == 18604, sent[0]
        deadline = time.monotonic() + 20
        while True:
            network = smoke.request_json(web_port, "/api/v1/network/status", timeout=3)["datagrams"]
            health = smoke.request_json(web_port, "/api/health", timeout=3)
            if network["parsed"] - before["parsed"] == sent[0] and health["capture"]["packets_written"] == sent[0]:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(.5)
        report["datagrams_observed_delta"] = {key: network[key] - before[key] for key in before}
        report["capture"] = health["capture"]
        report["full_field_archive"] = health["full_field_archive"]
        state = smoke.request_json(web_port, "/api/state")
        report["current_lap"] = state["current_lap"]
        smoke.stop_owned_server(process, web_port, version, data)
        report["checks"].append("graceful_shutdown")
        smoke.verify_persistence(data, replay.SESSION_UID, diagnostics=diagnostics)
        report["checks"].append("database_integrity_and_finalized_capture")
        assert report["datagrams_observed_delta"] == {"received": sent[0], "parsed": sent[0], "rejected": 0}, f"Sent {sent[0]}, received/parsed {report['datagrams_observed_delta']}"
        assert health["capture"]["packets_written"] == sent[0]
        assert health["capture"]["queue_drops"] == health["capture"]["write_errors"] == 0
        assert state["current_lap"] >= 2
        assert report["emission_elapsed_s"] <= 115, "Emitter was too slow to prove the intended packet rate"
        assert not report["sample_timeouts"], "One or more endpoint responses exceeded the unchanged 3-second bound"
        report["result"] = "passed"
    except Exception as exc:
        report.update(result="failed", error=str(exc))
        raise
    finally:
        stop.set()
        if emitter:
            emitter.join(timeout=10)
        for item in reservations:
            item.close()
        if process is not None and process.poll() is None:
            try:
                smoke.stop_owned_server(process, web_port, version, data)
            except Exception:
                process.terminate()
                process.wait(timeout=15)
        report["datagrams_sent"] = sent[0]
        for route in ("/api/health", "/api/state"):
            samples = sorted(item[route] for item in report["samples"] if route in item)
            if samples:
                report[route + "_latency"] = {"max_s": samples[-1], "p95_s": samples[math.ceil(len(samples) * .95) - 1]}
        smoke.save_json(diagnostics / "summary.json", report)
        print(json.dumps({key: value for key, value in report.items() if key != "samples"}), flush=True)


if __name__ == "__main__":
    main()
