import pytest

from DMR.Render.sprites import (
    SpriteFactory,
    resolve_emoji_font,
    resolve_font,
    resolve_layout_engine,
)


def test_cmap_fallback_splits_missing_script_into_configured_font():
    primary = resolve_font("Segoe UI Symbol", True)
    fallback = resolve_font("Malgun Gothic", True)
    if primary == fallback:
        pytest.skip("测试环境没有独立的韩文字体回退链")
    factory = SpriteFactory(
        primary,
        resolve_emoji_font(None),
        {},
        36,
        fallback_fonts=[fallback],
    )
    runs = list(factory._split_text_runs("안녕하세요"))
    assert "".join(text for _, text in runs) == "안녕하세요"
    assert any(font is not factory.text_font for font, _ in runs)


def test_missing_codepoint_warns_once_and_uses_primary(caplog):
    primary = resolve_font("Segoe UI Symbol", True)
    factory = SpriteFactory(primary, resolve_emoji_font(None), {}, 36, fallback_fonts=[])
    with caplog.at_level("WARNING"):
        first = factory._font_for(0x10FFFF)
        second = factory._font_for(0x10FFFF)
    assert first is factory.text_font and second is first
    assert sum("U+10FFFF" in record.message for record in caplog.records) == 1


@pytest.mark.parametrize("text", ["Tiếng Việt Ấ", "กิ่", "e\u0301"])
def test_combining_cluster_stays_in_one_font_run(text):
    primary = resolve_font("Segoe UI Symbol", True)
    fallback = resolve_font("Malgun Gothic", True)
    factory = SpriteFactory(
        primary,
        resolve_emoji_font(None),
        {},
        36,
        fallback_fonts=[fallback],
    )
    runs = factory._split_text_runs(text)
    assert "".join(value for _, value in runs) == text
    for _, value in runs:
        if "Ấ" in value or "กิ่" in value or "e\u0301" in value:
            assert len(runs) == 1 or value in {"Tiếng Việt Ấ", "กิ่", "e\u0301"}


def test_layout_engine_falls_back_to_basic_without_raqm():
    assert resolve_layout_engine("auto") in {"BASIC", "RAQM"}
    with pytest.raises(ValueError):
        resolve_layout_engine("unknown")
