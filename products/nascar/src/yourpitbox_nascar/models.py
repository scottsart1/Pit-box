from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)


class Flag(StrEnum):
    UNKNOWN = "unknown"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    WHITE = "white"
    CHECKERED = "checkered"


class RaceConfig(Model):
    name: str = Field(default="Race weekend", min_length=1, max_length=100)
    track: str = Field(default="Charlotte", min_length=1, max_length=80)
    track_type: Literal["short", "intermediate", "superspeedway", "road"] = "intermediate"
    series: Literal["cup", "oreilly", "trucks", "arca"] = "cup"
    session_type: Literal["practice", "qualifying", "race"] = "race"
    total_laps: int = Field(default=100, ge=1, le=2000)
    stage_ends: list[int] = Field(default_factory=list, max_length=4)
    stage_cautions: bool = True
    overtime_enabled: bool = True
    reserve_laps: float = Field(default=3, ge=0, le=30)
    fuel_burn_green: float | None = Field(default=None, gt=0, le=100)
    fuel_burn_yellow: float | None = Field(default=None, gt=0, le=100)
    fuel_multiplier: float = Field(default=1, ge=0.1, le=10)
    tire_multiplier: float = Field(default=1, ge=0.1, le=10)
    green_pit_loss_s: float | None = Field(default=None, ge=0, le=300)
    yellow_pit_loss_s: float | None = Field(default=None, ge=0, le=300)
    fuel_fill_pct_s: float | None = Field(default=None, gt=0, le=100)
    four_tire_s: float | None = Field(default=None, gt=0, le=120)
    two_tire_s: float | None = Field(default=None, gt=0, le=120)
    service_parallel: bool = True
    mode: Literal["driver", "demo"] = "driver"

    @model_validator(mode="after")
    def ordered_stages(self):
        if self.stage_ends != sorted(set(self.stage_ends)):
            raise ValueError("Stage end laps must be unique and increasing")
        if any(n < 1 or n >= self.total_laps for n in self.stage_ends):
            raise ValueError("Stage ends must be before the final race lap")
        return self


class Tires(Model):
    lf: float | None = Field(default=None, ge=0, le=100)
    rf: float | None = Field(default=None, ge=0, le=100)
    lr: float | None = Field(default=None, ge=0, le=100)
    rr: float | None = Field(default=None, ge=0, le=100)


class Opponent(Model):
    car: str = Field(min_length=1, max_length=12)
    name: str = Field(default="", max_length=80)
    position: int = Field(ge=1, le=80)
    laps_down: int = Field(default=0, ge=0, le=100)
    gap_s: float | None = Field(default=None, ge=0, le=10000)
    last_pit_lap: int | None = Field(default=None, ge=0, le=2000)
    fuel_laps: float | None = Field(default=None, ge=0, le=1000)
    in_pit: bool | None = None


class Sample(Model):
    # A completed-lap counter is unambiguous: the currently driven lap is +1.
    completed_laps: int | None = Field(default=None, ge=0, le=2200)
    leader_completed_laps: int | None = Field(default=None, ge=0, le=2200)
    lap_fraction: float | None = Field(default=None, ge=0, lt=1)
    position: int | None = Field(default=None, ge=1, le=80)
    speed_mph: float | None = Field(default=None, ge=0, le=300)
    fuel_pct: float | None = Field(default=None, ge=0, le=100)
    flag: Flag | None = None
    pit_open: bool | None = None
    in_pit: bool | None = None
    last_lap_s: float | None = Field(default=None, gt=0, le=1800)
    laps_down: int | None = Field(default=None, ge=0, le=100)
    tires: Tires | None = None
    opponents: list[Opponent] | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def unique_field(self):
        if self.opponents:
            if len({x.car for x in self.opponents}) != len(self.opponents):
                raise ValueError("Car numbers in the field must be unique")
            if len({x.position for x in self.opponents}) != len(self.opponents):
                raise ValueError("Positions in the field must be unique")
        return self


class Observation(Model):
    session_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=0)
    source: Literal["manual", "ocr", "adapter", "demo"] = "manual"
    confidence: float = Field(default=1, ge=0, le=1)
    sample: Sample


class Lap(Model):
    number: int = Field(ge=1, le=2200)
    time_s: float | None = Field(default=None, gt=0, le=1800)
    fuel_used_pct: float | None = Field(default=None, ge=0, le=100)
    flag: Flag = Flag.UNKNOWN
    clean: bool = True
    stint: int = Field(default=1, ge=1)
    source: str = "manual"


class PitService(Model):
    tires: Literal["four", "right", "left", "none"] = "four"
    fuel_added_pct: float = Field(default=0, ge=0, le=100)
    damage_s: float = Field(default=0, ge=0, le=600)


class Handling(Model):
    phase: Literal["entry", "middle", "exit"] = "middle"
    balance: Literal["tight", "loose", "neutral"] = "tight"
    timing: Literal["early", "late", "whole"] = "whole"
    severity: int = Field(default=2, ge=1, le=5)
    scope: Literal["minimum", "moderate", "radical"] = "moderate"
    goal: Literal["consistency", "pace", "tire_life"] = "consistency"
    notes: str = Field(default="", max_length=1500)
    available_controls: list[Literal["wedge", "pressures", "brake_bias", "springs", "bars", "camber", "gearing"]] = Field(default_factory=lambda: ["wedge", "pressures"])


class ChatRequest(Model):
    message: str = Field(min_length=1, max_length=2000)


class OCRRegion(Model):
    field: Literal["current_lap", "leader_current_lap", "completed_laps", "position", "fuel_pct", "speed_mph", "last_lap_s", "flag", "pit_open", "tire_lf", "tire_rf", "tire_lr", "tire_rr"]
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.width > 1.001 or self.y + self.height > 1.001:
            raise ValueError("The region must fit inside the game window")
        return self


class OCRConfig(Model):
    window_id: int = Field(ge=1)
    regions: list[OCRRegion] = Field(min_length=1, max_length=16)
    interval_s: float = Field(default=1, ge=0.5, le=10)

    @model_validator(mode="after")
    def unique_regions(self):
        if len({x.field for x in self.regions}) != len(self.regions):
            raise ValueError("Use one region per HUD field")
        return self
