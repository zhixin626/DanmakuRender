import json
from datetime import datetime
import logging
import os
import time
import threading
import queue
import importlib
from dataclasses import dataclass
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from os.path import join, exists
from typing import Tuple

from DMR.utils import (
    VideoInfo,
    DateTimeDecoder,
    FFprobe,
    uuid,
    PipeMessage,
    StreamerInfo,
    atomic_json_dump,
)
from .baserender import RenderStopped


@dataclass(frozen=True)
class RenderEngine:
    module: str
    class_name: str
    label: str
    overlay: bool = True
    raster: bool = False
    panel_raster: bool = False
    fallback: str | None = None

    def load(self):
        """Import optional dependencies only when this backend is selected."""
        return getattr(
            importlib.import_module(self.module, __package__), self.class_name
        )


ENGINES = {
    "dmrender": RenderEngine(".dmrender", "DmRender", "弹幕渲染"),
    "emoji_dmrender": RenderEngine(
        ".emoji_dmrender", "EmojiDmRender", "彩色表情渲染", raster=True
    ),
    "gpu_render": RenderEngine(
        ".gpu_render",
        "GpuRender",
        "GPU 弹幕渲染",
        raster=True,
        panel_raster=True,
        fallback="emoji_dmrender",
    ),
    "transcode": RenderEngine(".transcode", "Transcoder", "转码", overlay=False),
}
DEFAULT_ENGINE = next(iter(ENGINES))
EMOJI_ENGINE = next(
    name for name, spec in ENGINES.items() if spec.raster and not spec.panel_raster
)
TRANSCODE_ENGINE = next(name for name, spec in ENGINES.items() if not spec.overlay)


def get_engine(name: str) -> RenderEngine:
    try:
        return ENGINES[name]
    except (KeyError, TypeError):
        raise ValueError(f'未知渲染引擎：{name}；可用：{", ".join(ENGINES)}') from None


from .layout_track import LayoutTrackManager


