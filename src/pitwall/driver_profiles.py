"""Driver profiles on the desktop: one installation, a separate pit box per driver.

The Android app keeps the same rules in Java (``ProfileStore``,
``ProfileEvents``). Each driver opens their profile with an email address (an
identifier, not a secret) or a 4-digit PIN (an app lock). The first profile
created inherits everything stored before profiles existed; every other
profile gets its own data folder, ``.env`` and database.

This module imports nothing from the rest of ``pitwall``: the profile screen
runs before the backend's settings are loaded, because those settings are
read once per process from the chosen profile's folder.

On the desktop the files live in the driver's own Windows account, so a PIN
keeps the app from opening another driver's profile; it does not encrypt
anything a person signed in to that account could open directly.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROFILES_DIR = "driver-profiles"
INDEX_FILE = "profiles.json"
JOURNAL_FILE = "sign-ins.json"
MAX_PROFILES = 20
MAX_NAME = 40
MAX_EMAIL = 254
LOCKOUT_SECONDS = 15 * 60
FAILURES_BEFORE_LOCKOUT = 5
DAILY_FAILURE_LIMIT = 20
PBKDF2_ALGORITHM = "PBKDF2WithHmacSHA256"
PBKDF2_ITERATIONS = 120_000
MAX_PENDING = 500
BATCH = 50
REPORT_ENDPOINT = "https://pitwall-activation.sarthakvij123450.workers.dev/account/"

_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PIN = re.compile(r"^[0-9]{4}$")


class Rejected(ValueError):
    """Why a profile could not be created or opened; the screen words it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class IndexUnavailable(RuntimeError):
    """The profile list exists but cannot be read; nothing has been changed."""


def profiles_dir(root: Path) -> Path:
    return Path(root) / PROFILES_DIR


def data_dir(root: Path, profile: dict[str, Any]) -> Path:
    """The first profile keeps the installation's folder; others get their own."""
    if profile.get("legacy"):
        return Path(root)
    identifier = str(profile.get("id", ""))
    if not _ID.match(identifier):
        raise ValueError("Invalid profile identifier")
    return profiles_dir(root) / identifier


def has_existing_data(root: Path, legacy_env: Path | None = None) -> bool:
    """True when the installation was used before profiles existed.

    The first profile is then told it inherits that history and those
    settings. The licence folder and the profile folder itself do not count:
    both exist before any race is recorded.
    """
    root = Path(root)
    try:
        entries = {entry.name for entry in root.iterdir()}
    except OSError:
        entries = set()
    if entries - {"license", PROFILES_DIR}:
        return True
    try:
        return legacy_env is not None and Path(legacy_env).is_file() and Path(legacy_env).stat().st_size > 0
    except OSError:
        return False


def derive_pin(pin: str, salt: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not _PIN.match(pin or ""):
        raise Rejected("pin_format")
    return hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt.encode("utf-8"), iterations, 32).hex()


