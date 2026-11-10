from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from DMR.utils import replace_keywords
from DMR.utils.text_width import TextMeasurer

from .emoji_width import EmojiWidthMeasurer
from .layout_models import (
    LayoutState,
    RenderPlan,
    TRACK_R2L,
    TRACK_SLIDE_LEFT,
    TRACK_SLIDE_RIGHT,
    EVENT_TRACKS,
)
from .resolution import resolve_render_canvas


SUPPORTED_SCHEMA = 1
logger = logging.getLogger(__name__)

PANEL_ENTER_DURATION = 0.5
PANEL_EXIT_DURATION = 0.5
PANEL_PUSH_DURATION = 0.3
PANEL_STACK_GAP = 10.0

# Fallback panel policies used only when a track omits its own ranges.
# Ranges are left-closed and right-open; the last range has no upper bound.
DEFAULT_PANEL_PRICE_COLORS = (
    {"min_cny": 0, "max_cny": 1, "top_color": "#EEF6F8", "bottom_color": "#607D8B"},
    {"min_cny": 1, "max_cny": 10, "top_color": "#DBFFFD", "bottom_color": "#3E8E95"},
    {"min_cny": 10, "max_cny": 50, "top_color": "#E2F0FF", "bottom_color": "#4C78C2"},
    {"min_cny": 50, "max_cny": 100, "top_color": "#EDE7FF", "bottom_color": "#7656B5"},
    {"min_cny": 100, "max_cny": 1000, "top_color": "#FFE8F1", "bottom_color": "#C65386"},
    {"min_cny": 1000, "max_cny": None, "top_color": "#FFF2C7", "bottom_color": "#B77A16"},
)
DEFAULT_PANEL_PRICE_DURATIONS = (
    {"min_price_cny": 0, "max_price_cny": 30, "seconds": 30},
    {"min_price_cny": 30, "max_price_cny": 50, "seconds": 60},
    {"min_price_cny": 50, "max_price_cny": 100, "seconds": 120},
    {"min_price_cny": 100, "max_price_cny": None, "seconds": 180},
)


@dataclass
class LayoutCompileResult:
    render_plan_path: str
    next_state: LayoutState
    event_count: int = 0
    dropped_count: int = 0
    track_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    unrouted_dropped_count: int = 0
    cleanup_paths: list[str] = field(default_factory=list)

    def cleanup(self):
        for path in self.cleanup_paths:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


def _build_vip_map(vip_users) -> dict:
    result = {}
    if isinstance(vip_users, dict):
        for uid, color in vip_users.items():
            result[str(uid)] = str(color) if color else None
    elif isinstance(vip_users, (list, tuple)):
        for item in vip_users:
            if isinstance(item, dict) and item.get("uid") is not None:
                result[str(item["uid"])] = (
                    str(item.get("pill_color")) if item.get("pill_color") else None
                )
            elif isinstance(item, (list, tuple)) and item:
                result[str(item[0])] = (
                    str(item[1]) if len(item) > 1 and item[1] else None
                )
            else:
                result[str(item)] = None
    return result


def _compile_patterns(raw) -> list[re.Pattern]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [re.compile(str(value)) for value in raw]


def _hex_color(value, default="#FFFFFF") -> str:
    raw = str(value or default).lstrip("#")
    if len(raw) != 6:
        raw = str(default).lstrip("#")
    return "#" + raw.upper()


def _decimal_price(value):
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not price.is_finite() or price < 0:
        return None
    return price


def _select_price_range(price_cny, configured, fallback):
    raw_ranges = configured if isinstance(configured, (list, tuple)) and configured else fallback
    parsed = []
    for index, price_range in enumerate(raw_ranges):
        if not isinstance(price_range, dict):
            continue
        lower_raw = price_range.get("min_price_cny", price_range.get("min_cny"))
        upper_raw = price_range.get("max_price_cny", price_range.get("max_cny"))
        try:
            lower = Decimal(str(lower_raw if lower_raw is not None else 0))
            upper = Decimal(str(upper_raw)) if upper_raw is not None else None
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not lower.is_finite() or (upper is not None and not upper.is_finite()):
            continue
        if upper is not None and upper <= lower:
            continue
        parsed.append((lower, upper, index, price_range))
    if not parsed:
        return None
    parsed.sort(key=lambda item: (item[0], item[2]))
    price = _decimal_price(price_cny)
    if price is None:
        return parsed[0][3]
    for lower, upper, _, price_range in parsed:
        if price >= lower and (upper is None or price < upper):
            return price_range
    # An uncovered value deliberately falls back to the lowest tier.
    return parsed[0][3]


def _panel_duration(record: dict, event_cfg: dict, track_cfg: dict) -> float:
    if event_cfg.get("use_event_duration", False):
        try:
            event_duration = float(
                record.get("platform_duration", record.get("duration"))
            )
            if math.isfinite(event_duration) and event_duration > 0:
                return event_duration
        except (TypeError, ValueError):
            pass
    # Compatibility with the former top-level gift/member ``duration``.
    if event_cfg.get("duration") is not None:
        try:
            fixed_duration = float(event_cfg["duration"])
            if math.isfinite(fixed_duration) and fixed_duration > 0:
                return fixed_duration
        except (TypeError, ValueError):
            pass
    selected = _select_price_range(
        record.get("price_cny"),
        track_cfg.get("price_duration_ranges"),
        DEFAULT_PANEL_PRICE_DURATIONS,
    ) or DEFAULT_PANEL_PRICE_DURATIONS[0]
    try:
        seconds = float(selected.get("seconds"))
    except (TypeError, ValueError):
        seconds = float(DEFAULT_PANEL_PRICE_DURATIONS[0]["seconds"])
    return max(0.1, seconds if math.isfinite(seconds) else 30.0)


def _panel_colors(record: dict, event_cfg: dict, track_cfg: dict) -> tuple[str, str, str, str]:
    selected = _select_price_range(
        record.get("price_cny"),
        track_cfg.get("price_color_ranges"),
        DEFAULT_PANEL_PRICE_COLORS,
    ) or DEFAULT_PANEL_PRICE_COLORS[0]
    price_top = _hex_color(selected.get("top_color"), "#EEF6F8")
    price_bottom = _hex_color(selected.get("bottom_color"), "#607D8B")
    default_name = _hex_color(track_cfg.get("name_color"), "#666666")
    default_content = _hex_color(track_cfg.get("content_color"), "#FFFFFF")
    if not event_cfg.get("use_event_background_colors", False):
        return price_top, price_bottom, default_name, default_content
    return (
        _hex_color(record.get("background_color"), price_top),
        _hex_color(record.get("background_bottom_color"), price_bottom),
        default_name,
        default_content,
    )