class Render():
    def __init__(self,
                 pipe:Tuple[queue.Queue, queue.Queue],
                 nrenders:int=1,
                 **kwargs,
                 ) -> None:

        self.nrenders = int(nrenders)
        self.send_queue, self.recv_queue = pipe
        self.logger = logging.getLogger(__name__)
        self.kwargs = kwargs
        self.stoped = True

        self._piperecvprocess = None
        self.render_tasks = {}
        self.failed_tasks = {}
        self.completed_tasks = []   # 渲染成功的视频记录（最近若干条）
        self.failed_tasks_file = kwargs.get(
            "failed_tasks_file", ".temp/failed_renders.json"
        )

        self._render_class = {}
        self.render_executors = ThreadPoolExecutor(max_workers=self.nrenders)
        self._lock = threading.Lock()
        self._layout_sequences = defaultdict(int)
        self.layout_tracks = LayoutTrackManager(logger=self.logger)

    def load_failed_tasks(self):
        if exists(self.failed_tasks_file):
            try:
                with open(self.failed_tasks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f, cls=DateTimeDecoder)
                    for uuid, task in data.items():
                        if task.get('video'):
                            for key in ("ctime", "segment_start_time"):
                                if isinstance(task["video"].get(key), str):
                                    task["video"][key] = datetime.fromisoformat(
                                        task["video"][key]
                                    )
                            if isinstance(task["video"].get("streamer"), dict):
                                task["video"]["streamer"] = StreamerInfo(
                                    **task["video"]["streamer"]
                                )
                            task['video'] = VideoInfo(**task['video'])
                            task["config"]["video"] = task["video"]
                        if task.get("status") not in ("stopped", "error"):
                            task["status"] = "stopped"
                            task["error"] = "程序退出时尚未完成，可独立重试"
                        self.failed_tasks[uuid] = task
                self.logger.info(f'Loaded {len(self.failed_tasks)} failed render tasks.')
            except Exception as e:
                self.logger.error(f'Failed to load failed tasks: {e}')

    def save_failed_tasks(self):
        try:
            atomic_json_dump(self.failed_tasks_file, self.failed_tasks)
        except Exception as e:
            self.logger.error(f'Failed to save failed tasks: {e}')

    def retry_task(self, task_id):
        with self._lock:
            if task_id in self.failed_tasks and task_id not in self.render_tasks:
                task = self.failed_tasks[task_id]

                # Re-submit
                task['status'] = 'waiting'
                task["request_id"] = uuid()
                task["config"]["_independent_retry"] = True
                task.pop("layout_track_key", None)
                task.pop("layout_sequence", None)
                self.render_tasks[task['uuid']] = task
                self.render_executors.submit(self._render_subprocess, task)
                return True
            return False

    def delete_failed_task(self, uuid):
        with self._lock:
            if uuid in self.failed_tasks and uuid not in self.render_tasks:
                self.failed_tasks.pop(uuid)
                return True
            return False

    def _pipeSend(self, event, msg, target='engine', dtype=None, data=None, **kwargs):
        if self.send_queue:
            msg = PipeMessage(
                source='render',
                target=target,
                event=event,
                msg=msg,
                dtype=dtype,
                data=data,
                **kwargs,
            )
            self.send_queue.put(msg)

    def _pipeRecvMonitor(self):
        while self.stoped == False and self.recv_queue is not None:
            message:PipeMessage = self.recv_queue.get()
            try:
                if message.target == 'render':
                    if message.event == "newtask":
                        self.add_task(message)
                    elif message.event == "layout_end":
                        track_key = (message.data or {}).get('track_key')
                        if track_key:
                            self.layout_tracks.reset(track_key)
                            with self._lock:
                                self._layout_sequences.pop(track_key, None)
            except Exception as e:
                self.logger.error(f'Message:{message} raise an error.')
                self.logger.exception(e)

    def start(self):
        self.stoped = False
        self._piperecvprocess = threading.Thread(target=self._pipeRecvMonitor, daemon=True)
        self._piperecvprocess.start()

    def add_task(self, msg:PipeMessage):
        with self._lock:
            config = msg.data
            source = msg.source
            request_id = msg.request_id
            task = {
                "uuid": uuid(),
                "source": source,
                "request_id": request_id,
                "mode": config.get("mode", DEFAULT_ENGINE),
                "args": config.get("args", {}),
                "video": config.get("video"),
                "output": config.get("output"),
                "config": config,
                "status": "waiting",
                "progress": None,  # 渲染百分比（'45%'），由渲染引擎回调更新，供 WebUI 展示
            }
            if get_engine(task["mode"]).overlay:
                video = task.get('video')
                events_path = getattr(video, 'dm_file_id', None)
                if not events_path or not str(events_path).lower().endswith('.jsonl'):
                    events_path = None
            else:
                events_path = None
            if events_path:
                group_id = getattr(video, 'group_id', None) or 'default'
                track_key = f"{config.get('taskname') or source}:{group_id}"
                retry_track = config.get('_layout_track_key')
                retry_sequence = config.get('_layout_sequence')
                if retry_track == track_key and retry_sequence is not None:
                    task['layout_track_key'] = track_key
                    task['layout_sequence'] = int(retry_sequence)
                else:
                    if track_key not in self._layout_sequences:
                        self.layout_tracks.begin(track_key)
                    task['layout_track_key'] = track_key
                    task['layout_sequence'] = self._layout_sequences[track_key]
                    self._layout_sequences[track_key] += 1
                    # liveevents retries the original PipeMessage.  Persist the
                    # layout identity in that message so a retry rebuilds the
                    # same historical segment instead of consuming a new one.
                    config['_layout_track_key'] = track_key
                    config['_layout_sequence'] = task['layout_sequence']
            self.render_tasks[task['uuid']] = task
            self.render_executors.submit(self._render_subprocess, task)

    def _gather(self, task, status, desc=''):
        with self._lock:
            self.render_tasks.pop(task['uuid'], None)
            if status == "stopped":
                task["status"] = "stopped"
                task["error"] = str(desc)
                self.failed_tasks[task["uuid"]] = task
                self._pipeSend(
                    event="stopped",
                    msg=f"视频{task['output']}渲染已停止",
                    target=task["source"],
                    request_id=task["request_id"],
                    data=desc,
                )
            elif status == "error":
                if task["config"].get("_independent_retry"):
                    task["status"] = "error"
                    task["error"] = str(desc)
                    self.failed_tasks[task["uuid"]] = task
                # 渲染失败已交由 liveevents 挂起转人工（见 onRenderError），不再留存 failed_tasks
                self._pipeSend(
                    event='error',
                    msg=f"渲染视频{task['output']}时出现错误: {desc}",
                    target=task['source'],
                    request_id=task['request_id'],
                    dtype=str(type(desc)),
                    data=desc,
                )
            else:
                # 记录渲染成功的视频（最新在前），只保留最近 200 条避免无限增长
                self.failed_tasks.pop(task["uuid"], None)
                output_path = getattr(desc, 'path', None) or task.get('output')
                video = task.get('video')
                self.completed_tasks.append({
                    'uuid': task['uuid'],
                    'video': video.path if video else 'Unknown',
                    'output': output_path,
                    'mode': task.get('mode', 'Unknown'),
                    'time': datetime.now(),
                })
                if len(self.completed_tasks) > 200:
                    self.completed_tasks = self.completed_tasks[-200:]

                self._pipeSend(
                    event='end',
                    msg=f"视频{task['output']}渲染完成",
                    target=task['source'],
                    request_id=task['request_id'],
                    dtype='dict',
                    data={
                        'config': task['config'],
                        'output': desc,
                    },
                )

    def _compile_layout(self, task, mode):
        """Use the same historical checkpoint when changing fallback backend."""
        if task["config"].get("_independent_retry"):
            from .layout_compiler import LayoutCompiler

            return LayoutCompiler(
                task["args"], engine=mode, engine_config=task["args"]
            ).compile(
                video=task["video"],
                events_path=task["video"].dm_file_id,
                work_dir=os.path.dirname(task["output"]) or None,
            )
        return self.layout_tracks.compile_segment(
            track_key=task.get("layout_track_key"),
            sequence=task.get("layout_sequence"),
            video=task["video"],
            events_path=task["video"].dm_file_id,
            layout_config=task["args"],
            engine=mode,
            engine_config=task["args"],
            work_dir=os.path.dirname(task["output"]) or None,
        )

    def _render_subprocess(self, task):
        task['status'] = 'rendering'
        layout_result = None
        target_render = None
        try:
            if self.stoped:
                self._gather(task, "stopped", desc=RenderStopped("用户已停止渲染"))
                return
            render_args = task['args']
            mode:str = task['mode']
            video:VideoInfo = task.get('video')
            output:str = task.get('output')

            engine = get_engine(mode)
            TargetRender = engine.load()

            target_args = render_args
            render_kwargs = {}
            if engine.overlay:
                output_dir = os.path.dirname(output)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                layout_keys = {
                    'include_events',
                    'danmaku', 'gift', 'superchat', 'member',
                    'r2l', 'slide_left', 'slide_right',
                }
                target_args = {
                    key: value for key, value in render_args.items()
                    if key != 'engine' and key not in layout_keys
                }
                if not engine.raster:
                    for key in (
                        'emoji_pack', 'emoji_font', 'min_fps',
                    ):
                        target_args.pop(key, None)

                events_path = getattr(video, 'dm_file_id', None)
                if events_path and str(events_path).lower().endswith('.jsonl'):
                    declared_duration = float(
                        getattr(video, 'duration', 0) or 0
                    )
                    timeline_duration = FFprobe.get_timeline_duration(
                        video.path
                    )
                    if timeline_duration > 0:
                        video.duration = timeline_duration
                        if abs(timeline_duration - declared_duration) >= 0.05:
                            self.logger.info(
                                '媒体时间线时长修正: %.3fs → %.3fs',
                                declared_duration,
                                timeline_duration,
                            )
                    layout_result = self._compile_layout(task, mode)
                    render_kwargs['render_plan'] = layout_result.render_plan_path
                    render_kwargs['canvas_resolution'] = (
                        layout_result.next_state.canvas_resolution
                    )
                    track_stats = layout_result.track_stats
                    r2l_stats = track_stats.get('r2l', {})
                    slide_left_stats = track_stats.get('slide_left', {})
                    slide_right_stats = track_stats.get('slide_right', {})
                    other_dropped = layout_result.unrouted_dropped_count
                    other_summary = (
                        f'；其他丢弃 {other_dropped} 条'
                        if other_dropped
                        else ''
                    )
                    self.logger.info(
                        '布局编译完成: 共布局 %s 条，丢弃 %s 条'
                        '（R2L: 布局 %s/丢弃 %s；'
                        'Slide L: 布局 %s/丢弃 %s；'
                        'Slide R: 布局 %s/丢弃 %s%s）',
                        layout_result.event_count,
                        layout_result.dropped_count,
                        r2l_stats.get('accepted', 0),
                        r2l_stats.get('dropped', 0),
                        slide_left_stats.get('accepted', 0),
                        slide_left_stats.get('dropped', 0),
                        slide_right_stats.get('accepted', 0),
                        slide_right_stats.get('dropped', 0),
                        other_summary,
                    )

            target_render = TargetRender(**target_args)
            verb = engine.label
            log_task_start = self.logger.debug if engine.raster else self.logger.info
            log_task_start(f'准备{verb}任务: {video.path}')
            output_dir = os.path.dirname(output)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            self._render_class[task['uuid']] = target_render

            # 进度回调：渲染引擎按视频时长/帧数回报百分比，写到 task['progress'] 供 WebUI 轮询展示
            def on_progress(pct, _task=task):
                _task['progress'] = f'{int(pct)}%'

            # 重试：失败最多重试 retry 次（默认3），都失败才回 render/error → liveevents 挂起转人工
            retry = render_args.get('retry', 3)
            status, info = False, '未执行'
            for attempt in range(retry + 1):
                task['progress'] = None   # 每次（重）试从头开始计进度
                try:
                    status, info = target_render.render_one(
                        video=video,
                        output=output,
                        progress_cb=on_progress,
                        **render_kwargs,
                    )
                except Exception as e:
                    status, info = False, e
                    self.logger.exception(e)
                if (
                    status
                    or self.stoped
                    or target_render.is_stopped()
                    or isinstance(info, RenderStopped)
                ):
                    break
                if attempt < retry:
                    self.logger.warning(f'{verb}失败，重试 {attempt + 1}/{retry}: {video.path}')
                    time.sleep(5)
            self._render_class.pop(task['uuid'], None)

            cancelled = (
                self.stoped
                or target_render.is_stopped()
                or isinstance(info, RenderStopped)
            )
            if cancelled:
                self._gather(task, "stopped", desc=RenderStopped("用户已停止渲染"))
            elif status:
                self._gather(task, 'info', desc=info)
            elif engine.fallback:
                self.logger.warning(
                    "%s失败，回退到 %s: %s", mode, engine.fallback, info
                )
                if layout_result is not None:
                    original_layout = layout_result
                    layout_result = self._compile_layout(task, engine.fallback)
                    original_layout.cleanup()
                    render_kwargs["render_plan"] = layout_result.render_plan_path
                    render_kwargs["canvas_resolution"] = (
                        layout_result.next_state.canvas_resolution
                    )
                fallback = get_engine(engine.fallback).load()(**target_args)
                self._render_class[task["uuid"]] = fallback
                try:
                    fallback_status, fallback_info = fallback.render_one(
                        video=video,
                        output=output,
                        progress_cb=on_progress,
                        **render_kwargs,
                    )
                    if (
                        self.stoped
                        or fallback.is_stopped()
                        or isinstance(fallback_info, RenderStopped)
                    ):
                        self._gather(
                            task, "stopped", desc=RenderStopped("用户已停止渲染")
                        )
                    else:
                        self._gather(
                            task,
                            "info" if fallback_status else "error",
                            desc=fallback_info,
                        )
                finally:
                    self._render_class.pop(task["uuid"], None)
            else:
                self._gather(task, 'error', desc=info)
        except KeyboardInterrupt:
            if target_render:
                target_render.stop()
            self._gather(task, "stopped", desc=RenderStopped("用户已停止渲染"))
        except Exception as e:
            if self.stoped or (
                target_render is not None and target_render.is_stopped()
            ):
                self._gather(task, "stopped", desc=RenderStopped("用户已停止渲染"))
            else:
                self.logger.exception(e)
                self._gather(task, "error", desc=e)
        finally:
            self._render_class.pop(task["uuid"], None)
            if layout_result:
                layout_result.cleanup()

    def stop(self):
        self.stoped = True
        for render in list(self._render_class.values()):
            render.stop()
        self.render_executors.shutdown(wait=False)
        self.layout_tracks.reset()

        with self._lock:
            for uuid, task in self.render_tasks.items():
                if uuid not in self.failed_tasks:
                    self.failed_tasks[uuid] = task

        self.logger.info('Render stopped.')
