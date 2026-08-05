"""Driver and team identity for the F1 26 (packet format 2026) participant data.

The game reports a participant's display name as a surname only ("VERSTAPPEN"),
so a driver asking "has Max boxed yet" or "how old are Checo's tyres" could not
be matched to a car. ``ParticipantData`` also carries ``driver_id`` and
``race_number``, which resolve to a full identity through the UDP specification's
driver appendix; ``team_id`` resolves through the team appendix.

Matching is deliberately conservative. A name is only accepted when it matches a
complete token, so "ers" cannot match "Verstappen" and "p1" cannot match a name
fragment. When two drivers would match equally (two Schumachers, say), the
resolver reports the ambiguity rather than guessing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

from .catalog import session_car_id, session_id
from .intent import has_phrase, normalize_text

# driver_id -> full name, from the UDP specification driver appendix.
# 255 means a networked human, whose name comes from the packet instead.
DRIVER_NAMES: dict[int, str] = {
    0: "Carlos Sainz", 2: "Daniel Ricciardo", 3: "Fernando Alonso",
    4: "Felipe Massa", 7: "Lewis Hamilton", 9: "Max Verstappen",
    10: "Nico Hulkenberg", 11: "Kevin Magnussen", 14: "Sergio Perez",
    15: "Valtteri Bottas", 17: "Esteban Ocon", 19: "Lance Stroll",
    20: "Arron Barnes", 21: "Martin Giles", 22: "Alex Murray",
    23: "Lucas Roth", 24: "Igor Correia", 25: "Sophie Levasseur",
    26: "Jonas Schiffer", 27: "Alain Forest", 28: "Jay Letourneau",
    29: "Esto Saari", 30: "Yasar Atiyeh", 31: "Callisto Calabresi",
    32: "Naota Izumi", 33: "Howard Clarke", 34: "Lars Kaufmann",
    35: "Marie Laursen", 36: "Flavio Nieves", 38: "Klimek Michalski",
    39: "Santiago Moreno", 40: "Benjamin Coppens", 41: "Noah Visser",
    50: "George Russell", 54: "Lando Norris", 58: "Charles Leclerc",
    59: "Pierre Gasly", 62: "Alexander Albon", 70: "Rashid Nair",
    71: "Jack Tremblay", 77: "Ayrton Senna", 80: "Guanyu Zhou",
    83: "Juan Manuel Correa", 90: "Michael Schumacher", 94: "Yuki Tsunoda",
    102: "Aidan Jackson", 109: "Jenson Button", 110: "David Coulthard",
    112: "Oscar Piastri", 113: "Liam Lawson", 116: "Richard Verschoor",
    123: "Enzo Fittipaldi", 125: "Mark Webber", 126: "Jacques Villeneuve",
    127: "Callie Mayer", 132: "Logan Sargeant", 136: "Jack Doohan",
    137: "Amaury Cordeel", 138: "Dennis Hauger", 145: "Zane Maloney",
    146: "Victor Martins", 147: "Oliver Bearman", 148: "Jak Crawford",
    149: "Isack Hadjar", 152: "Roman Stanek", 153: "Kush Maini",
    156: "Brendon Leigh", 157: "David Tonizza", 158: "Jarno Opmeer",
    159: "Lucas Blakeley", 160: "Paul Aron", 161: "Gabriel Bortoleto",
    162: "Franco Colapinto", 163: "Taylor Barnard", 164: "Joshua Durksen",
    165: "Andrea Kimi Antonelli", 166: "Ritomo Miyata",
    167: "Rafael Villagomez", 168: "Zak O'Sullivan", 169: "Pepe Marti",
    170: "Sonny Hayes", 171: "Joshua Pearce", 172: "Callum Voisin",
    173: "Matias Zagazeta", 174: "Nikola Tsolov", 175: "Tim Tramnitz",
    185: "Luca Cortez",
}

# team_id -> team name, from the UDP specification team appendix. Populating
# this replaces the previous deliberate blank: the code declined to guess team
# names until a verified table existed for the current title.
TEAM_NAMES: dict[int, str] = {
    0: "Mercedes", 1: "Ferrari", 2: "Red Bull Racing", 3: "Williams",
    4: "Aston Martin", 5: "Alpine", 6: "RB", 7: "Haas", 8: "McLaren",
    9: "Sauber", 41: "F1 Generic", 104: "F1 Custom Team", 129: "Konnersport",
    142: "APXGP '24", 154: "APXGP '25", 155: "Konnersport '24",
    158: "Art GP '24", 159: "Campos '24", 160: "Rodin Motorsport '24",
    161: "AIX Racing '24", 162: "DAMS '24", 163: "Hitech '24",
    164: "MP Motorsport '24", 165: "Prema '24", 166: "Trident '24",
    167: "Van Amersfoort Racing '24", 168: "Invicta '24", 185: "Mercedes '24",
    186: "Ferrari '24", 187: "Red Bull Racing '24", 188: "Williams '24",
    189: "Aston Martin '24", 190: "Alpine '24", 191: "RB '24", 192: "Haas '24",
    193: "McLaren '24", 194: "Sauber '24",
}

# Spoken nicknames a driver actually uses on the radio. Keyed by driver_id so a
# nickname can never attach itself to the wrong car.
DRIVER_NICKNAMES: dict[int, tuple[str, ...]] = {
    3: ("nando",),
    7: ("sir lewis",),
    9: ("mad max", "verstappe"),
    10: ("hulk", "the hulk"),
    14: ("checo",),
    54: ("lando norris",),
    58: ("charlie",),
    94: ("yuki san",),
    112: ("oscar the chef",),
    165: ("kimi", "antonelli"),
    161: ("gabi",),
}

# Network humans have no appendix identity.
NETWORK_HUMAN_DRIVER_ID = 255


def driver_full_name(driver_id: int, fallback: str = "") -> str:
    """Full name for an appendix driver id, or the packet name for a human."""
    name = DRIVER_NAMES.get(int(driver_id), "")
    return name or fallback


def team_name(team_id: int, fallback: str = "") -> str:
    return TEAM_NAMES.get(int(team_id), fallback)


def identity_aliases(driver: dict[str, Any]) -> list[str]:
    """Every token a driver could reasonably be called on the radio.

    Includes the packet surname, the appendix full name and each of its parts,
    any nickname, and the car number. Single-character parts are skipped so an
    initial cannot swallow an unrelated word.
    """
    aliases: list[str] = []
    packet_name = str(driver.get("name") or "").strip()
    if packet_name and packet_name.lower() != "unknown":
        aliases.append(packet_name)

    driver_id = int(driver.get("driver_id", -1) or -1)
    full_name = DRIVER_NAMES.get(driver_id, "")
    if full_name:
        aliases.append(full_name)
        aliases.extend(part for part in full_name.split() if len(part) > 2)
    aliases.extend(DRIVER_NICKNAMES.get(driver_id, ()))

    race_number = int(driver.get("race_number", 0) or 0)
    if race_number > 0:
        # Only number references that carry a qualifying word. A bare "#14"
        # cannot be used: phrase matching tokenises on [a-z0-9], which discards
        # the "#" and leaves the pattern "14" — so every integer in a sentence
        # ("box on lap 14", "sector 1", "ERS at 43 percent") would name whoever
        # carries that car number, and the request would then be answered about
        # their car instead of the driver's own.
        aliases.extend((f"car {race_number}", f"number {race_number}"))

    seen: set[str] = set()
    unique: list[str] = []
    for alias in aliases:
        key = normalize_text(alias)
        if key and key not in seen:
            seen.add(key)
            unique.append(alias)
    return unique


def display_name(driver: dict[str, Any]) -> str:
    """The name to speak for a driver: full name when known, else the packet name."""
    full_name = DRIVER_NAMES.get(int(driver.get("driver_id", -1) or -1), "")
    packet_name = str(driver.get("name") or "").strip()
    if full_name:
        return full_name
    return packet_name or "Unknown"


def match_drivers(drivers: Iterable[dict[str, Any]], utterance: str) -> list[dict[str, Any]]:
    """Drivers explicitly named in an utterance, best match first.

    Longer aliases win, so "Michael Schumacher" does not also return every other
    Schumacher, and a full-name mention outranks a bare first name.
    """
    scored: list[tuple[int, dict[str, Any]]] = []
    for driver in drivers:
        if not driver.get("active", True):
            continue
        best = 0
        for alias in identity_aliases(driver):
            if has_phrase(utterance, alias):
                best = max(best, len(normalize_text(alias)))
        if best:
            scored.append((best, driver))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [driver for _, driver in scored]


@dataclass(frozen=True, slots=True)
class SessionKey:
    game_session_uid: str
    restart_epoch: int

    @property
    def id(self) -> str:
        return session_id(self.game_session_uid, self.restart_epoch)


@dataclass(frozen=True, slots=True)
class SessionCarIdentity:
    id: str
    session_id: str
    car_index: int
    identity_revision: int
    driver_id: int | None = None
    network_id: str | None = None
    name: str | None = None
    race_number: int | None = None
    team_id: int | None = None
    nationality_id: int | None = None
    is_ai: bool | None = None
    is_player: bool = False
    first_frame: int = 0
    last_frame: int = 0
    change_reason: str = "first_observation"
    confidence: float = 0.5

    @property
    def anonymized_name(self) -> str:
        return f"Driver {self.car_index + 1:02d}"

    def public_name(self, *, anonymize: bool = False) -> str:
        if anonymize:
            return self.anonymized_name
        return self.name or driver_full_name(self.driver_id or -1, self.anonymized_name)

    def as_record(self, *, anonymize: bool = False) -> dict[str, Any]:
        record = asdict(self)
        record["display_name"] = self.public_name(anonymize=anonymize)
        record["anonymized_name"] = self.anonymized_name
        if anonymize:
            record["name"] = None
            record["network_id"] = None
        return record


class SessionIdentityRegistry:
    """Revisioned identity for historical cars, separate from radio matching.

    Packet array indices remain useful live locators, but a conflicting known
    identity creates a new revision so old samples are never retroactively
    relabelled. Missing fields may enrich the current revision without creating
    false churn as participant packets fill in over time.
    """

    _IDENTITY_FIELDS = (
        "driver_id",
        "network_id",
        "name",
        "race_number",
        "team_id",
        "is_ai",
    )

    def __init__(self) -> None:
        self.session: SessionKey | None = None
        self._last_epoch_by_uid: dict[str, int] = {}
        self._current: dict[int, SessionCarIdentity] = {}
        self._history: list[SessionCarIdentity] = []

    def begin_session(
        self,
        game_session_uid: int | str,
        *,
        restart_evidence: bool = False,
    ) -> SessionKey:
        uid = str(game_session_uid)
        if self.session is not None and self.session.game_session_uid == uid:
            if not restart_evidence:
                return self.session
            epoch = self.session.restart_epoch + 1
        elif restart_evidence or uid in self._last_epoch_by_uid:
            epoch = self._last_epoch_by_uid.get(uid, -1) + 1
        else:
            epoch = 0
        self.session = SessionKey(uid, epoch)
        self._last_epoch_by_uid[uid] = epoch
        self._current = {}
        return self.session

    @staticmethod
    def _clean_optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        number = int(value)
        return None if number in {-1, 255} else number

    @staticmethod
    def _clean_optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return None if not text or text.lower() == "unknown" else text

    def _from_observation(
        self,
        car_index: int,
        frame_identifier: int,
        values: dict[str, Any],
        *,
        revision: int,
        reason: str,
    ) -> SessionCarIdentity:
        if self.session is None:
            raise RuntimeError("begin_session must be called before observing cars")
        name = self._clean_optional_text(values.get("name"))
        driver_id = self._clean_optional_int(values.get("driver_id"))
        known_fields = sum(
            value is not None
            for value in (
                name,
                driver_id,
                self._clean_optional_text(values.get("network_id")),
                self._clean_optional_int(values.get("race_number")),
                self._clean_optional_int(values.get("team_id")),
            )
        )
        return SessionCarIdentity(
            id=session_car_id(self.session.id, car_index, revision),
            session_id=self.session.id,
            car_index=car_index,
            identity_revision=revision,
            driver_id=driver_id,
            network_id=self._clean_optional_text(values.get("network_id")),
            name=name or driver_full_name(driver_id or -1, "") or None,
            race_number=self._clean_optional_int(values.get("race_number")),
            team_id=self._clean_optional_int(values.get("team_id")),
            nationality_id=self._clean_optional_int(values.get("nationality_id")),
            is_ai=(
                bool(values.get("is_ai", values.get("ai_controlled")))
                if "is_ai" in values or "ai_controlled" in values
                else None
            ),
            is_player=bool(values.get("is_player", False)),
            first_frame=int(frame_identifier),
            last_frame=int(frame_identifier),
            change_reason=reason,
            confidence=min(0.99, 0.45 + known_fields * 0.1),
        )

    @staticmethod
    def _known_conflicts(
        current: SessionCarIdentity,
        incoming: SessionCarIdentity,
    ) -> list[str]:
        conflicts: list[str] = []
        for field_name in SessionIdentityRegistry._IDENTITY_FIELDS:
            old = getattr(current, field_name)
            new = getattr(incoming, field_name)
            if old is None or new is None:
                continue
            if field_name == "name":
                differs = normalize_text(str(old)) != normalize_text(str(new))
            else:
                differs = old != new
            if differs:
                conflicts.append(field_name)
        return conflicts

    @staticmethod
    def _merge(
        current: SessionCarIdentity,
        incoming: SessionCarIdentity,
    ) -> SessionCarIdentity:
        updates: dict[str, Any] = {
            "last_frame": max(current.last_frame, incoming.last_frame),
            "is_player": current.is_player or incoming.is_player,
            "confidence": max(current.confidence, incoming.confidence),
        }
        for field_name in (
            "driver_id",
            "network_id",
            "name",
            "race_number",
            "team_id",
            "nationality_id",
            "is_ai",
        ):
            if getattr(current, field_name) is None:
                updates[field_name] = getattr(incoming, field_name)
        return replace(current, **updates)

    def observe(
        self,
        car_index: int,
        frame_identifier: int,
        values: dict[str, Any],
    ) -> SessionCarIdentity:
        index = int(car_index)
        if not 0 <= index < 24:
            raise ValueError("car_index must be between 0 and 23")
        current = self._current.get(index)
        revision = current.identity_revision if current else 0
        incoming = self._from_observation(
            index,
            frame_identifier,
            values,
            revision=revision,
            reason="first_observation" if current is None else "enrichment",
        )
        if current is None:
            self._current[index] = incoming
            self._history.append(incoming)
            return incoming
        conflicts = self._known_conflicts(current, incoming)
        if conflicts:
            revised = self._from_observation(
                index,
                frame_identifier,
                values,
                revision=current.identity_revision + 1,
                reason="changed:" + ",".join(conflicts),
            )
            self._current[index] = revised
            self._history.append(revised)
            return revised
        merged = self._merge(current, incoming)
        self._current[index] = merged
        # Replace the last stored form of this revision so first/last-frame and
        # enrichment metadata stay current without rewriting older revisions.
        for position in range(len(self._history) - 1, -1, -1):
            if self._history[position].id == merged.id:
                self._history[position] = merged
                break
        return merged

    def current(self, car_index: int) -> SessionCarIdentity | None:
        return self._current.get(int(car_index))

    def revisions(self, car_index: int | None = None) -> tuple[SessionCarIdentity, ...]:
        if car_index is None:
            return tuple(self._history)
        return tuple(item for item in self._history if item.car_index == int(car_index))