def _write_private(path: Path, text: str) -> None:
    """Atomically replace a file, readable only by this account where supported."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


class ProfileStore:
    """The profile list: names, sign-in method, PIN verifiers and counters."""

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.path = profiles_dir(root) / INDEX_FILE
        self._clock = clock
        self._lock = threading.RLock()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return []
            except OSError as error:
                raise IndexUnavailable(str(error)) from error
            try:
                data = json.loads(raw)
            except ValueError as error:
                raise IndexUnavailable("The profile list is not valid JSON") from error
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise IndexUnavailable("The profile list has an unexpected shape")
            return data

    def _write(self, profiles: list[dict[str, Any]]) -> None:
        _write_private(self.path, json.dumps(profiles, indent=1))

    def find(self, identifier: str) -> dict[str, Any]:
        for profile in self.list():
            if profile.get("id") == identifier:
                return profile
        raise Rejected("unknown_profile")

    def create(self, name: str, method: str, *, email: str = "", pin: str = "") -> dict[str, Any]:
        with self._lock:
            profiles = self.list()
            label = (name or "").strip()
            if not label or len(label) > MAX_NAME:
                raise Rejected("name")
            if len(profiles) >= MAX_PROFILES:
                raise Rejected("limit")
            address = (email or "").strip().lower()
            if method == "email":
                if len(address) > MAX_EMAIL or not _EMAIL.match(address):
                    raise Rejected("email_format")
            elif method == "pin":
                if not _PIN.match(pin or ""):
                    raise Rejected("pin_format")
            else:
                raise Rejected("method")
            for existing in profiles:
                if str(existing.get("name", "")).casefold() == label.casefold():
                    raise Rejected("name_taken")
                if method == "email" and address == existing.get("email"):
                    raise Rejected("email_taken")
            identifier = str(uuid.uuid4())
            profile: dict[str, Any] = {
                "id": identifier, "name": label, "method": method,
                "email": address if method == "email" else "",
                "device": secrets.token_hex(32), "account": identifier,
                "legacy": not profiles,
            }
            if method == "pin":
                salt = secrets.token_hex(32)
                profile.update(salt=salt, algorithm=PBKDF2_ALGORITHM, verifier=derive_pin(pin, salt))
            profiles.append(profile)
            self._write(profiles)
            return profile

    def save(self, profile: dict[str, Any]) -> None:
        with self._lock:
            profiles = self.list()
            for index, existing in enumerate(profiles):
                if existing.get("id") == profile.get("id"):
                    profiles[index] = profile
                    self._write(profiles)
                    return
            raise Rejected("unknown_profile")

    def check_email(self, identifier: str, address: str) -> bool:
        profile = self.find(identifier)
        if profile.get("method") != "email":
            return False
        supplied = (address or "").strip().lower().encode("utf-8")
        return hmac.compare_digest(supplied, str(profile.get("email", "")).encode("utf-8"))

    def check_pin(self, identifier: str, pin: str) -> bool:
        with self._lock:
            profile = self.find(identifier)
            if profile.get("method") != "pin":
                return False
            now = self._clock()
            if now < float(profile.get("blocked_until", 0)):
                raise Rejected("locked")
            day = int(now // 86400)
            if profile.get("day") != day:
                profile.update(day=day, daily_failures=0)
            if int(profile.get("daily_failures", 0)) >= DAILY_FAILURE_LIMIT:
                raise Rejected("locked_day")
            # Reserve the attempt before checking it: a crash cannot reset guesses.
            failures = int(profile.get("failures", 0)) + 1
            profile["failures"] = failures
            profile["daily_failures"] = int(profile.get("daily_failures", 0)) + 1
            if failures >= FAILURES_BEFORE_LOCKOUT:
                profile.update(blocked_until=now + LOCKOUT_SECONDS, failures=0)
            self.save(profile)
            try:
                expected = str(profile["verifier"])
                valid = hmac.compare_digest(derive_pin(pin, str(profile["salt"])), expected)
            except Rejected:
                valid = False
            if valid:
                profile.update(failures=0, blocked_until=0, daily_failures=profile["daily_failures"] - 1)
                self.save(profile)
            return valid

    def reset(self) -> Path | None:
        """Set an unreadable list aside so profiles can be created again.

        Nothing is deleted: the file is renamed, and every profile folder stays
        on disk. The first profile created next inherits the installation's
        own history and settings.
        """
        with self._lock:
            if not self.path.exists():
                return None
            kept = self.path.with_name(f"{INDEX_FILE}.unreadable-{int(self._clock())}")
            os.replace(self.path, kept)
            return kept


# ----- Sign-in records -------------------------------------------------------

def outcome(status: int) -> str:
    """What a server answer means for a queued sign-in: sent, rejected or retry."""
    if status == 200:
        return "sent"
    if status in (408, 429) or status < 0 or status >= 500:
        return "retry"
    if status >= 400:
        return "rejected"
    return "retry"


class SignInJournal:
    """Successful openings waiting to be reported, oldest first, bounded."""

    def __init__(self, root: Path) -> None:
        self.path = profiles_dir(root) / JOURNAL_FILE
        self._lock = threading.RLock()

    def pending(self) -> list[Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write(self, events: list[Any]) -> None:
        _write_private(self.path, json.dumps(events))

    def enqueue(self, profile: dict[str, Any], occurred_at: int, version: str) -> None:
        with self._lock:
            events = self.pending()
            events.append({"profile": profile["id"], "event_id": str(uuid.uuid4()),
                           "occurred_at": int(occurred_at), "version": version})
            self._write(events[-MAX_PENDING:])

    def acknowledged(self, event_id: str) -> None:
        with self._lock:
            self._write([event for event in self.pending()
                         if not (isinstance(event, dict) and event.get("event_id") == event_id)])

    def drop_malformed(self) -> int:
        with self._lock:
            events = self.pending()
            kept = [event for event in events if isinstance(event, dict) and event.get("event_id")]
            if len(kept) != len(events):
                self._write(kept)
            return len(events) - len(kept)


def registration(profile: dict[str, Any]) -> dict[str, Any]:
    return {"account_id": profile["account"], "device_secret": profile["device"]}


def payload(profile: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    return {**registration(profile), "event_id": event["event_id"],
            "occurred_at": int(event["occurred_at"]), "version": str(event["version"])}


def flush(journal: SignInJournal, store: ProfileStore, post: Callable[[str, dict[str, Any]], int]) -> int:
    """Send up to one batch; stop at the first retryable failure.

    A request the server rejects would be rejected again on every retry, so
    that event is dropped rather than blocking every later one. Returns the
    number of events removed from the queue.
    """
    removed = journal.drop_malformed()
    for event in journal.pending()[:BATCH]:
        try:
            profile = store.find(str(event.get("profile", "")))
        except Rejected:
            profile = None
        result = "rejected"
        if profile is not None:
            result = outcome(post("profile", registration(profile)))
            if result == "sent":
                result = outcome(post("sign-in", payload(profile, event)))
        if result == "retry":
            break
        journal.acknowledged(str(event["event_id"]))
        removed += 1
    return removed


def version_label(version: str, platform: str) -> str:
    """The app version a sign-in reports, with the platform it came from."""
    suffix = {"win32": "windows", "darwin": "macos"}.get(platform, "desktop")
    return f"{version}-{suffix}"


def http_post(endpoint: str) -> Callable[[str, dict[str, Any]], int]:
    def post(route: str, body: dict[str, Any]) -> int:
        try:
            import httpx

            response = httpx.post(endpoint + route, json=body, timeout=10.0, follow_redirects=False)
            return response.status_code
        except Exception:  # noqa: BLE001 - offline or blocked: keep the record queued
            return -1
    return post


def start_sender(root: Path, *, endpoint: str | None = None, interval_s: float = 60.0) -> threading.Thread:
    """Send queued sign-ins now, then every minute while the app runs."""
    target = endpoint or os.environ.get("PITWALL_PROFILE_REPORT_ENDPOINT") or REPORT_ENDPOINT
    journal, store, post = SignInJournal(root), ProfileStore(root), http_post(target)

    def loop() -> None:
        while True:
            # Reporting must never affect the app; a failure waits for the next round.
            with contextlib.suppress(Exception):
                flush(journal, store, post)
            time.sleep(interval_s)

    thread = threading.Thread(target=loop, name="pitwall-sign-ins", daemon=True)
    thread.start()
    return thread


# ----- The open profile, as the backend sees it ------------------------------

_switch_requested = threading.Event()


def active() -> dict[str, Any]:
    """The open profile for the dashboard: a name and how switching works.

    ``switch`` is ``"restart"`` on the desktop (the app saves, restarts and
    shows the profile screen), ``"native"`` in the Android app (its own
    screen handles the link) and absent when no profile screen is in use.
    """
    name = os.environ.get("PITWALL_PROFILE_NAME", "")
    switch = os.environ.get("PITWALL_PROFILE_SWITCH", "")
    if not name or switch not in {"restart", "native"}:
        return {"active": False}
    return {"active": True, "name": name, "switch": switch}


def request_switch() -> None:
    _switch_requested.set()


def switch_requested() -> bool:
    return _switch_requested.is_set()


__all__ = [
    "IndexUnavailable", "ProfileStore", "Rejected", "SignInJournal", "active", "data_dir",
    "flush", "has_existing_data", "outcome", "payload", "request_switch", "start_sender",
    "switch_requested", "version_label",
]
