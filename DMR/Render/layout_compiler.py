from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from DMR.utils import replace_keywords
from DMR.utils.text_width import TextMeasurer

from .emoji_width import EmojiWidthMeasurer
from .layout_models import LayoutState, RenderPlan


SUPPORTED_SCHEMA = 1

PANEL_SLIDE_DURATION = 0.5
PANEL_PUSH_DURATION = 0.3
PANEL_STACK_GAP = 10.0

TRACK_R2L = "r2l"
TRACK_SLIDE_LEFT = "slide_left"
TRACK_SLIDE_RIGHT = "slide_right"

# Event routing is intentionally fixed for now. Event configuration controls
# content/appearance/lifetime; track configuration controls motion/geometry.
EVENT_TRACKS = {
    "danmaku": TRACK_R2L,
    "superchat": TRACK_SLIDE_LEFT,
    "gift": TRACK_SLIDE_RIGHT,
    "member": TRACK_SLIDE_RIGHT,
}

# Gift/member colours are selected by total CNY value. Ranges are left-closed
# and right-open: [min_cny, max_cny). The last range has no upper bound.
GIFT_MEMBER_TOTAL_PRICE_COLORS = {
    "ranges": (
        {"min_cny": 0, "max_cny": 1, "top_color": "#EEF6F8", "bottom_color": "#607D8B"},
        {"min_cny": 1, "max_cny": 10, "top_color": "#DBFFFD", "bottom_color": "#3E8E95"},
        {"min_cny": 10, "max_cny": 50, "top_color": "#E2F0FF", "bottom_color": "#4C78C2"},
        {"min_cny": 50, "max_cny": 100, "top_color": "#EDE7FF", "bottom_color": "#7656B5"},
        {"min_cny": 100, "max_cny": 1000, "top_color": "#FFE8F1", "bottom_color": "#C65386"},
        {"min_cny": 1000, "max_cny": None, "top_color": "#FFF2C7", "bottom_color": "#B77A16"},
    ),
    "unknown": {"top_color": "#EEEEEE", "bottom_color": "#616161"},
}


@dataclass
class LayoutCompileResult:
    render_plan_path: str
    next_state: LayoutState
    event_count: int = 0
    dropped_count: int = 0
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


