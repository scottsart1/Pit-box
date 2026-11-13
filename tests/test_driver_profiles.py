"""Desktop driver profiles: the store, PIN lockouts and the sign-in queue.

The Android app keeps the same rules in Java (ProfileStoreCheck covers it);
these check the desktop implementation the profile screen uses.
"""
from __future__ import annotations

import json

import pytest

from pitwall import driver_profiles as profiles


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_first_profile_inherits_the_installation_and_later_ones_get_their_own(tmp_path):
    store = profiles.ProfileStore(tmp_path)
    assert store.list() == []
    first = store.create("Driver One", "pin", pin="0123")
    second = store.create("Email Driver", "email", email=" Driver@Example.TEST ")
    assert first["legacy"] is True and second["legacy"] is False
    assert profiles.data_dir(tmp_path, first) == tmp_path
    assert profiles.data_dir(tmp_path, second) == tmp_path / "driver-profiles" / second["id"]
    assert second["email"] == "driver@example.test"
    stored = (tmp_path / "driver-profiles" / "profiles.json").read_text()
    assert "0123" not in stored
    assert first["email"] == ""
    with pytest.raises(ValueError):
        profiles.data_dir(tmp_path, {"id": "../elsewhere", "legacy": False})


@pytest.mark.parametrize(("args", "reason"), [
    (("  ", "pin"), "name"),
    (("x" * 41, "pin"), "name"),
    (("Driver", "pin", "", "123"), "pin_format"),
    (("Driver", "pin", "", "１２３４"), "pin_format"),
    (("Driver", "email", "not-an-address"), "email_format"),
    (("Driver", "sms"), "method"),
])
def test_invalid_profiles_are_refused_with_a_reason(tmp_path, args, reason):
    name, method, *rest = args
    email = rest[0] if rest else ""
    pin = rest[1] if len(rest) > 1 else ""
    with pytest.raises(profiles.Rejected) as refused:
        profiles.ProfileStore(tmp_path).create(name, method, email=email, pin=pin)
    assert refused.value.reason == reason


def test_names_and_emails_are_unique_and_the_device_holds_twenty(tmp_path):
    store = profiles.ProfileStore(tmp_path)
    store.create("Driver", "email", email="one@example.test")
    with pytest.raises(profiles.Rejected, match="name_taken"):
        store.create("DRIVER", "pin", pin="1111")
    with pytest.raises(profiles.Rejected, match="email_taken"):
        store.create("Someone else", "email", email="ONE@example.test")
    for index in range(profiles.MAX_PROFILES - 1):
        store.create(f"Driver {index}", "pin", pin="1234")
    with pytest.raises(profiles.Rejected, match="limit"):
        store.create("One more", "pin", pin="1234")


def test_email_is_an_identifier_and_never_opens_a_pin_profile(tmp_path):
    store = profiles.ProfileStore(tmp_path)
    pin = store.create("Pin", "pin", pin="2468")
    email = store.create("Mail", "email", email="mail@example.test")
    assert store.check_email(email["id"], " MAIL@example.test ")
    assert not store.check_email(email["id"], "other@example.test")
    assert not store.check_email(pin["id"], "")
    assert not store.check_pin(email["id"], "2468")
    with pytest.raises(profiles.Rejected, match="unknown_profile"):
        store.check_pin("12345678-1234-4123-8123-123456789abc", "2468")


def test_pin_lockouts_survive_reopening_the_store(tmp_path):
    clock = Clock()
    store = profiles.ProfileStore(tmp_path, clock=clock)
    driver = store.create("Driver", "pin", pin="0123")
    assert store.check_pin(driver["id"], "0123")
    for _ in range(profiles.FAILURES_BEFORE_LOCKOUT):
        assert not store.check_pin(driver["id"], "9999")
    reopened = profiles.ProfileStore(tmp_path, clock=clock)
    with pytest.raises(profiles.Rejected, match="locked"):
        reopened.check_pin(driver["id"], "0123")
    clock.now += profiles.LOCKOUT_SECONDS + 1
    assert reopened.check_pin(driver["id"], "0123")


def test_twenty_wrong_pins_lock_the_day_and_tomorrow_opens(tmp_path):
    clock = Clock()
    store = profiles.ProfileStore(tmp_path, clock=clock)
    driver = store.create("Driver", "pin", pin="0123")
    failures = 0
    while failures < profiles.DAILY_FAILURE_LIMIT:
        try:
            assert not store.check_pin(driver["id"], "9999")
            failures += 1
        except profiles.Rejected:   # A 15-minute lockout: wait it out.
            clock.now += profiles.LOCKOUT_SECONDS + 1
    clock.now += profiles.LOCKOUT_SECONDS + 1
    with pytest.raises(profiles.Rejected, match="locked_day"):
        store.check_pin(driver["id"], "0123")
    clock.now += 86400
    assert store.check_pin(driver["id"], "0123")