def _event_is_enabled(record: dict, event_cfg: dict) -> bool:
    if event_cfg.get("enable", True) is False:
        return False
    minimum = _decimal_price(event_cfg.get("min_price_cny", 0))
    if minimum is None or minimum <= 0:
        return True
    price = _decimal_price(record.get("price_cny"))
    return price is not None and price >= minimum


def _event_config(config: dict, track_cfg: dict, event_type: str) -> dict:
    """Read the track-first config while accepting legacy top-level overrides."""
    result = copy.deepcopy(track_cfg.get(event_type) or {})
    legacy = config.get(event_type)
    if isinstance(legacy, dict):
        result.update(copy.deepcopy(legacy))
    return result


def _panel_width(video_width, value) -> int:
    """Resolve a slide track width from either ``NNpx`` or ``NN%``."""
    raw = str(value if value is not None else "25%").strip().lower()
    try:
        if raw.endswith("%"):
            width = float(video_width or 0) * float(raw[:-1]) / 100.0
        elif raw.endswith("px"):
            width = float(raw[:-2])
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(
            f"slide width 仅支持百分比或像素，例如 25% 或 270px，当前值: {value!r}"
        )
    if not math.isfinite(width) or width <= 0:
        raise ValueError(f"slide width 必须大于 0，当前值: {value!r}")
    video_width = max(1.0, float(video_width or 1))
    return int(round(min(video_width, width)))


def _pixels(value, default=0.0) -> float:
    if value is None or value == "":
        value = default
    if isinstance(value, str):
        value = value.strip()
        if value.lower().endswith("px"):
            value = value[:-2].strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _positive_count(value) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _quantity_suffix(value, unit="") -> str:
    count = _positive_count(value)
    return f" ×{count}{unit or ''}"


def merge_gift_records(records, *, mode="total", window_seconds=10.0):
    """Merge gifts by user/name, preferring platform combo identity."""
    ordered = sorted(
        records,
        key=lambda item: (
            float(item.get("received_at", 0) or 0),
            item.get("_line", 0),
        ),
    )
    result = []
    groups = {}
    seen_tids = set()
    for record in ordered:
        if record.get("type") != "gift":
            result.append(record)
            continue
        tid = record.get("tid")
        if tid not in (None, ""):
            if tid in seen_tids:
                continue
            seen_tids.add(tid)
        uid = record.get("uid") or record.get("uname") or ""
        name = record.get("gift_name") or ""
        combo_id = record.get("combo_id")
        key = (uid, name, combo_id) if combo_id else (uid, name)
        when = float(record.get("received_at", 0) or 0)
        previous = groups.get(key)
        if previous is None or (
            not combo_id and when - previous["_last_time"] > float(window_seconds)
        ):
            merged = dict(record)
            merged["gift_count"] = _positive_count(record.get("gift_count"))
            merged["gift_states"] = [dict(record)]
            merged["_last_time"] = when
            groups[key] = merged
            result.append(merged)
            continue
        count = _positive_count(record.get("gift_count"))
        merged["gift_count"] += count
        merged["gift_states"].append(dict(record))
        merged["_last_time"] = when
        merged["price_cny"] = float(merged.get("price_cny") or 0) + float(
            record.get("price_cny") or 0
        )
    for record in result:
        record.pop("_last_time", None)
        if record.get("type") == "gift" and mode == "total":
            record.pop("gift_states", None)
        elif record.get("type") == "gift":
            record["combo_mode"] = "live"
    return result


def _optional_quantity_suffix(value, unit="") -> str:
    return "" if value is None else _quantity_suffix(value, unit)


def _format_cny(value) -> str:
    if value is None or value == "":
        return ""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if not amount.is_finite():
        return ""
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = format(amount.normalize(), "f")
    if text == "-0":
        text = "0"
    return f"￥{text}"


def _panel_price_text(record: dict) -> str:
    # Every paid panel displays the CNY total for this event. Gift/member
    # quantities remain in the content line and are not repeated in the corner.
    return _format_cny(record.get("price_cny"))


def _path_end(event: dict) -> float:
    path = event.get("path") or []
    return float(path[-1][0]) if path else -math.inf


def _path_start(event: dict) -> float:
    path = event.get("path") or []
    return float(path[0][0]) if path else math.inf


def _position_at(path: list, timestamp: float) -> tuple[float, float]:
    if not path:
        return 0.0, 0.0
    timestamp = float(timestamp)
    if timestamp <= float(path[0][0]):
        return float(path[0][1]), float(path[0][2])
    for first, second in zip(path, path[1:]):
        t0, t1 = float(first[0]), float(second[0])
        if timestamp <= t1:
            if t1 <= t0:
                return float(second[1]), float(second[2])
            progress = (timestamp - t0) / (t1 - t0)
            return (
                float(first[1]) + (float(second[1]) - float(first[1])) * progress,
                float(first[2]) + (float(second[2]) - float(first[2])) * progress,
            )
    return float(path[-1][1]), float(path[-1][2])


def _append_point(path: list, point: list) -> None:
    point = [float(point[0]), float(point[1]), float(point[2])]
    # Paths use epoch seconds (~1.8e9).  math.isclose's default relative
    # tolerance would therefore treat timestamps up to ~1.8 seconds apart as
    # equal, collapsing the entry/push/hold/exit boundary points into one long
    # ASS move.  Timeline equality must be absolute only.
    if path and math.isclose(
        float(path[-1][0]),
        point[0],
        rel_tol=0.0,
        abs_tol=1e-7,
    ):
        path[-1] = point
    elif not path or point[0] > float(path[-1][0]):
        path.append(point)


def _cut_path(path: list, timestamp: float) -> list:
    x, y = _position_at(path, timestamp)
    result = [
        [float(point[0]), float(point[1]), float(point[2])]
        for point in path
        if float(point[0]) < float(timestamp)
    ]
    _append_point(result, [timestamp, x, y])
    return result


