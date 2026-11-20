"""Choosing a Bilibili stream by the stream_type setting."""

from DMR.LiveAPI.bilibili import _ordered_stream_types, bilibili


def make_api(urls):
    api = bilibili.__new__(bilibili)  # the real constructor queries the network
    api.rid = "24486091"
    api.get_stream_urls = lambda **kwargs: urls
    return api


def url(qn, stream_type):
    return {"quality": qn, "stream_cdn": "cdn", "stream_type": stream_type,
            "stream_url": f"{stream_type}-{qn}"}


ROOM = [url(25000, "fmp4-hevc"), url(10000, "flv-avc"), url(10000, "fmp4-avc")]


def test_auto_keeps_the_highest_quality():
    assert make_api(ROOM).get_stream_url() == "fmp4-hevc-25000"


def test_avc_prefers_flv_avc_even_at_lower_quality():
    assert make_api(ROOM).get_stream_url(stream_type="avc") == "flv-avc-10000"


def test_avc_without_avc_falls_back_like_auto_not_to_flv_hevc():
    # FLV-HEVC goes to StreamGears, which cannot record it reliably.
    room = [url(25000, "flv-hevc"), url(25000, "fmp4-hevc")]
    assert make_api(room).get_stream_url(stream_type="avc") == "fmp4-hevc-25000"


def test_fallback_never_lists_unsafe_flv_unless_asked():
    for pref in ("avc", "fmp4", "flv-avc", "fmp4-hevc"):
        order = _ordered_stream_types(pref)
        assert "flv-av1" not in order
    assert "flv-hevc" in _ordered_stream_types("hevc")
    assert _ordered_stream_types("avc_only") == ["flv-avc", "fmp4-avc", "ts-avc"]
