from pitwall.audio import AudioService


def test_wake_phrase_matches_only_at_transcript_start():
    phrases = ["mark", "hey mark", "mark radio"]

    matched, command, phrase = AudioService.extract_wake_command(
        "Mark, what is the target lap?",
        phrases,
    )
    assert matched is True
    assert command == "what is the target lap"
    assert phrase == "mark"

    matched, command, phrase = AudioService.extract_wake_command(
        "Hey Mark — give me the top three best laps.",
        phrases,
    )
    assert matched is True
    assert command == "give me the top three best laps"
    assert phrase == "hey mark"

    matched, _, _ = AudioService.extract_wake_command(
        "The commentator said Mark was quick.",
        phrases,
    )
    assert matched is False


def test_phrase_only_arms_follow_up():
    matched, command, phrase = AudioService.extract_wake_command(
        "Mark.",
        ["mark"],
    )
    assert matched is True
    assert command == ""
    assert phrase == "mark"
