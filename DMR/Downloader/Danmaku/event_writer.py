from __future__ import annotations

import json
import os
import threading
from collections import deque
from typing import Iterable

from DMR.utils.danmaku import (
    EntryDanmaku,
    GiftDanmaku,
    MemberDanmaku,
    SuperChatDanmaku,
    UserDanmaku,
)


SCHEMA_VERSION = 1
SUPPORTED_EVENT_TYPES = {
    "danmaku",
    "entry",
    "gift",
    "superchat",
    "member",
}


def _put_optional(target: dict, source, *names: str) -> None:
    for name in names:
        value = getattr(source, name, None)
        if value is not None and value != "":
            target[name] = value


def _identity_fields(event) -> dict:
    result = {}
    uid = getattr(event, "uid", None)
    if uid is not None and uid != "":
        result["uid"] = str(uid)
    _put_optional(result, event, "uname")
    return result


def serialize_event(event) -> dict | None:
    """把已识别事件转换为稳定、显式的持久化格式。

    不允许序列化 __dict__：其中可能包含 raw、协议对象和平台临时字段。
    """
    received_at = getattr(event, "received_at", None)
    if received_at is None:
        received_at = getattr(event, "timestamp", None)
    if received_at is None:
        return None

    if isinstance(event, UserDanmaku):
        data = {
            "type": "danmaku",
            "received_at": float(received_at),
            **_identity_fields(event),
            "content": str(getattr(event, "content", "") or ""),
            "color": str(getattr(event, "color", "ffffff") or "ffffff"),
        }
        _put_optional(data, event, "face_url")
        emoticon = getattr(event, "emoticon", None)
        if isinstance(emoticon, dict):
            normalized = {}
            for key in (
                "emoticon_unique",
                "name",
                "url",
                "width",
                "height",
                "is_dynamic",
            ):
                value = emoticon.get(key)
                if value is not None and value != "":
                    normalized[key] = value
            if normalized:
                data["emoticon"] = normalized
        return data

    if isinstance(event, EntryDanmaku):
        data = {
            "type": "entry",
            "received_at": float(received_at),
            **_identity_fields(event),
        }
        _put_optional(data, event, "face_url")
        return data

    if isinstance(event, GiftDanmaku):
        data = {
            "type": "gift",
            "received_at": float(received_at),
            **_identity_fields(event),
            "gift_name": getattr(event, "gift_name", ""),
            "gift_count": getattr(event, "gift_count", 0),
            "gift_price": getattr(event, "gift_price", None),
            "price_unit": getattr(event, "price_unit", ""),
            "unit_price_cny": getattr(event, "unit_price_cny", None),
            "price_cny": getattr(event, "price_cny", None),
        }
        _put_optional(data, event, "content", "face_url", "gift_icon_url")
        return data

    if isinstance(event, SuperChatDanmaku):
        data = {
            "type": "superchat",
            "received_at": float(received_at),
            **_identity_fields(event),
            "content": getattr(event, "content", ""),
            "price": getattr(event, "price", None),
            "price_unit": getattr(event, "price_unit", ""),
            "price_cny": getattr(event, "price_cny", None),
            "platform_duration": getattr(event, "sc_duration", None),
        }
        _put_optional(
            data,
            event,
            "background_color",
            "background_bottom_color",
            "name_color",
            "content_color",
            "face_url",
        )
        return data

    if isinstance(event, MemberDanmaku):
        data = {
            "type": "member",
            "received_at": float(received_at),
            **_identity_fields(event),
            "member_name": getattr(event, "member_name", ""),
            "member_price": getattr(event, "member_price", None),
            "member_count": getattr(event, "member_count", None),
            "member_unit": getattr(event, "member_unit", ""),
            "price_cny": getattr(event, "price_cny", None),
        }
        _put_optional(data, event, "content", "face_url")
        return data

    return None


