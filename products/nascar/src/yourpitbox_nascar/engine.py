from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from copy import deepcopy

from .analysis import field_strategy, fuel_plan, long_run, pit_options, setup_review
from .models import Flag, Handling, Lap, Observation, PitService, RaceConfig
from .store import Store


class RaceEngine:
    """Bounded latest-state ingestion, independent of UI, OCR and cloud latency."""

    def __init__(self, store: Store, clock=time.time):
        self.store, self.clock = store, clock
        self.lock = threading.RLock()
        self.sid: str | None = None
        self.config = RaceConfig()
        self.values: dict = {}
        self.evidence: dict = {}
        self.laps: deque[Lap] = deque(maxlen=2200)
        self.sequences: dict[str, int] = {}
        self.radio: deque[dict] = deque(maxlen=80)
        self.radio_seq = 0
        self.stint = 1
        self.anchor: dict | None = None
        self.lap_dirty = False
        self.lap_flag: str | None = None
        self.handling_report: dict | None = None
        self.last_radio: dict[str, float] = {}
        self.last_snapshot_at = 0.0
        self.metrics = {"accepted": 0, "rejected": 0, "low_confidence": 0}

    def start(self, config: RaceConfig) -> str:
        with self.lock:
            self.flush()
            self.sid = uuid.uuid4().hex
            self.config = config
            self.values, self.evidence, self.sequences = {}, {}, {}
            self.laps.clear()
            self.radio.clear()
            self.last_radio.clear()
            self.stint, self.anchor, self.lap_dirty, self.lap_flag = 1, None, False, None
            self.handling_report = None
            self.store.create(self.sid, config.model_dump(mode="json"))
            self.store.save_setting("active_session", self.sid)
            return self.sid

    def resume(self, sid: str):
        with self.lock:
            self.flush()
            saved = self.store.read(sid)
            if not saved:
                raise ValueError("Session not found")
            self.sid = sid
            self.config = RaceConfig.model_validate(saved["config"])
            self.values = saved["snapshot"].get("values", {})
            # A persisted observation is historical, never newly live on restart.
            self.evidence = {k: {**v, "source": "history", "at": 0} for k, v in saved["snapshot"].get("evidence", {}).items()}
            self.laps = deque((Lap.model_validate(x) for x in saved["laps"]), maxlen=2200)
            self.sequences, self.anchor, self.lap_flag = {}, None, None
            self.stint = max(saved["snapshot"].get("stint", 1), max((x.stint for x in self.laps), default=1))
            self.lap_dirty = False
            self.handling_report = next((x["data"]["report"] for x in reversed(saved["notes"]) if x["kind"] == "handling"), None)
            self.radio.clear()
            self.last_radio.clear()
            self.store.save_setting("active_session", sid)

    def flush(self):
        if self.sid:
            self.store.snapshot(self.sid, {"values": self.values, "evidence": self.evidence, "stint": self.stint})
            self.last_snapshot_at = self.clock()

    def ingest(self, observation: Observation) -> dict:
        with self.lock:
            if observation.session_id != self.sid:
                self.metrics["rejected"] += 1
                raise ValueError("Observation belongs to a different session")
            if (observation.source == "demo") != (self.config.mode == "demo"):
                self.metrics["rejected"] += 1
                raise ValueError("Demo and driver observations cannot mix")
            if observation.sequence <= self.sequences.get(observation.source, -1):
                self.metrics["rejected"] += 1
                return {"accepted": False, "reason": "out-of-order or duplicate"}
            self.sequences[observation.source] = observation.sequence
            if observation.confidence < .85:
                self.metrics["low_confidence"] += 1
                return {"accepted": False, "reason": "low confidence"}
            incoming = observation.sample.model_dump(exclude_unset=True, mode="json")
            if not incoming:
                return {"accepted": False, "reason": "empty observation"}
            now = self.clock()
            previous = deepcopy(self.values)
            source_evidence = [v for v in self.evidence.values() if v["source"] == observation.source]
            if source_evidence and observation.source in ("ocr", "adapter") and now - max(x["at"] for x in source_evidence) > 8:
                self.anchor = None
                self.lap_dirty = True
            n = incoming.get("completed_laps")
            prev_n = previous.get("completed_laps")
            if n is not None and prev_n is not None and n < prev_n:
                self.metrics["rejected"] += 1
                return {"accepted": False, "reason": "lap counter regressed; start a new session for a restart"}
            if n is not None and prev_n is not None and n > prev_n + 1:
                self.anchor = None
                self.lap_dirty = True
            if n is not None and prev_n is not None and n > prev_n and "fuel_pct" not in incoming and "fuel_pct" in self.evidence:
                # A new lap with no fuel observation must not combine a current
                # race distance with a previous lap's apparently current tank.
                self.evidence["fuel_pct"]["at"] = now - 301
            flag_evidence = self.evidence.get("flag", {})
            prior_flag = previous.get("flag") if flag_evidence.get("source") != "history" and now - flag_evidence.get("at", 0) <= 8 else None
            flag = incoming.get("flag", prior_flag)
            # Losing race-control evidence cannot silently train the green rate.
            # Once pit entry is observed, require an explicit exit to clear it.
            in_pit = incoming.get("in_pit", previous.get("in_pit"))
            if in_pit or flag not in (Flag.GREEN, Flag.YELLOW, Flag.WHITE) or flag != self.lap_flag:
                self.lap_dirty = True
            if incoming.get("fuel_pct") is not None and previous.get("fuel_pct") is not None and incoming["fuel_pct"] > previous["fuel_pct"] + .5:
                self.lap_dirty = True
            # Arrays replace atomically; tire fields merge individually and retain
            # individual timestamps so a partial HUD cannot refresh old corners.
            for key, value in incoming.items():
                if key == "tires" and value is not None:
                    if not isinstance(self.values.get("tires"), dict):
                        self.values["tires"] = {}
                    for wheel, reading in value.items():
                        self.values["tires"][wheel] = reading
                        self.evidence[f"tires.{wheel}"] = {"at": now, "source": observation.source, "confidence": observation.confidence}
                else:
                    self.values[key] = value
                self.evidence[key] = {"at": now, "source": observation.source, "confidence": observation.confidence}
            # A complete lap needs consecutive boundaries, a matching fresh fuel
            # observation at each boundary, and no caution/pit transition in it.
            if n is not None and n != prev_n:
                if self.anchor and n == self.anchor["number"] + 1:
                    fresh_fuel = incoming.get("fuel_pct")
                    used = self.anchor["fuel"] - fresh_fuel if fresh_fuel is not None and self.anchor["fuel"] is not None else None
                    if used is not None and not 0 <= used <= 100:
                        used = None
                    lap = Lap(number=n, time_s=incoming.get("last_lap_s"), fuel_used_pct=used,
                              flag=self.lap_flag or Flag.UNKNOWN, clean=not self.lap_dirty,
                              stint=self.stint, source=observation.source)
                    self._lap(lap)
                self.anchor = {"number": n, "fuel": incoming.get("fuel_pct")}
                self.lap_dirty = bool(in_pit) or flag not in (Flag.GREEN, Flag.YELLOW, Flag.WHITE)
                self.lap_flag = flag
            self.metrics["accepted"] += 1
            if previous.get("flag") != flag and flag in (Flag.YELLOW, Flag.RED, Flag.WHITE, Flag.CHECKERED):
                text = {Flag.YELLOW: "Caution reported. Check pit-road status and fuel options.",
                        Flag.RED: "Red flag reported. Race paused; hold strategy calls.",
                        Flag.WHITE: "White flag reported. One lap to go.",
                        Flag.CHECKERED: "Checkered flag. Your run is saved."}[flag]
                self.call(f"flag:{flag}", text, "race_control", 2)
            if now - self.last_snapshot_at >= 2:
                self.flush()
            return {"accepted": True}

    def _lap(self, lap: Lap):
        self.laps = deque((x for x in self.laps if x.number != lap.number), maxlen=2200)
        self.laps.append(lap)
        self.store.save_lap(self.sid, lap.model_dump(mode="json"))

    def add_laps(self, laps: list[Lap]):
        with self.lock:
            if not self.sid:
                raise ValueError("Start a weekend first")
            for lap in laps:
                self._lap(lap)
            self.laps = deque(sorted(self.laps, key=lambda x: x.number), maxlen=2200)
            self.stint = max(self.stint, max((x.stint for x in self.laps), default=1))

    def import_laps(self, config: RaceConfig, laps: list[Lap]) -> str:
        # Creating a CSV session and saving its laps must remain one operation
        # even when another device switches the active weekend concurrently.
        with self.lock:
            sid = self.start(config)
            self.add_laps(laps)
            self.flush()
            return sid

    def live_values(self) -> tuple[dict, list[str]]:
        values = deepcopy(self.values)
        stale = []
        now = self.clock()
        for key, evidence in self.evidence.items():
            ttl = 300 if evidence["source"] == "manual" and key not in ("flag", "pit_open", "in_pit") else 8
            if evidence["source"] == "history" or now - evidence["at"] > ttl:
                stale.append(key)
                if key.startswith("tires."):
                    if isinstance(values.get("tires"), dict):
                        values["tires"][key.split(".")[1]] = None
                elif key != "tires":
                    values[key] = None
        return values, stale

    def snapshot(self, yellow_laps: float = 0) -> dict:
        with self.lock:
            values, stale = self.live_values()
            config = self.config
            fuel = fuel_plan(config, values, list(self.laps), yellow_laps)
            return {"session_id": self.sid, "config": config.model_dump(mode="json"),
                    "values": values, "last_observed": deepcopy(self.values), "stale_fields": stale,
                    "evidence": deepcopy(self.evidence), "fuel": fuel,
                    "pits": pit_options(config, values, fuel), "field": field_strategy(values, fuel),
                    "run": long_run(list(self.laps)), "laps": [x.model_dump(mode="json") for x in self.laps],
                    "handling_report": deepcopy(self.handling_report),
                    "stint": self.stint, "radio": list(self.radio), "metrics": dict(self.metrics), "at": self.clock()}

    def call(self, key: str, text: str, kind="strategy", cooldown=60):
        now = self.clock()
        if now - self.last_radio.get(key, -1e12) < cooldown:
            return
        self.last_radio[key] = now
        self.radio_seq += 1
        self.radio.append({"id": self.radio_seq, "at": now, "text": text, "kind": kind,
                           "demo": self.config.mode == "demo"})

    def proactive(self):
        with self.lock:
            values, _ = self.live_values()
            flag = values.get("flag")
            if not self.sid or flag in (Flag.RED, Flag.CHECKERED):
                return
            plan = fuel_plan(self.config, values, list(self.laps))
            fuel_evidence = self.evidence.get("fuel_pct", {})
            fuel_current = fuel_evidence.get("source") != "history" and self.clock() - fuel_evidence.get("at", 0) <= 8
            if fuel_current and plan["fuel_laps"] is not None and plan["fuel_laps"] <= 2:
                access = values.get("pit_open")
                suffix = "Pit road is closed." if access is False else "Confirm pit access." if access is None else "Pit road is reported open."
                if flag not in (Flag.GREEN, Flag.YELLOW, Flag.WHITE):
                    suffix = "Race control is unconfirmed. " + suffix
                self.call("fuel_critical", f"Fuel estimate: {plan['fuel_laps']:g} green laps. {suffix}", "urgent", 45)
            if flag in (Flag.GREEN, Flag.YELLOW, Flag.WHITE) and plan["stage_remaining"] is not None and 0 < plan["stage_remaining"] <= 2:
                self.call(f"stage:{plan['stage_end']}", f"Two laps or less to the configured stage end at lap {plan['stage_end']}. Review the next fuel window.", cooldown=300)

    def pit(self, service: PitService):
        with self.lock:
            if not self.sid:
                raise ValueError("Start a weekend first")
            if self.config.mode == "demo":
                raise ValueError("Service logging is disabled during the sample replay")
            self.stint += 1
            self.anchor, self.lap_dirty = None, True
            self.store.note(self.sid, "pit", {**service.model_dump(), "completed_laps": self.values.get("completed_laps"), "stint": self.stint})
            self.flush()

    def handling(self, report: Handling) -> dict:
        with self.lock:
            result = setup_review(report, self.config, list(self.laps))
            if self.sid:
                self.store.note(self.sid, "handling", {"report": report.model_dump(), "review": result})
                self.handling_report = report.model_dump(mode="json")
            return result
