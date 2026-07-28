import asyncio
import logging
import os
import platform
import threading
import time

from DMR.LiveAPI.danmaku import DanmakuClient
from DMR.utils.danmaku import (
    EntryDanmaku,
    GiftDanmaku,
    MemberDanmaku,
    SuperChatDanmaku,
    UserDanmaku,
)

from .event_writer import EventWriter, SUPPORTED_EVENT_TYPES

__all__ = ["DanmakuDownloader"]


_EVENT_CLASSES = (
    UserDanmaku,
    EntryDanmaku,
    GiftDanmaku,
    SuperChatDanmaku,
    MemberDanmaku,
)


def _detect_platform(url: str) -> str:
    value = str(url or "").lower()
    for platform_name in ("bilibili", "douyin", "douyu", "huya", "twitch", "youtube", "cc"):
        if platform_name in value:
            return platform_name
    return ""


class DanmakuDownloader:
    """连接单一直播间弹幕流，并把已识别事件写为分段 JSONL。

    本类只负责连接、重连、类型白名单和持久化。过滤、模板、VIP、测宽、
    碰撞以及礼物/SC 时间线均由渲染阶段的 LayoutCompiler 处理。
    """

    def __init__(
        self,
        url: str,
        output: str,
        segment: float,
        danmaku_args: dict = None,
        description: str = "",
        width: int = 1920,
        height: int = 1080,
        **kwargs,
    ) -> None:
        del description, width, height  # 旧构造参数保留兼容，不再用于下载阶段布局
        da = danmaku_args or {}
        adv = da.get("advanced_args") or {}

        self.logger = logging.getLogger(__name__)
        self.stoped = False
        self.part = 0
        self.url = url
        self.output = output
        self.segment = segment
        self.dm_stream_option = da.get("stream_option") or {}
        self.dm_auto_restart = adv.get("dm_auto_restart", 300)

        raw_types = da.get("record_types")
        if raw_types is None:
            raw_types = ["danmaku", "gift", "superchat", "member"]
        if isinstance(raw_types, str):
            raw_types = [raw_types]
        self.record_types = {
            str(item).lower()
            for item in raw_types
            if str(item).lower() in SUPPORTED_EVENT_TYPES
        }
        self.writer = EventWriter(
            platform=_detect_platform(url),
            record_types=self.record_types,
            tail_window=adv.get("event_tail_window", 15),
        )
        self.dm_file = None
        self.monitor = None

        if platform.system() == "Windows":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def start(self, self_segment: bool = False, segment_start_time: float = None):
        start_time = float(segment_start_time or time.time())
        self.dm_file = self.output.replace("%03d", "%03d" % self.part)
        self.writer.open(self.dm_file, start_time)

        if self_segment:
            def monitor():
                while not self.stoped:
                    time.sleep(self.segment)
                    if not self.stoped:
                        self.split(next_segment_start_time=time.time())

            self.monitor = threading.Thread(target=monitor, daemon=True)
            self.monitor.start()

        return self.start_dmc()

    def split(
        self,
        filename: str = None,
        next_segment_start_time: float = None,
        open_next: bool = True,
    ):
        """完成当前事件段，并按精确边界打开下一段。

        filename 是与已完成视频同名的最终 JSONL 路径。最终停机时 open_next=False，
        只关闭并重命名当前文件，不制造空的下一段。
        """
        old_dm_file = self.dm_file
        next_file = None
        if open_next:
            self.part += 1
            next_file = self.output.replace("%03d", "%03d" % self.part)
            next_segment_start_time = float(next_segment_start_time or time.time())

        self.writer.rotate(next_file, next_segment_start_time)

        if filename and old_dm_file and old_dm_file != filename:
            try:
                os.replace(old_dm_file, filename)
                old_dm_file = filename
            except Exception as exc:
                self.logger.error("弹幕事件文件 %s 分段失败: %s", old_dm_file, exc)

        self.dm_file = next_file
        return old_dm_file

    def process_danmaku(self, event) -> bool:
        if not isinstance(event, _EVENT_CLASSES):
            return False
        if getattr(event, "dtype", None) not in self.record_types:
            return False

        # 只做无法恢复的结构校验；业务过滤必须留到渲染阶段。
        if isinstance(event, UserDanmaku):
            if not getattr(event, "uname", None) or not getattr(event, "content", None):
                return False
        elif isinstance(event, EntryDanmaku):
            if not getattr(event, "uname", None):
                return False
        elif isinstance(event, (GiftDanmaku, SuperChatDanmaku, MemberDanmaku)):
            if not getattr(event, "uname", None):
                return False

        return self.writer.write(event)

    def start_dmc(self):
        async def danmu_monitor():
            queue = asyncio.Queue()

            async def dmc_task():
                dmc = DanmakuClient(self.url, queue, **self.dm_stream_option)
                try:
                    await dmc.start()
                except asyncio.CancelledError:
                    await dmc.stop()
                    raise
                except Exception:
                    await dmc.stop()
                    self.logger.exception("弹幕连接异常")

            task = asyncio.create_task(dmc_task())
            last_event_time = time.monotonic()
            retry = 0

            while not self.stoped:
                try:
                    event = queue.get_nowait()
                    retry = 0
                    # 重连超时判断的是连接是否仍有消息，不应受 record_types 白名单影响。
                    last_event_time = time.monotonic()
                    self.process_danmaku(event)
                    continue
                except asyncio.QueueEmpty:
                    pass

                if task.done():
                    self.logger.error("%s 弹幕下载线程异常退出，正在重试", self.url)
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        self.logger.exception("弹幕任务结束")
                    retry += 1
                    await asyncio.sleep(min(15 * retry, 60))
                    task = asyncio.create_task(dmc_task())
                    last_event_time = time.monotonic()
                    continue

                if self.dm_auto_restart and time.monotonic() - last_event_time > self.dm_auto_restart:
                    self.logger.error("%s 获取弹幕超时，正在重连", self.url)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    task = asyncio.create_task(dmc_task())
                    last_event_time = time.monotonic()
                    continue

                await asyncio.sleep(0.1)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            new_loop.run_until_complete(danmu_monitor())
        finally:
            new_loop.close()

    def stop(self, discard_unpaired: bool = False):
        self.stoped = True
        unpaired_file = self.dm_file
        self.writer.close()
        self.dm_file = None

        if discard_unpaired and unpaired_file:
            try:
                if os.path.exists(unpaired_file):
                    os.remove(unpaired_file)
                    self.logger.info(
                        "删除没有对应视频的弹幕事件文件: %s",
                        unpaired_file,
                    )
            except OSError:
                self.logger.exception(
                    "删除未配对弹幕事件文件失败: %s",
                    unpaired_file,
                )

        self.logger.debug("danmaku event writer stopped.")
        return True