def _gift_panel_colors(price_cny) -> tuple[str, str]:
    ranges = GIFT_MEMBER_TOTAL_PRICE_COLORS["ranges"]
    unknown = GIFT_MEMBER_TOTAL_PRICE_COLORS["unknown"]
    try:
        price = Decimal(str(price_cny))
    except (InvalidOperation, TypeError, ValueError):
        return unknown["top_color"], unknown["bottom_color"]
    if not price.is_finite() or price < 0:
        return unknown["top_color"], unknown["bottom_color"]

    for price_range in ranges:
        try:
            lower_raw = price_range.get("min_cny")
            upper_raw = price_range.get("max_cny")
            lower = Decimal(str(lower_raw)) if lower_raw is not None else None
            upper = Decimal(str(upper_raw)) if upper_raw is not None else None
        except (InvalidOperation, TypeError, ValueError):
            continue
        if lower is not None and price < lower:
            continue
        if upper is not None and price >= upper:
            continue
        return price_range["top_color"], price_range["bottom_color"]
    return unknown["top_color"], unknown["bottom_color"]


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
    if count <= 1:
        return ""
    return f" ×{count}{unit or ''}"


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
        engine: str = "dmrender",
        engine_config: dict = None,
    ):
        self.config = layout_config or {}
        self.engine = engine
        self.engine_config = engine_config or {}

    def compile(
        self,
        video,
        events_path: str,
        previous_state: LayoutState | dict | None = None,
        work_dir: str = None,
    ) -> LayoutCompileResult:
        header, records = self._read_events(events_path)
        width, height = getattr(video, "resolution", None) or (1920, 1080)
        width, height = int(width or 1920), int(height or 1080)
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

        state_events = [
            copy.deepcopy(event)
            for event in old_state.events
            if _path_end(event) > segment_start
        ]

        dan_render = self.config.get("danmaku") or {}
        gift_render = self.config.get("gift") or {}
        sc_render = self.config.get("superchat") or {}
        member_render = self.config.get("member") or {}
        r2l_render = self.config.get(TRACK_R2L) or {}
        slide_left_render = self.config.get(TRACK_SLIDE_LEFT) or {}
        slide_right_render = self.config.get(TRACK_SLIDE_RIGHT) or {}

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

        accepted = 0
        dropped = 0
        new_r2l = []
        new_panels = {
            TRACK_SLIDE_LEFT: [],
            TRACK_SLIDE_RIGHT: [],
        }
        ordered_records = sorted(
            records,
            key=lambda item: (float(item.get("received_at", 0) or 0), item["_line"]),
        )

        for record in ordered_records:
            event_type = record.get("type")
            event_track = EVENT_TRACKS.get(event_type)
            if event_type not in included_events or event_track is None:
                dropped += 1
                continue
            try:
                # Keep the event's segment-local time unchanged when an
                # overlapping segment origin was moved forward.
                start_at = float(record["received_at"]) + segment_time_shift
            except (KeyError, TypeError, ValueError):
                dropped += 1
                continue
            if duration > 0 and start_at >= segment_end + 1:
                dropped += 1
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
                    dropped += 1
                    continue
                new_r2l.append(prepared)
                accepted += 1
                continue

            if event_type == "gift":
                min_cny = gift_render.get("min_price_cny")
                try:
                    below_minimum = (
                        min_cny is not None
                        and record.get("price_cny") is not None
                        and float(record["price_cny"]) < float(min_cny)
                    )
                except (TypeError, ValueError):
                    below_minimum = False
                if below_minimum:
                    dropped += 1
                    continue
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
                    float(gift_render.get("duration", 10) or 10),
                    record.get("uname", ""),
                    content,
                    width,
                    height,
                    gift_render,
                    slide_right_render,
                    panel_class="gift",
                    record=record,
                )
                new_panels[event_track].append(panel)
                accepted += 1
                continue

            if event_type == "member":
                display_record = dict(record)
                display_record["member_count_suffix"] = _quantity_suffix(
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
                    float(member_render.get("duration", 10) or 10),
                    record.get("uname", ""),
                    content,
                    width,
                    height,
                    member_render,
                    slide_right_render,
                    panel_class="member",
                    record=record,
                )
                new_panels[event_track].append(panel)
                accepted += 1
                continue

            if event_type == "superchat":
                panel = self._prepare_panel(
                    event_id,
                    event_track,
                    start_at,
                    float(record.get("duration", 60) or 60),
                    record.get("uname", ""),
                    str(record.get("content", "") or ""),
                    width,
                    height,
                    sc_render,
                    slide_left_render,
                    panel_class="superchat",
                    record=record,
                )
                new_panels[event_track].append(panel)
                accepted += 1
                continue

            dropped += 1

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
            else:
                accepted -= 1
                dropped += 1

        track_configs = {
            TRACK_SLIDE_LEFT: slide_left_render,
            TRACK_SLIDE_RIGHT: slide_right_render,
        }
        for track, track_render in track_configs.items():
            existing = [event for event in state_events if event.get("track") == track]
            combined = self._layout_stacked_panels(
                track=track,
                existing=existing,
                newcomers=new_panels[track],
                segment_start=segment_start,
                segment_end=segment_end,
                width=width,
                height=height,
                track_cfg=track_render,
            )
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
                if key != "track" and not key.startswith("_")
            }
            plan_event["path"] = local_path
            plan_events.append(plan_event)

        next_events = [
            copy.deepcopy(event)
            for event in state_events
            if _path_end(event) > segment_end
        ]
        next_state = LayoutState(at=segment_end, events=next_events)
        plan = RenderPlan(duration=duration, events=plan_events)

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
        is_raster = emoji_measurer.is_special(text, emoticon)
        measure = (
            (lambda value: emoji_measurer.measure(value, emoticon))
            if is_raster
            else text_measurer.measure
        )
        text = self._truncate(text, max_length, measure, width)
        if not text:
            return None

        content_width = max(1, int(measure(text)))
        line_height = max(
            int(dan_render.get("fontsize", 36) or 36),
            emoji_measurer.png_height if is_raster else 0,
        )
        outer_width = content_width
        if pill:
            pad_x = (float(dan_render.get("fontsize", 36) or 36) + 2 * pill["padding"]) / 2
            outer_width += int(round(2 * pad_x))
            line_height = max(
                line_height,
                int(dan_render.get("fontsize", 36) or 36) + 2 * pill["padding"],
            )
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
        duration = max(0.1, float(track_cfg.get("duration", 16) or 16))
        speed = width / duration
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
    ):
        fontsize = 30
        box_width = _panel_width(width, track_cfg.get("width", "25%"))
        top_height = 40
        corner_radius = _pixels(render_cfg.get("corner_radius"), 40)
        box_outline = _pixels(render_cfg.get("box_border_width"), 1)
        box_outline_opacity = float(
            render_cfg.get("box_border_opacity", 0.45) or 0
        )
        padding_top = 9
        padding_bottom = 14
        text_padding = 6
        measurer = TextMeasurer("Microsoft YaHei", fontsize, bold=True)
        lines = measurer.wrap(str(content or ""), box_width - 2 * text_padding) or [""]
        bottom_height = len(lines) * fontsize + padding_top + padding_bottom
        total_height = top_height + bottom_height
        content = "\n".join(lines)
        text_budget = box_width - 2 * text_padding
        record = record or {}
        price_text = _panel_price_text(record)
        price_width = measurer.measure(price_text)
        name_price_gap = (
            max(12, int(round(box_width * 0.05))) if price_text else 0
        )
        name_budget = max(0, text_budget - price_width - name_price_gap)

        if panel_class == "superchat":
            top_color = _hex_color(record.get("background_color"), "#FFF5ED")
            bottom_color = _hex_color(record.get("background_bottom_color"), "#B2602A")
            name_color = _hex_color(record.get("name_color"), "#000000")
            content_color = _hex_color(record.get("content_color"), "#FFFFFF")
            uname = measurer.truncate(
                str(uname or ""), name_budget, ellipsis="..."
            )
            top_opacity = float(render_cfg.get("top_opacity", 0.7) or 0.7)
            bottom_opacity = float(render_cfg.get("bottom_opacity", 0.3) or 0.3)
            name_outline = _pixels(render_cfg.get("name_border_width"), 0)
            content_outline = _pixels(render_cfg.get("content_border_width"), 1)
            name_opacity = content_opacity = 1.0
            name_outline_color = content_outline_color = "#000000"
        else:
            top_color, bottom_color = _gift_panel_colors(
                record.get("price_cny")
            )
            name_color = "#666666"
            content_color = "#FFFFFF"
            uname = measurer.truncate(
                str(uname or ""), name_budget, ellipsis="..."
            )
            # Match SC typography and geometry; only the background colours differ.
            top_opacity = float(render_cfg.get("top_opacity", 0.7) or 0.7)
            bottom_opacity = float(render_cfg.get("bottom_opacity", 0.3) or 0.3)
            name_opacity = content_opacity = 1.0
            name_outline = _pixels(render_cfg.get("name_border_width"), 0)
            content_outline = _pixels(render_cfg.get("content_border_width"), 1)
            name_outline_color = content_outline_color = "#000000"

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
                "font": "Microsoft YaHei",
                "size": fontsize,
                "color": name_color,
                "opacity": name_opacity,
                "outline": name_outline,
                "outline_color": name_outline_color,
                "layer": 1,
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
                    "font": "Microsoft YaHei",
                    "size": fontsize,
                    "color": name_color,
                    "opacity": name_opacity,
                    "outline": name_outline,
                    "outline_color": name_outline_color,
                    "layer": 1,
                }
            )
        nodes.append(
            {
                "type": "text",
                "anchor": "top_left",
                "x": text_padding,
                "y": top_height + padding_top,
                "text": content,
                "font": "Microsoft YaHei",
                "size": fontsize,
                "color": content_color,
                "opacity": content_opacity,
                "outline": content_outline,
                "outline_color": content_outline_color,
                "layer": 1,
            }
        )
        visual = {
            "type": "panel",
            "class": panel_class,
            "nodes": nodes,
        }
        return {
            "id": event_id,
            "track": track,
            "mode": "ass",
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
                76 if side_left else 130,
            ),
        )
        anchor_y = max(0.0, min(float(height), height - margin_bottom))
        # Keep the former SC/Gift converters' timing contract:
        # duration is the complete lifetime (entry/hold/exit included),
        # horizontal entry/exit takes 0.5 s and vertical push/fall 0.3 s.
        buff = max(
            0.0,
            _pixels(track_cfg.get("vertical_gap"), PANEL_STACK_GAP),
        )
        slide = PANEL_SLIDE_DURATION
        push = PANEL_PUSH_DURATION

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
                "slide_duration": float(slide),
            }
        active = [
            event
            for event in all_events
            if _path_start(event) <= segment_start < _path_end(event)
        ]
        if active and all(event.get("_stack_order") is not None for event in active):
            active.sort(key=lambda event: float(event["_stack_order"]))
        else:
            active.sort(
                key=lambda event: _position_at(event["path"], segment_start)[1],
                reverse=True,
            )
        self._set_panel_order(active)
        actions = []
        arrival_batches = {}
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
                "slide_duration": float(slide),
            }
            event["path"] = self._panel_initial_path(
                start_at,
                lifetime,
                outside_x,
                inside_x,
                anchor_y - event["bounds"]["height"],
                slide,
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

        actions.sort(key=lambda item: (item[0], item[1]))
        for timestamp, action_type, payload in actions:
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
                        slide,
                    )
                active = batch + current
                self._set_panel_order(active)
                self._reflow_panels(
                    active,
                    timestamp,
                    anchor_y,
                    buff,
                    slide,
                    push,
                )
            else:
                event = payload
                active = [item for item in active if item["id"] != event["id"]]
                self._set_panel_order(active)
                self._reflow_panels(
                    active,
                    timestamp,
                    anchor_y,
                    buff,
                    slide,
                    push,
                )

        return [event for event in all_events if _path_end(event) > segment_start]

    @staticmethod
    def _set_panel_order(active):
        for index, event in enumerate(active):
            event["_stack_order"] = index

    @staticmethod
    def _panel_initial_path(start, lifetime, outside_x, inside_x, y, slide):
        end = start + max(0.1, float(lifetime))
        entry_end = min(start + slide, end)
        exit_start = max(entry_end, end - slide)
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
        slide,
        push,
    ):
        cursor = anchor_y
        for event in active:
            # An exiting panel is frozen on the row where its exit began.
            # Reserve that real row instead of a synthetic stack slot;
            # otherwise an off-screen exit leaves a visible blank row.
            end_at = _path_end(event)
            exit_start = max(_path_start(event), end_at - slide)
            if timestamp >= exit_start - 1e-7:
                _, current_y = _position_at(event["path"], timestamp)
                cursor = min(cursor, current_y) - buff
                continue
            target_y = cursor - float(event["bounds"]["height"])
            cursor = target_y - buff
            self._retarget_panel(event, timestamp, target_y, slide, push)

    @staticmethod
    def _retarget_panel(event, timestamp, target_y, slide, push):
        end_at = _path_end(event)
        if timestamp >= end_at:
            return
        motion = (event.get("visual") or {}).get("motion") or {}
        inside_x = float(motion.get("inside_x", 0))
        outside_x = float(motion.get("outside_x", inside_x))
        exit_start = max(_path_start(event), end_at - slide)
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