def _clip_path(path: list, start: float, end: float) -> list:
    if not path or _path_end({"path": path}) <= start or _path_start({"path": path}) >= end:
        return []
    visible_start = max(float(start), float(path[0][0]))
    visible_end = min(float(end), float(path[-1][0]))
    if visible_end <= visible_start:
        return []
    x0, y0 = _position_at(path, visible_start)
    result = [[visible_start - start, x0, y0]]
    for point in path:
        timestamp = float(point[0])
        if visible_start < timestamp < visible_end:
            _append_point(
                result,
                [timestamp - start, float(point[1]), float(point[2])],
            )
    x1, y1 = _position_at(path, visible_end)
    _append_point(result, [visible_end - start, x1, y1])
    return result


class LayoutCompiler:
    """Compile typed JSONL events into a RenderPlan and the next LayoutState."""

    def __init__(
        self,
        layout_config: dict = None,
        engine: str | None = None,
        engine_config: dict = None,
    ):
        self.config = layout_config or {}
        from . import get_engine, DEFAULT_ENGINE

        self.engine = engine if engine is not None else DEFAULT_ENGINE
        self.engine_spec = get_engine(self.engine)
        self.engine_config = engine_config or {}

    def compile(
        self,
        video,
        events_path: str,
        previous_state: LayoutState | dict | None = None,
        work_dir: str = None,
    ) -> LayoutCompileResult:
        header, records = self._read_events(events_path)
        source_resolution = resolve_render_canvas(
            getattr(video, "resolution", None),
            rescale_to_1080p=False,
        )
        raw_segment_start = float(header["segment_start_time"])
        duration = max(0.0, float(getattr(video, "duration", 0) or 0))

        if isinstance(previous_state, LayoutState):
            old_state = LayoutState.from_dict(
                previous_state.to_dict(),
                raw_segment_start,
            )
        else:
            old_state = LayoutState.from_dict(
                previous_state,
                raw_segment_start,
            )
        current_canvas = resolve_render_canvas(
            source_resolution,
            rescale_to_1080p=bool(
                self.engine_config.get("rescale_to_1080p", False)
            ),
            rescale_orientation=self.engine_config.get(
                "rescale_orientation", "auto"
            ),
            previous_canvas=old_state.canvas_resolution,
            previous_source_resolution=old_state.source_resolution,
        )
        width, height = current_canvas
        if (
            previous_state is not None
            and old_state.canvas_resolution != current_canvas
        ):
            logger.info(
                "布局画布变化 %s → %s，放弃上一段布局状态",
                old_state.canvas_resolution,
                current_canvas,
            )
            old_state = LayoutState(
                at=old_state.at,
                canvas_resolution=current_canvas,
                source_resolution=source_resolution,
            )

        # The downloader records a wall-clock start in each JSONL, while render
        # uses the video's packet timeline. Some containers report a shorter
        # format duration than their real packet timeline, so the next JSONL
        # may start slightly before the previous LayoutState ends. A rendered
        # live timeline must never run backwards: collapse only this overlap,
        # but preserve a positive real-world gap between two segments.
        segment_start = raw_segment_start
        if previous_state is not None and old_state.at > segment_start:
            segment_start = float(old_state.at)
        segment_time_shift = segment_start - raw_segment_start
        segment_end = segment_start + duration

        # Stacked panels must be retained from the checkpoint time, including
        # panels which expire in a recording gap.  Their exits still change the
        # positions of surviving panels even though no frames are emitted for
        # that gap.  Other tracks have no persistent stack state and can be
        # filtered directly at the new segment boundary.
        state_events = [
            copy.deepcopy(event)
            for event in old_state.events
            if _path_end(event) > (
                old_state.at
                if event.get("track") in (TRACK_SLIDE_LEFT, TRACK_SLIDE_RIGHT)
                else segment_start
            )
        ]
        # Checkpoints may come from another backend (GPU failure -> fallback).
        # Geometry/identity persist, but carried panels must use this backend's
        # drawing capability instead of retaining the previous engine's mode.
        for event in state_events:
            if (event.get("visual") or {}).get("type") == "panel":
                event["mode"] = "raster" if self.engine_spec.panel_raster else "ass"

        r2l_render = self.config.get(TRACK_R2L) or {}
        slide_left_render = self.config.get(TRACK_SLIDE_LEFT) or {}
        slide_right_render = self.config.get(TRACK_SLIDE_RIGHT) or {}
        dan_render = _event_config(self.config, r2l_render, "danmaku")
        gift_render = _event_config(self.config, slide_right_render, "gift")
        sc_render = _event_config(self.config, slide_left_render, "superchat")
        member_render = _event_config(self.config, slide_right_render, "member")
        event_configs = {
            "danmaku": dan_render,
            "gift": gift_render,
            "superchat": sc_render,
            "member": member_render,
        }

        font = dan_render.get("font", "Microsoft YaHei")
        fontsize = int(dan_render.get("fontsize", 36) or 36)
        text_measurer = TextMeasurer(font, fontsize, bold=True)
        emoji_measurer = EmojiWidthMeasurer(
            font=font,
            fontsize=fontsize,
            platform=header.get("platform", ""),
            emoji_pack=self.engine_config.get("emoji_pack"),
            emoji_font=self.engine_config.get("emoji_font"),
        )
        style = {
            "font": font,
            "size": fontsize,
            "bold": bool(dan_render.get("bold", True)),
            "color": "#FFFFFF",
            "opacity": float(dan_render.get("opacity", 0.8) or 0.8),
            "outline": _pixels(dan_render.get("outline_size"), 1.0),
            "outline_color": _hex_color(
                dan_render.get("outline_color", "000000"), "#000000"
            ),
        }

        vip_map = _build_vip_map(dan_render.get("vip_users"))
        keyword_patterns = _compile_patterns(dan_render.get("filter_keywords"))
        username_patterns = _compile_patterns(dan_render.get("filter_users"))
        max_length = self._parse_max_length(dan_render.get("max_width"))
        raw_includes = self.config.get("include_events")
        included_events = (
            {str(value) for value in raw_includes}
            if isinstance(raw_includes, (list, tuple, set))
            else set(EVENT_TRACKS)
        )

        track_stats = {
            TRACK_R2L: {"accepted": 0, "dropped": 0},
            TRACK_SLIDE_LEFT: {"accepted": 0, "dropped": 0},
            TRACK_SLIDE_RIGHT: {"accepted": 0, "dropped": 0},
        }
        unrouted_dropped = 0

        def mark_accepted(track):
            track_stats[track]["accepted"] += 1

        def mark_dropped(track):
            nonlocal unrouted_dropped
            if track in track_stats:
                track_stats[track]["dropped"] += 1
            else:
                unrouted_dropped += 1

        new_r2l = []
        new_panels = {
            TRACK_SLIDE_LEFT: [],
            TRACK_SLIDE_RIGHT: [],
        }
        ordered_records = sorted(
            records,
            key=lambda item: (float(item.get("received_at", 0) or 0), item["_line"]),
        )
        gift_cfg = event_configs.get("gift") or {}
        ordered_records = merge_gift_records(
            ordered_records,
            mode=str(gift_cfg.get("combo_mode", "total") or "total"),
            window_seconds=float(gift_cfg.get("combo_window_seconds", 10) or 10),
        )

        for record in ordered_records:
            event_type = record.get("type")
            event_track = EVENT_TRACKS.get(event_type)
            if event_type not in included_events or event_track is None:
                mark_dropped(event_track)
                continue
            event_cfg = event_configs.get(event_type) or {}
            if not _event_is_enabled(record, event_cfg):
                mark_dropped(event_track)
                continue
            try:
                # Keep the event's segment-local time unchanged when an
                # overlapping segment origin was moved forward.
                start_at = float(record["received_at"]) + segment_time_shift
            except (KeyError, TypeError, ValueError):
                mark_dropped(event_track)
                continue
            if duration > 0 and start_at >= segment_end + 1:
                mark_dropped(event_track)
                continue

            event_id = self._event_id(video, events_path, record["_line"])
            if event_type == "danmaku":
                prepared = self._prepare_line(
                    record=record,
                    event_id=event_id,
                    start_at=start_at,
                    width=width,
                    height=height,
                    dan_render=dan_render,
                    style=style,
                    vip_map=vip_map,
                    keyword_patterns=keyword_patterns,
                    username_patterns=username_patterns,
                    max_length=max_length,
                    text_measurer=text_measurer,
                    emoji_measurer=emoji_measurer,
                )
                if prepared is None:
                    mark_dropped(event_track)
                    continue
                new_r2l.append(prepared)
                continue

            if event_type == "gift":
                display_record = dict(record)
                display_record["gift_count_suffix"] = _quantity_suffix(
                    record.get("gift_count")
                )
                content = self._format_template(
                    gift_render.get("template"),
                    display_record,
                    (
                        f"送出 {record.get('gift_name', '')}"
                        f"{display_record['gift_count_suffix']}"
                    ),
                )
                panel = self._prepare_panel(
                    event_id,
                    event_track,
                    start_at,
                    _panel_duration(record, gift_render, slide_right_render),
                    record.get("uname", ""),
                    content,
                    width,
                    height,
                    gift_render,
                    slide_right_render,
                    panel_class="gift",
                    record=record,
                    emoji_measurer=emoji_measurer,
                )
                new_panels[event_track].append(panel)
                mark_accepted(event_track)
                continue

            if event_type == "member":
                display_record = dict(record)
                display_record["member_count_suffix"] = _optional_quantity_suffix(
                    record.get("member_count"),
                    record.get("member_unit", ""),
                )
                content = self._format_template(
                    member_render.get("template"),
                    display_record,
                    (
                        f"开通 {record.get('member_name', '')}"
                        f"{display_record['member_count_suffix']}"
                    ),
                )
                panel = self._prepare_panel(
                    event_id,
                    event_track,
                    start_at,
                    _panel_duration(record, member_render, slide_right_render),
                    record.get("uname", ""),
                    content,
                    width,
                    height,
                    member_render,
                    slide_right_render,
                    panel_class="member",
                    record=record,
                    emoji_measurer=emoji_measurer,
                )
                new_panels[event_track].append(panel)
                mark_accepted(event_track)
                continue

            if event_type == "superchat":
                panel = self._prepare_panel(
                    event_id,
                    event_track,
                    start_at,
                    _panel_duration(record, sc_render, slide_left_render),
                    record.get("uname", ""),
                    str(record.get("content", "") or ""),
                    width,
                    height,
                    sc_render,
                    slide_left_render,
                    panel_class="superchat",
                    record=record,
                    emoji_measurer=emoji_measurer,
                )
                new_panels[event_track].append(panel)
                mark_accepted(event_track)
                continue

            mark_dropped(event_track)

        active_r2l = [
            event for event in state_events
            if event.get("track") == TRACK_R2L
        ]
        for event in sorted(new_r2l, key=lambda item: _path_start(item)):
            if self._place_r2l(
                event,
                active_r2l,
                width,
                height,
                dan_render,
                r2l_render,
            ):
                active_r2l.append(event)
                state_events.append(event)
                mark_accepted(TRACK_R2L)
            else:
                mark_dropped(TRACK_R2L)

        track_configs = {
            TRACK_SLIDE_LEFT: slide_left_render,
            TRACK_SLIDE_RIGHT: slide_right_render,
        }
        next_track_orders = {}
        for track, track_render in track_configs.items():
            existing = [event for event in state_events if event.get("track") == track]
            combined, track_order = self._layout_stacked_panels(
                track=track,
                existing=existing,
                newcomers=new_panels[track],
                state_at=old_state.at,
                initial_order=old_state.track_orders.get(track),
                segment_start=segment_start,
                segment_end=segment_end,
                width=width,
                height=height,
                track_cfg=track_render,
            )
            next_track_orders[track] = track_order
            state_events = [event for event in state_events if event.get("track") != track]
            state_events.extend(combined)

        plan_events = []
        for state_event in state_events:
            local_path = _clip_path(state_event.get("path") or [], segment_start, segment_end)
            if len(local_path) < 2:
                continue
            plan_event = {
                key: copy.deepcopy(value)
                for key, value in state_event.items()
                if not key.startswith("_")
            }
            plan_event["path"] = local_path
            plan_events.append(plan_event)

        next_events = [
            copy.deepcopy(event)
            for event in state_events
            if _path_end(event) > segment_end
        ]
        live_ids = {str(event.get("id")) for event in next_events}
        next_state = LayoutState(
            at=segment_end,
            events=next_events,
            track_orders={
                track: [
                    event_id
                    for event_id in order
                    if event_id in live_ids
                ]
                for track, order in next_track_orders.items()
            },
            canvas_resolution=current_canvas,
            source_resolution=source_resolution,
        )
        plan = RenderPlan(
            duration=duration, events=plan_events, platform=header.get("platform")
        )
        accepted = sum(stats["accepted"] for stats in track_stats.values())
        dropped = (
            sum(stats["dropped"] for stats in track_stats.values())
            + unrouted_dropped
        )

        os.makedirs(work_dir or tempfile.gettempdir(), exist_ok=True)
        fd, plan_path = tempfile.mkstemp(
            prefix="render_plan_",
            suffix=".json",
            dir=work_dir or None,
        )
        os.close(fd)
        plan.save(plan_path)
        return LayoutCompileResult(
            render_plan_path=plan_path,
            next_state=next_state,
            event_count=accepted,
            dropped_count=dropped,
            track_stats=track_stats,
            unrouted_dropped_count=unrouted_dropped,
            cleanup_paths=[plan_path],
        )

    def _prepare_line(
        self,
        *,
        record,
        event_id,
        start_at,
        width,
        height,
        dan_render,
        style,
        vip_map,
        keyword_patterns,
        username_patterns,
        max_length,
        text_measurer,
        emoji_measurer,
    ):
        content = str(record.get("content", "") or "")
        uname = str(record.get("uname", "") or "")
        if record.get("type") == "entry":
            content = content or f"{uname} 进入直播间"
        if any(pattern.search(content) for pattern in keyword_patterns):
            return None
        if any(pattern.fullmatch(uname) for pattern in username_patterns):
            return None

        template = dan_render.get("template")
        text = self._format_template(template, record, content)
        pill = None
        uid = str(record.get("uid", ""))
        if uid in vip_map:
            text = f"{uname}:{content}"
            pill = {
                "color": _hex_color(vip_map[uid] or record.get("color") or "#FFC0CB"),
                "padding": int(dan_render.get("vip_padding", 6) or 0),
                "border": int(dan_render.get("vip_border", 3) or 1),
            }

        emoticon = record.get("emoticon") if isinstance(record.get("emoticon"), dict) else None
        # emoji_dmrender 中所有 VIP 药丸统一由 Pillow 绘制。否则纯文字 VIP
        # 会走 libass，而带 Emoji 的 VIP 会走 Pillow，产生两套圆角、留白、
        # 描边透明度和文字基线。普通 dmrender 没有 Pillow 合成阶段，仍走 ASS。
        is_raster = (
            self.engine_spec.raster and pill is not None
        ) or emoji_measurer.is_special(text, emoticon)
        measure = (
            (lambda value: emoji_measurer.measure(value, emoticon))
            if is_raster
            else text_measurer.measure
        )
        text = self._truncate(text, max_length, measure, width)
        if not text:
            return None

        content_width = max(1, int(measure(text)))
        fontsize = int(dan_render.get("fontsize", 36) or 36)
        if is_raster:
            content_ascent, content_descent = emoji_measurer.vertical_metrics(
                text, emoticon
            )
            content_height = content_ascent + content_descent
        else:
            content_ascent, content_descent = fontsize, 0
            content_height = fontsize
        line_height = max(fontsize, content_height)
        outer_width = content_width
        if pill:
            pill_height = content_height + 2 * pill["padding"]
            pad_x = pill_height / 2
            outer_width += int(round(2 * pad_x))
            line_height = max(line_height, pill_height)
        visual = {
            "type": "line",
            "text": text,
            "style": {
                **style,
                "color": _hex_color(record.get("color"), "#FFFFFF"),
            },
        }
        if is_raster:
            visual["images"] = emoji_measurer.resolve_images(text, emoticon)
            visual["text_box"] = {
                "ascent": content_ascent,
                "descent": content_descent,
            }
        if pill:
            visual["pill"] = pill
        return {
            "id": event_id,
            "track": TRACK_R2L,
            "mode": "raster" if is_raster else "ass",
            "bounds": {"width": outer_width, "height": line_height},
            "path": [[float(start_at), 0.0, 0.0]],
            "visual": visual,
        }

    def _place_r2l(
        self,
        event,
        active,
        width,
        height,
        event_cfg,
        track_cfg,
    ) -> bool:
        fontsize = int(event_cfg.get("fontsize", 36) or 36)
        vertical_gap = _pixels(track_cfg.get("vertical_gap"), 6)
        horizontal_gap = _pixels(track_cfg.get("horizontal_gap"), 60)
        margin_top = _pixels(track_cfg.get("margin_top"), 20)
        vertical_ratio = float(
            track_cfg.get("vertical_ratio", 0.4) or 0.4
        )
        ntracks = max(
            1,
            int(
                ((height - margin_top) * vertical_ratio)
                / max(1.0, fontsize + vertical_gap)
            ),
        )
        speed = track_cfg.get("speed")
        if speed is None:
            duration = float(
                track_cfg.get("screen_duration", track_cfg.get("duration", 16))
            )
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("r2l.screen_duration must be positive and finite")
            # Legacy duration denotes travel across a 1920px landscape canvas.
            speed = 1920.0 / duration
        speed = float(speed)
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("r2l.speed must be positive and finite")
        start_at = float(event["path"][0][0])
        render_width = float(event["bounds"]["width"])

        best_lane = 0
        best_gap = -math.inf
        for lane in range(ntracks):
            y = margin_top + lane * (fontsize + vertical_gap)
            lane_events = [
                item
                for item in active
                if item.get("track") == TRACK_R2L
                and math.isclose(float((item.get("path") or [[0, 0, -999]])[0][2]), y, abs_tol=0.5)
                and _path_end(item) > start_at
            ]
            if not lane_events:
                gap = math.inf
            else:
                tail = max(lane_events, key=_path_start)
                tail_x, _ = _position_at(tail["path"], start_at)
                gap = width - (tail_x + float(tail["bounds"]["width"]))
            if gap >= horizontal_gap:
                best_lane, best_gap = lane, gap
                break
            if gap > best_gap:
                best_lane, best_gap = lane, gap

        if horizontal_gap >= 0 and best_gap < horizontal_gap:
            return False
        y = margin_top + best_lane * (fontsize + vertical_gap)
        end_at = start_at + (width + render_width) / speed
        event["path"] = [
            [start_at, float(width), float(y)],
            [end_at, -render_width, float(y)],
        ]
        return True

    def _prepare_panel(
        self,
        event_id,
        track,
        start_at,
        duration,
        uname,
        content,
        width,
        height,
        render_cfg,
        track_cfg,
        *,
        panel_class,
        record=None,
        emoji_measurer=None,
    ):
        def panel_setting(key, default=None):
            # Event-level visual keys are accepted for legacy configurations;
            # new configurations keep these keys on the track.
            if key in render_cfg:
                return render_cfg.get(key)
            return track_cfg.get(key, default)

        font = str(panel_setting("font", "Microsoft YaHei") or "Microsoft YaHei")
        fontsize = max(1, int(panel_setting("fontsize", 30) or 30))
        box_width = _panel_width(width, track_cfg.get("width", "25%"))
        top_height = 40
        corner_radius = _pixels(panel_setting("corner_radius"), 20)
        box_outline = _pixels(panel_setting("box_border_width"), 1)
        box_outline_opacity = float(
            panel_setting("box_border_opacity", 0.45) or 0
        )
        padding_top = 9
        padding_bottom = 14
        text_padding = 6
        text_budget = box_width - 2 * text_padding
        name_outline = _pixels(panel_setting("name_border_width"), 0)
        content_outline = _pixels(panel_setting("content_border_width"), 1)
        measurer = TextMeasurer(font, fontsize, bold=True)
        name_measurer = measurer
        if self.engine_spec.panel_raster:
            from .sprites import PillowText

            pack = emoji_measurer.pack if emoji_measurer is not None else {}
            emoji_font = self.engine_config.get("emoji_font")
            measurer = PillowText(font, fontsize, content_outline, pack, emoji_font)
            name_measurer = PillowText(font, fontsize, name_outline, pack, emoji_font)
        lines = measurer.wrap(str(content or ""), text_budget) or [""]
        line_height = fontsize
        if self.engine_spec.panel_raster:
            line_height = max(measurer.height(line, text_budget) for line in lines)
        bottom_height = len(lines) * line_height + padding_top + padding_bottom
        total_height = top_height + bottom_height
        content = "\n".join(lines)
        record = record or {}
        price_text = _panel_price_text(record)
        price_width = min(text_budget, name_measurer.measure(price_text))
        name_price_gap = (
            max(12, int(round(box_width * 0.05))) if price_text else 0
        )
        name_budget = max(0, text_budget - price_width - name_price_gap)

        top_color, bottom_color, name_color, content_color = _panel_colors(
            record,
            render_cfg,
            track_cfg,
        )
        uname = name_measurer.truncate(str(uname or ""), name_budget, ellipsis="...")
        top_opacity = float(panel_setting("top_opacity", 0.7) or 0.7)
        bottom_opacity = float(panel_setting("bottom_opacity", 0.3) or 0.3)
        name_opacity = content_opacity = 1.0
        if self.engine_spec.panel_raster:
            top_height = max(
                top_height,
                name_measurer.height(uname) + 10,
                name_measurer.height(price_text, text_budget) + 10,
            )
            total_height = top_height + bottom_height
        name_outline_color = content_outline_color = "#000000"

        def node_images(text_value):
            if emoji_measurer is None:
                return {}
            return emoji_measurer.resolve_images(str(text_value or ""))

        nodes = [
            {
                "type": "rect",
                "shape": "round_top",
                "radius": corner_radius,
                "x": 0,
                "y": 0,
                "width": box_width,
                "height": top_height,
                "color": top_color,
                "opacity": top_opacity,
                "outline": box_outline,
                "outline_color": "#000000",
                "outline_opacity": box_outline_opacity,
                "layer": 0,
            },
            {
                "type": "rect",
                "shape": "round_bottom",
                "radius": corner_radius,
                "x": 0,
                "y": top_height,
                "width": box_width,
                "height": bottom_height,
                "color": bottom_color,
                "opacity": bottom_opacity,
                "outline": box_outline,
                "outline_color": "#000000",
                "outline_opacity": box_outline_opacity,
                "layer": 0,
            },
            {
                "type": "text",
                "anchor": "top_left",
                "x": text_padding,
                "y": 5,
                "text": uname,
                "font": font,
                "size": fontsize,
                "color": name_color,
                "opacity": name_opacity,
                "outline": name_outline,
                "outline_color": name_outline_color,
                "layer": 1,
                "images": node_images(uname),
            },
        ]
        if price_text:
            nodes.append(
                {
                    "type": "text",
                    "anchor": "top_right",
                    "x": box_width - text_padding,
                    "y": 5,
                    "text": price_text,
                    "font": font,
                    "size": fontsize,
                    "color": name_color,
                    "opacity": name_opacity,
                    "outline": name_outline,
                    "outline_color": name_outline_color,
                    "layer": 1,
                    "images": node_images(price_text),
                }
            )
        nodes.append(
            {
                "type": "text",
                "anchor": "top_left",
                "x": text_padding,
                "y": top_height + padding_top,
                "text": content,
                "font": font,
                "size": fontsize,
                "color": content_color,
                "opacity": content_opacity,
                "outline": content_outline,
                "outline_color": content_outline_color,
                "layer": 1,
                "images": node_images(content),
            }
        )
        visual = {
            "type": "panel",
            "class": panel_class,
            "nodes": nodes,
        }
        if self.engine_spec.panel_raster:
            for node in nodes:
                if node["type"] != "text":
                    continue
                node["width"] = name_budget if node is nodes[2] else text_budget
                node["line_height"] = line_height
            if self.engine_config.get("emoji_font"):
                visual["emoji_font"] = self.engine_config["emoji_font"]
        return {
            "id": event_id,
            "track": track,
            "mode": "raster" if self.engine_spec.panel_raster else "ass",
            "bounds": {"width": box_width, "height": total_height},
            "path": [[float(start_at), 0.0, 0.0]],
            "visual": visual,
            "_duration": max(0.1, float(duration)),
        }

    def _layout_stacked_panels(
        self,
        *,
        track,
        existing,
        newcomers,
        state_at,
        initial_order,
        segment_start,
        segment_end,
        width,
        height,
        track_cfg,
    ):
        side_left = track == TRACK_SLIDE_LEFT
        margin_left = max(0.0, _pixels(track_cfg.get("margin_left"), 10))
        margin_right = max(0.0, _pixels(track_cfg.get("margin_right"), 10))
        margin_bottom = max(
            0.0,
            _pixels(
                track_cfg.get("margin_bottom"),
                10,
            ),
        )
        anchor_y = max(0.0, min(float(height), height - margin_bottom))
        # Keep the former SC/Gift converters' timing contract:
        # duration is the complete lifetime (entry/hold/exit included),
        # entry, exit and vertical reflow are independently configurable.
        buff = max(
            0.0,
            _pixels(track_cfg.get("vertical_gap"), PANEL_STACK_GAP),
        )
        enter = max(
            0.0,
            _pixels(track_cfg.get("enter_duration"), PANEL_ENTER_DURATION),
        )
        exit_duration = max(
            0.0,
            _pixels(track_cfg.get("exit_duration"), PANEL_EXIT_DURATION),
        )
        push = max(
            0.0,
            _pixels(track_cfg.get("reflow_duration"), PANEL_PUSH_DURATION),
        )

        all_events = [copy.deepcopy(event) for event in existing]
        for event in all_events:
            event.pop("_duration", None)
            event.pop("_arrival", None)
            event_width = float(
                (event.get("bounds") or {}).get("width")
                or _panel_width(width, track_cfg.get("width", "25%"))
            )
            inside_x = (
                margin_left
                if side_left
                else float(width) - margin_right - event_width
            )
            outside_x = (
                -(event_width + margin_left)
                if side_left
                else float(width) + margin_right
            )
            event.setdefault("visual", {})["motion"] = {
                "inside_x": float(inside_x),
                "outside_x": float(outside_x),
                "enter_duration": float(enter),
                "exit_duration": float(exit_duration),
            }
        simulation_start = min(float(state_at), float(segment_start))
        active = [
            event
            for event in all_events
            if _path_start(event) <= simulation_start < _path_end(event)
        ]
        by_id = {str(event.get("id")): event for event in active}
        ordered_ids = [
            str(event_id)
            for event_id in (initial_order or [])
            if str(event_id) in by_id
        ]
        if ordered_ids:
            ordered_set = set(ordered_ids)
            active = [by_id[event_id] for event_id in ordered_ids]
            active.extend(
                sorted(
                    (
                        event
                        for event in by_id.values()
                        if str(event.get("id")) not in ordered_set
                    ),
                    key=lambda event: _position_at(
                        event["path"], simulation_start
                    )[1],
                    reverse=True,
                )
            )
        elif active and all(event.get("_stack_order") is not None for event in active):
            # Backward compatibility for checkpoints written before
            # ``track_orders`` was introduced.
            active.sort(key=lambda event: float(event["_stack_order"]))
        else:
            active.sort(
                key=lambda event: _position_at(event["path"], simulation_start)[1],
                reverse=True,
            )
        actions = []
        arrival_batches = {}
        # Normally a checkpoint only contains panels already visible at
        # ``state_at``.  Keep future-starting panels valid as well (for example,
        # an event accepted inside the one-second boundary tolerance).
        for event in all_events:
            start_at = _path_start(event)
            if start_at > simulation_start + 1e-7:
                arrival_batches.setdefault(start_at, []).append(event)
                actions.append((_path_end(event), 1, event))
        for event in sorted(newcomers, key=lambda item: float(item["path"][0][0])):
            start_at = max(float(event["path"][0][0]), segment_start)
            lifetime = max(0.1, float(event.pop("_duration")))
            end_at = start_at + lifetime
            event_width = float(
                (event.get("bounds") or {}).get("width")
                or _panel_width(width, track_cfg.get("width", "25%"))
            )
            inside_x = (
                margin_left
                if side_left
                else float(width) - margin_right - event_width
            )
            outside_x = (
                -(event_width + margin_left)
                if side_left
                else float(width) + margin_right
            )
            event.setdefault("visual", {})["motion"] = {
                "inside_x": float(inside_x),
                "outside_x": float(outside_x),
                "enter_duration": float(enter),
                "exit_duration": float(exit_duration),
            }
            event["path"] = self._panel_initial_path(
                start_at,
                lifetime,
                outside_x,
                inside_x,
                anchor_y - event["bounds"]["height"],
                enter,
                exit_duration,
            )
            arrival_batches.setdefault(start_at, []).append(event)
            actions.append((end_at, 1, event))
            all_events.append(event)
        actions.extend(
            (timestamp, 0, batch)
            for timestamp, batch in arrival_batches.items()
        )
        for event in active:
            end_at = _path_end(event)
            actions.append((end_at, 1, event))

        active = self._advance_panel_state(
            active=active,
            actions=actions,
            from_time=simulation_start,
            to_time=segment_end,
            anchor_y=anchor_y,
            buff=buff,
            enter=enter,
            exit_duration=exit_duration,
            push=push,
        )

        for event in all_events:
            event.pop("_stack_order", None)
        return (
            [event for event in all_events if _path_end(event) > segment_start],
            [str(event["id"]) for event in active],
        )

    def _advance_panel_state(
        self,
        *,
        active,
        actions,
        from_time,
        to_time,
        anchor_y,
        buff,
        enter,
        exit_duration,
        push,
    ):
        """Advance one stacked track through an absolute time window.

        The same transition logic is used for an unrecorded gap and for the
        visible segment.  RenderPlan clipping decides which part is emitted;
        layout state itself always advances continuously.
        """
        actions.sort(key=lambda item: (item[0], item[1]))
        for timestamp, action_type, payload in actions:
            if timestamp > to_time + 1e-7:
                break
            if timestamp < from_time - 1e-7:
                continue
            active = [item for item in active if _path_end(item) > timestamp]
            if action_type == 0:
                # `active` is the logical bottom-to-top order.  Never rebuild it
                # from an instantaneous Y position: while panels are being pushed,
                # their paths may temporarily cross, and sorting that frame would
                # permanently swap two otherwise correctly ordered gifts.
                current = list(active)
                # Events with the exact same received_at are one batch: they enter
                # together instead of being delayed by an artificial one-second
                # queue.  Preserve chronological stacking semantics by putting the
                # last record at the bottom and earlier records above it.
                batch = list(reversed(payload))
                cursor = anchor_y
                for event in batch:
                    target_y = cursor - float(event["bounds"]["height"])
                    cursor = target_y - buff
                    end_at = _path_end(event)
                    motion = event["visual"]["motion"]
                    event["path"] = self._panel_initial_path(
                        timestamp,
                        end_at - timestamp,
                        float(motion["outside_x"]),
                        float(motion["inside_x"]),
                        target_y,
                        enter,
                        exit_duration,
                    )
                active = batch + current
                self._reflow_panels(
                    active,
                    timestamp,
                    anchor_y,
                    buff,
                    exit_duration,
                    push,
                )
            else:
                event = payload
                active = [item for item in active if item["id"] != event["id"]]
                self._reflow_panels(
                    active,
                    timestamp,
                    anchor_y,
                    buff,
                    exit_duration,
                    push,
                )

        return active

    @staticmethod
    def _panel_initial_path(
        start,
        lifetime,
        outside_x,
        inside_x,
        y,
        enter,
        exit_duration,
    ):
        end = start + max(0.1, float(lifetime))
        entry_end = min(start + enter, end)
        exit_start = max(entry_end, end - exit_duration)
        path = [[start, outside_x, y]]
        _append_point(path, [entry_end, inside_x, y])
        _append_point(path, [exit_start, inside_x, y])
        _append_point(path, [end, outside_x, y])
        return path

    def _reflow_panels(
        self,
        active,
        timestamp,
        anchor_y,
        buff,
        exit_duration,
        push,
    ):
        cursor = anchor_y
        for event in active:
            # An exiting panel is frozen on the row where its exit began.
            # Reserve that real row instead of a synthetic stack slot;
            # otherwise an off-screen exit leaves a visible blank row.
            end_at = _path_end(event)
            exit_start = max(_path_start(event), end_at - exit_duration)
            if timestamp >= exit_start - 1e-7:
                _, current_y = _position_at(event["path"], timestamp)
                cursor = min(cursor, current_y) - buff
                continue
            target_y = cursor - float(event["bounds"]["height"])
            cursor = target_y - buff
            self._retarget_panel(
                event,
                timestamp,
                target_y,
                exit_duration,
                push,
            )

    @staticmethod
    def _retarget_panel(event, timestamp, target_y, exit_duration, push):
        end_at = _path_end(event)
        if timestamp >= end_at:
            return
        motion = (event.get("visual") or {}).get("motion") or {}
        inside_x = float(motion.get("inside_x", 0))
        outside_x = float(motion.get("outside_x", inside_x))
        exit_start = max(_path_start(event), end_at - exit_duration)
        # Exit is a fixed horizontal movement on the row occupied at exit_start.
        # New arrivals must not turn it into a diagonal move or erase that phase.
        if timestamp >= exit_start - 1e-7:
            return
        current_x, current_y = _position_at(event["path"], timestamp)
        if math.isclose(current_y, target_y, abs_tol=0.1):
            return
        path = _cut_path(event["path"], timestamp)
        move_end = min(timestamp + push, exit_start)
        _append_point(path, [move_end, inside_x, target_y])
        _append_point(path, [exit_start, inside_x, target_y])
        _append_point(path, [end_at, outside_x, target_y])
        event["path"] = path

    @staticmethod
    def _format_template(template, record, default):
        if not template:
            return str(default or "")
        try:
            return str(template).format_map(_SafeFormatDict(record))
        except Exception:
            try:
                return replace_keywords(template, record)
            except Exception:
                return str(default or "")

    @staticmethod
    def _event_id(video, events_path, line_number):
        segment = getattr(video, "segment_id", None)
        if segment is None:
            segment = os.path.splitext(os.path.basename(events_path))[0]
        return f"{segment}:{line_number}"

    @staticmethod
    def _read_events(path: str) -> tuple[dict, list[dict]]:
        header = None
        records = []
        with open(path, "r", encoding="utf-8") as fp:
            lines = fp.readlines()
        last_content_index = max(
            (index for index, line in enumerate(lines) if line.strip()),
            default=-1,
        )
        for index, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                if index == last_content_index:
                    break
                raise ValueError(f"{path}:{index + 1} JSON 损坏") from exc
            if data.get("type") == "meta":
                if header is not None:
                    raise ValueError(f"{path} 包含多个 meta")
                header = data
            else:
                data["_line"] = index + 1
                records.append(data)
        if not header:
            raise ValueError(f"{path} 缺少 meta")
        if int(header.get("schema", 0)) != SUPPORTED_SCHEMA:
            raise ValueError(f"不支持的事件 schema: {header.get('schema')}")
        if header.get("segment_start_time") is None:
            raise ValueError(f"{path} 缺少 segment_start_time")
        return header, records

    @staticmethod
    def _parse_max_length(raw):
        if raw in (None, "", False):
            return None
        if isinstance(raw, dict):
            value = raw.get("value")
            if value is None:
                return None
            return str(raw.get("mode", "chars")).lower(), float(value)
        if isinstance(raw, str):
            value = raw.strip().lower()
            if value.endswith("%"):
                number = float(value[:-1])
                return None if number <= 0 else ("ratio", number / 100.0)
            if value.endswith("px"):
                number = float(value[:-2])
                return None if number <= 0 else ("pixels", number)
            if value.endswith("char"):
                number = float(value[:-4])
                return None if number <= 0 else ("chars", number)
            # 兼容旧配置里的裸 0；其他裸数字仍按字符数解释。
            raw = value
        value = float(raw)
        return None if value <= 0 else ("chars", value)

    @staticmethod
    def _truncate(text: str, spec, measure, screen_width: int) -> str:
        if not spec:
            return text
        mode, value = spec
        if mode == "chars":
            limit = int(value)
            return text if len(text) <= limit else text[:limit]
        pixel_limit = screen_width * value if mode == "ratio" else value
        if measure(text) <= pixel_limit:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if measure(text[:middle]) <= pixel_limit:
                low = middle
            else:
                high = middle - 1
        return text[:low]


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"
