"""A failed live-status query must not be treated as the stream ending."""

from types import SimpleNamespace

import pytest

from DMR.Downloader.stream_downloader import StreamState, state_when_onair_unknown
from DMR.LiveAPI import LiveAPI
from DMR.LiveAPI.bilibili import bilibili


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        # Recording keeps going; the stream did not end.
        (StreamState.LIVE, StreamState.LIVE),
        (StreamState.LIVE_START, StreamState.LIVE),
        # Not live: an unknown status does not start a recording.
        (StreamState.OFFLINE, StreamState.OFFLINE),
        # Waiting to confirm the end: an unknown status does not end the session.
        (StreamState.STOPPING, StreamState.STOPPING),
        (StreamState.REPLAY, StreamState.REPLAY),
        (StreamState.REPLAY_START, StreamState.REPLAY),
    ],
)
def test_unknown_status_keeps_the_state(state, expected):
    assert state_when_onair_unknown(state) == expected


def test_query_error_returns_none_not_false():
    def fail():
        raise ConnectionError("TLS EOF")

    api = LiveAPI.__new__(LiveAPI)  # the real constructor queries the network
    api.platform, api.rid = "bilibili", "24486091"
    api.api_class = SimpleNamespace(onair=fail)
    assert api.Onair() is None


@pytest.mark.parametrize(
    ("resp", "expected"),
    [
        ({"code": 0, "data": {"live_status": 1}}, True),
        ({"code": 0, "data": {"live_status": 0}}, False),
        ({"code": 0, "data": {"live_status": 2}}, False),
        # The API itself reports an error: unknown, not offline.
        ({"code": -412, "message": "请求被拦截"}, None),
    ],
)
def test_bilibili_onair_distinguishes_api_errors(resp, expected):
    api = bilibili.__new__(bilibili)  # the real constructor queries the network
    api.rid = "24486091"
    api._get_response = lambda: resp
    assert api.onair() is expected
