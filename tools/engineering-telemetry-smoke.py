"""Real UDP practice runs and track transition against an isolated QA app only."""

import argparse
import json
import socket
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from f1.packets import PacketCarSetupData, PacketLapData, PacketSessionData

from tools import replay_demo as replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8011")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20790)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def get(path):
        return json.load(urllib.request.urlopen(args.base + path, timeout=20))

    health = get("/api/health")
    data_path = health.get("database", "")
    assert any(
        marker in data_path
        for marker in ("source-qa", "com.yourpitbox.app.qa", "com.yourpitbox.app.debug")
    ), "Refusing synthetic data in production"
    replay.SESSION_UID = int(time.time() * 1000)
    replay.PLAYER_INDEX = 0
    replay.select_circuit("spa")
    cars = [replay.Car(i, spec) for i, spec in enumerate(replay.GRID[:2])]
    for car in cars:
        car.compound = "MEDIUM"
        car.speed = 200
        car.fuel = 20
        car.tyre_age = 1
    frame, clock = 1, 1.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(packet):
        sock.sendto(bytes(packet), (args.host, args.port))

    def session():
        packet = PacketSessionData.from_buffer_copy(
            replay.build_session(clock, frame, 30, cars[0].lap)
        )
        packet.session_type = 1
        packet.track_temperature = 30
        packet.air_temperature = 22
        packet.weather = 0
        send(packet)

    def setup(wing):
        packet = PacketCarSetupData()
        packet.header = replay.header(5, clock, frame)
        packet.car_setup_data[0].front_wing = wing
        packet.car_setup_data[0].rear_wing = 26
        packet.car_setup_data[0].fuel_load = 20
        send(packet)

    def lap_packet(pit=0):
        packet = PacketLapData.from_buffer_copy(
            replay.build_lap_data(cars, clock, frame)
        )
        packet.lap_data[0].pit_status = pit
        packet.lap_data[0].driver_status = 2 if pit else 1
        # Explicit clear-air timing; rival is also physically 500 metres away.
        packet.lap_data[0].delta_to_car_in_front_ms_part = 5000
        send(packet)

    try:
        send(replay.build_participants(cars, clock, frame))
        session()
        setup(28)
        send(replay.build_car_status(cars, clock, frame))
        send(replay.build_car_damage(cars, clock, frame))
        send(replay.build_telemetry(cars, clock, frame))
        lap_packet()
        for run in (1, 2):
            setup(28 if run == 1 else 30)
            for age in range(1, 6):
                n = (run - 1) * 5 + age
                for car in cars:
                    car.lap = n
                    car.tyre_age = age
                    car.last_lap_ms = 90000 if run == 1 else 89700
                    car.fuel = 21 - age
                for sample in range(121):
                    frame += 1
                    clock += 0.75
                    for i, car in enumerate(cars):
                        car.distance = (
                            sample / 121 * replay.TRACK_LENGTH + i * 500
                        ) % replay.TRACK_LENGTH
                        car.total_distance = (
                            (n - 1) * replay.TRACK_LENGTH
                            + sample / 121 * replay.TRACK_LENGTH
                            + i * 500
                        )
                    session() if sample % 40 == 0 else None
                    send(replay.build_car_status(cars, clock, frame))
                    lap_packet()
                    send(replay.build_telemetry(cars, clock, frame))
                    send(replay.build_motion(cars, clock, frame))
                    time.sleep(0.012)
                # Packet 11 supplies the game's official complete sector splits.
                cars[0].lap_times.append(90000 if run == 1 else 89700)
                cars[0].sectors.append((30000, 30000 if run == 1 else 29700, 30000))
                send(replay.build_history(cars[0], clock, frame))
            # Cross the line to finalize the last complete timed lap, then pit.
            frame += 1
            clock += 1
            cars[0].lap += 1
            cars[0].distance = 0
            lap_packet()
            time.sleep(0.2)
            send(replay.build_history(cars[0], clock, frame))
            time.sleep(0.2)
            frame += 1
            clock += 1
            lap_packet(1)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = get("/api/state")
                if (
                    state.get("briefings", {})
                    .get("post_stint", {})
                    .get("payload", {})
                    .get("run_serial")
                    == run
                ):
                    break
                time.sleep(0.3)
            assert (
                state.get("briefings", {})
                .get("post_stint", {})
                .get("payload", {})
                .get("run_serial")
                == run
            ), "No automatic pit-entry debrief"
            if run == 1:
                setup(30)
                cars[0].tyre_age = 0
                cars[0].fuel = 21
                send(replay.build_car_status(cars, clock, frame))
                frame += 1
                clock += 1
                lap_packet(0)
                time.sleep(0.2)
        report = get("/api/v1/engineering/live")
        assert report["available"] and len(report["runs"]) >= 2, report
        candidates = [
            run for run in report["runs"] if run["summary"]["clean_lap_count"] >= 3
        ]
        assert len(candidates) >= 2, [
            (r["number"], r["summary"]) for r in report["runs"]
        ]
        body = json.dumps(
            {"a": candidates[0]["id"], "b": candidates[-1]["id"]}
        ).encode()
        request = urllib.request.Request(
            args.base + f"/api/v1/sessions/{report['session_id']}/engineering/compare",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        comparison = json.load(urllib.request.urlopen(request, timeout=20))
        assert comparison["enough_evidence"], comparison
        assert comparison["sector_deltas_s"] == [0, -0.3, 0], comparison
        old_uid = replay.SESSION_UID
        replay.SESSION_UID += 1
        replay.select_circuit("monza")
        clock = 1
        frame = 1
        session()
        time.sleep(0.5)
        state = get("/api/state")
        assert (
            state["track_id"] == 11
            and not state["briefings"]
            and not state["radio_log"]
        )
        assert not state["analysis"].get("last_lap_analyzed"), state["analysis"]
        # A delayed packet from the retired session must not restore Spa.
        current_uid = replay.SESSION_UID
        replay.SESSION_UID = old_uid
        replay.select_circuit("spa")
        session()
        time.sleep(0.3)
        state = get("/api/state")
        assert state["session_uid"] == current_uid and state["track_id"] == 11
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result = {
            "version": health["version"],
            "session_id": report["session_id"],
            "runs": report["runs"],
            "comparison": comparison,
            "pit_debrief": "pass",
            "track_transition": "pass",
            "retired_packet": "pass",
        }
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "result": "pass",
                    "runs": len(report["runs"]),
                    "matched_pairs": len(comparison["pairs"]),
                    "evidence": str(args.output),
                }
            )
        )
    finally:
        sock.close()


if __name__ == "__main__":
    main()
