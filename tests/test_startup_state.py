import pytest

from DMR.Downloader.stream_downloader import StreamState, startup_state


@pytest.mark.parametrize(
    ("onair", "trust", "in_window", "expected"),
    [
        (False, True, True, StreamState.OFFLINE),
        (True, True, True, StreamState.LIVE_START),
        # Restarting outside the record window must not start recording.
        (True, True, False, StreamState.REPLAY_START),
        (True, False, True, StreamState.REPLAY_START),
        (True, False, False, StreamState.REPLAY_START),
    ],
)
def test_startup_state_respects_trust_and_record_window(onair, trust, in_window, expected):
    assert startup_state(onair, trust, in_window) == expected