class EventWriter:
    """单段直播事件 JSONL 写入器。

    保留一个短尾缓冲，使视频下载器稍晚报告分段边界时，仍能把边界后的事件
    分配到新文件。正常停止会全部 flush；强杀进程最多损失 tail_window 秒事件。
    """

    def __init__(self, platform: str, record_types: Iterable[str], tail_window: float = 15.0):
        self.platform = str(platform or "")
        self.record_types = {
            str(item).lower()
            for item in (record_types or ())
            if str(item).lower() in SUPPORTED_EVENT_TYPES
        }
        self.tail_window = max(0.0, float(tail_window))
        self._lock = threading.RLock()
        self._pending = deque()
        self._fp = None
        self.path = None
        self.segment_start_time = None

    def open(self, path: str, segment_start_time: float) -> None:
        with self._lock:
            if self._fp is not None:
                raise RuntimeError("当前弹幕事件段尚未关闭，请使用 rotate() 连续切段")
            start_time = float(segment_start_time)
            # 弹幕连接可能早于视频文件建立。只保留实际视频开始之后收到的事件，
            # 视频开始之前的未归属事件没有对应画面，不能写入本段。
            while self._pending and self._pending[0]["received_at"] < start_time:
                self._pending.popleft()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self.path = path
            self.segment_start_time = start_time
            self._fp = open(path, "w", encoding="utf-8", newline="\n")
            self._write_line({
                "type": "meta",
                "schema": SCHEMA_VERSION,
                "segment_start_time": self.segment_start_time,
                "platform": self.platform,
            })

    def write(self, event) -> bool:
        data = serialize_event(event)
        if not data or data["type"] not in self.record_types:
            return False
        with self._lock:
            self._pending.append(data)
            if self._fp is None:
                # 视频尚未开始或正处于断线间隔。只保存一个短窗口，防止长时间
                # 无视频时未归属弹幕无限占用内存；open() 会再按真实开始时间筛选。
                cutoff = data["received_at"] - self.tail_window
                while self._pending and self._pending[0]["received_at"] < cutoff:
                    self._pending.popleft()
                return True
            watermark = data["received_at"] - self.tail_window
            self._flush_before(watermark)
        return True

    def rotate(self, path: str | None, segment_start_time: float | None) -> str | None:
        """按下一段起点切分尾部事件，返回已经关闭的旧文件路径。"""
        with self._lock:
            old_path = self.path
            if path is None or segment_start_time is None:
                self.close()
                return old_path

            cutoff = float(segment_start_time)
            self._flush_before(cutoff)
            carry = list(self._pending)
            self._pending.clear()
            if self._fp:
                self._fp.flush()
                self._fp.close()
            self._fp = None

            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self.path = path
            self.segment_start_time = cutoff
            self._fp = open(path, "w", encoding="utf-8", newline="\n")
            self._write_line({
                "type": "meta",
                "schema": SCHEMA_VERSION,
                "segment_start_time": cutoff,
                "platform": self.platform,
            })
            self._pending.extend(carry)
            return old_path

    def close_segment(self, segment_end_time: float) -> str | None:
        """按真实视频结束时间关闭当前段，保留结束后的事件供下一段筛选。"""
        with self._lock:
            old_path = self.path
            cutoff = float(segment_end_time)
            if self._fp is None:
                return old_path

            self._flush_before(cutoff)
            carry = list(self._pending)
            self._pending.clear()
            if self._fp:
                self._fp.flush()
                self._fp.close()
            self._fp = None
            self.path = None
            self.segment_start_time = None
            self._pending.extend(carry)
            return old_path

    def close(self) -> None:
        with self._lock:
            if self._fp:
                while self._pending:
                    self._write_line(self._pending.popleft())
            else:
                # 没有活动视频段时的事件没有对应画面，任务结束时直接丢弃。
                self._pending.clear()
            if self._fp:
                self._fp.flush()
                self._fp.close()
            self._fp = None
            self.path = None
            self.segment_start_time = None

    def _flush_before(self, cutoff: float) -> None:
        while self._pending and self._pending[0]["received_at"] < cutoff:
            self._write_line(self._pending.popleft())

    def _write_line(self, data: dict) -> None:
        if not self._fp:
            raise RuntimeError("EventWriter 尚未打开")
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        encoded = encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        self._fp.write(encoded)
        self._fp.write("\n")
        self._fp.flush()