def test_the_android_and_desktop_pin_verifiers_agree():
    # PBKDF2-HMAC-SHA256, 120,000 iterations, 256-bit output, as PinCredential.
    expected = __import__("hashlib").pbkdf2_hmac("sha256", b"0123", b"salt", 120_000, 32).hex()
    assert profiles.derive_pin("0123", "salt") == expected


def test_an_unreadable_list_is_set_aside_not_deleted(tmp_path):
    store = profiles.ProfileStore(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{not json")
    with pytest.raises(profiles.IndexUnavailable):
        store.list()
    kept = store.reset()
    assert kept is not None and kept.read_text() == "{not json"
    assert store.list() == []


def test_existing_data_is_anything_but_the_licence_and_profiles(tmp_path):
    root, install = tmp_path / "PitWallData", tmp_path / "install"
    assert not profiles.has_existing_data(root)
    (root / "license").mkdir(parents=True)
    (root / "driver-profiles").mkdir()
    assert not profiles.has_existing_data(root)
    env = install / ".env"
    install.mkdir()
    env.write_text("")
    assert not profiles.has_existing_data(root, env)
    env.write_text("OPENAI_API_KEY=sk-test\n")
    assert profiles.has_existing_data(root, env)
    (root / "pitwall.db").write_text("x")
    assert profiles.has_existing_data(root)


def test_sign_ins_carry_no_email_pin_or_name_and_stay_bounded(tmp_path):
    store = profiles.ProfileStore(tmp_path)
    driver = store.create("Email Driver", "email", email="driver@example.test")
    journal = profiles.SignInJournal(tmp_path)
    journal.enqueue(driver, 1_700_000_000, "5.7.1-windows")
    event = journal.pending()[0]
    report = profiles.payload(driver, event)
    assert set(report) == {"account_id", "device_secret", "event_id", "occurred_at", "version"}
    assert "driver@example.test" not in json.dumps(report) and "Email Driver" not in json.dumps(report)
    for index in range(profiles.MAX_PENDING + 10):
        journal.enqueue(driver, index, "v")
    kept = journal.pending()
    assert len(kept) == profiles.MAX_PENDING and kept[0]["occurred_at"] == 10


def test_delivery_drops_rejections_and_waits_out_outages(tmp_path):
    store = profiles.ProfileStore(tmp_path)
    driver = store.create("Driver", "pin", pin="0123")
    journal = profiles.SignInJournal(tmp_path)
    for index in range(3):
        journal.enqueue(driver, index, "v")
    calls = []
    assert profiles.flush(journal, store, lambda route, body: calls.append(route) or -1) == 0
    assert calls == ["profile"] and len(journal.pending()) == 3
    answers = iter([200, 400, 200, 200, 200, 200])
    assert profiles.flush(journal, store, lambda route, body: next(answers)) == 3
    assert journal.pending() == []
    journal.enqueue({"id": "12345678-1234-4123-8123-123456789abc"}, 1, "v")
    journal.enqueue(driver, 2, "v")
    journal.path.write_text(json.dumps(["junk", *journal.pending()]))
    assert profiles.flush(journal, store, lambda route, body: 200) == 3
    assert journal.pending() == []
    for index in range(3):
        journal.enqueue(driver, index, "v")
    answers = iter([200, 200, 429])
    assert profiles.flush(journal, store, lambda route, body: next(answers)) == 1
    assert len(journal.pending()) == 2


@pytest.mark.parametrize(("status", "result"), [
    (200, "sent"), (400, "rejected"), (401, "rejected"), (403, "rejected"), (409, "rejected"),
    (413, "rejected"), (422, "rejected"),
    # A service without the sign-in routes yet: keep the record for later.
    (404, "retry"), (405, "retry"),
    (408, "retry"), (429, "retry"), (503, "retry"), (-1, "retry"), (302, "retry"),
])
def test_server_answers_map_to_delivery_outcomes(status, result):
    assert profiles.outcome(status) == result


def test_versions_name_the_platform():
    assert profiles.version_label("5.7.1", "win32") == "5.7.1-windows"
    assert profiles.version_label("5.7.1", "darwin") == "5.7.1-macos"
    assert profiles.version_label("5.7.1", "linux") == "5.7.1-desktop"


def test_the_dashboard_sees_the_open_profile_only_when_one_was_opened(monkeypatch):
    monkeypatch.delenv("PITWALL_PROFILE_NAME", raising=False)
    monkeypatch.delenv("PITWALL_PROFILE_SWITCH", raising=False)
    assert profiles.active() == {"active": False}
    monkeypatch.setenv("PITWALL_PROFILE_NAME", "Driver")
    monkeypatch.setenv("PITWALL_PROFILE_SWITCH", "restart")
    assert profiles.active() == {"active": True, "name": "Driver", "switch": "restart"}
    monkeypatch.setenv("PITWALL_PROFILE_SWITCH", "anything")
    assert profiles.active() == {"active": False}
