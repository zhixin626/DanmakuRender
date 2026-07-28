import json
from datetime import datetime
import logging
import os
import time
import threading
import queue
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from os.path import join, exists
from typing import Tuple

from DMR.utils import (
    VideoInfo,
    DateTimeEncoder,
    DateTimeDecoder,
    FFprobe,
    uuid,
    PipeMessage,
)
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
        self.failed_tasks_file = '.temp/failed_renders.json'
        # self.load_failed_tasks()

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
                            task['video'] = VideoInfo(**task['video'])
                            # Update config as well
                            # if 'config' in task and 'video' in task['config']:
                            #     task['config']['video'] = task['video']
                        self.failed_tasks[uuid] = task
                self.logger.info(f'Loaded {len(self.failed_tasks)} failed render tasks.')
            except Exception as e:
                self.logger.error(f'Failed to load failed tasks: {e}')

    def save_failed_tasks(self):
        try:
            with open(self.failed_tasks_file, 'w', encoding='utf-8') as f:
                json.dump(self.failed_tasks, f, cls=DateTimeEncoder, ensure_ascii=False, indent=4)
        except Exception as e:
            self.logger.error(f'Failed to save failed tasks: {e}')

    def retry_task(self, uuid):
        with self._lock:
            if uuid in self.failed_tasks:
                task = self.failed_tasks.pop(uuid)
                # self.save_failed_tasks()
                
                # Re-submit
                task['status'] = 'waiting'
                self.render_tasks[task['uuid']] = task
                self.render_executors.submit(self._render_subprocess, task)
                return True
            return False

    def delete_failed_task(self, uuid):
        with self._lock:
            if uuid in self.failed_tasks:
                self.failed_tasks.pop(uuid)
                # self.save_failed_tasks()
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
                   if message.event == 'newtask':
                        self.add_task(message)
                   elif message.event == 'layout_end':
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
                'uuid': uuid(),
                'source': source,
                'request_id': request_id,
                'mode': config.get('mode', 'dmrender'),
                'args': config.get('args', {}),
                'video': config.get('video'),
                'output': config.get('output'),
                'config': config,
                'status': 'waiting',
                'progress': None,   # 渲染百分比（'45%'），由渲染引擎回调更新，供 WebUI 展示
            }
            if task['mode'] in ('dmrender', 'emoji_dmrender'):
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
            if status == 'error':
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

    def _render_subprocess(self, task):
        task['status'] = 'rendering'
        layout_result = None
        target_render = None
        try:
            render_args = task['args']
            mode:str = task['mode']
            video:VideoInfo = task.get('video')
            output:str = task.get('output')

            if mode == 'dmrender':
                from .dmrender import DmRender as TargetRender
            elif mode == 'emoji_dmrender':
                from .emoji_dmrender import EmojiDmRender as TargetRender
            elif mode == 'transcode':
                from .transcode import Transcoder as TargetRender
            else:
                raise ValueError(f'未知渲染模式: {mode}')

            target_args = render_args
            render_kwargs = {}
            if mode in ('dmrender', 'emoji_dmrender'):
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
                if mode == 'dmrender':
                    for key in (
                        'emoji_pack', 'emoji_font', 'workers',
                        'chunks_per_worker',
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
                    layout_result = self.layout_tracks.compile_segment(
                        track_key=task.get('layout_track_key'),
                        sequence=task.get('layout_sequence'),
                        video=video,
                        events_path=events_path,
                        layout_config=render_args,
                        engine=mode,
                        engine_config=render_args,
                        work_dir=output_dir or None,
                    )
                    render_kwargs['render_plan'] = layout_result.render_plan_path
                    self.logger.info(
                        '布局编译完成: %s 条，过滤/丢弃 %s 条',
                        layout_result.event_count,
                        layout_result.dropped_count,
                    )

            target_render = TargetRender(**target_args)
            verb = {'dmrender': '渲染', 'emoji_dmrender': '彩色emoji渲染', 'transcode': '转码'}.get(mode, '处理')
            log_task_start = self.logger.debug if mode == 'emoji_dmrender' else self.logger.info
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
                if status or self.stoped:
                    break
                if attempt < retry:
                    self.logger.warning(f'{verb}失败，重试 {attempt + 1}/{retry}: {video.path}')
                    time.sleep(5)
            self._render_class.pop(task['uuid'], None)

            if status:
                self._gather(task, 'info', desc=info)
            else:
                self._gather(task, 'error', desc=info)
        except KeyboardInterrupt:
            if target_render:
                target_render.stop()
        except Exception as e:
            self.logger.exception(e)
            self._gather(task, 'error', desc=e)
        finally:
            if layout_result:
                layout_result.cleanup()

    def stop(self):
        self.stoped = True
        for uuid, render in self._render_class.items():
            render.stop()
        self.render_executors.shutdown(wait=False)
        self.layout_tracks.reset()
        
        with self._lock:
            for uuid, task in self.render_tasks.items():
                if uuid not in self.failed_tasks:
                    self.failed_tasks[uuid] = task
            # self.save_failed_tasks()

        self.logger.info('Render stopped.')
