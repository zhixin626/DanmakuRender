"""Merger 插件：把合并从 liveevents 里抽出来的独立执行模块。

与 render/uploader/cleaner 对等：收 'newtask'（合并 job）→ 后台线程做实际合并
（merge_mp4 + 可选 amplify + 重命名 + probe）→ 回发 'merger/end'（带合并后的 VideoInfo）
或 'merger/error'。**只负责执行，不读 state_dict**，编排仍在 liveevents（由 request_id 关联）。

job data 约定（由 liveevents 发来；pipe 为进程内 queue，可直接传 Python 对象）：
  {
    'taskname'        : str,
    'paths'           : [str, ...],     # 有序的待合并文件路径
    'merge_type'      : str,            # 'dm_video' / 'src_video'，回显给 onMergeEnd
    'args'            : {               # 整个 merge_args（仿 uploader 的 'args'），merger 自己取：
        'is_amplify': bool, 'extra_gain_db': float,
        'file_name_template': str,      # 用 stime 格式化成输出文件名
        'remove_source': bool,          # 合并后是否删源（send2trash，默认 True）
    },
    'new_video_meta'  : {               # 用于构造合并后的 VideoInfo（会话/分组级字段）
        'dtype'     : str, 'stime': datetime, 'etime': datetime, 'totaltime': str,
        'segment_id': int, 'taskname': str, 'group_id': str,
        'streamer'  : StreamerInfo, 'title': str,
    }
  }
回发 'end' 的 data：{'output': <VideoInfo>, 'merge_type': vt}
"""
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from os.path import getsize, splitext
from pathlib import Path
from typing import Tuple

from DMR.utils import *
from DMR.utils.video_merge import merge_mp4, amplify_mp4, probe_media
from DMR.utils.utils import rename_safe


class Merger():
    def __init__(self,
                 pipe: Tuple[queue.Queue, queue.Queue],
                 **kwargs,
                 ) -> None:
        self.send_queue, self.recv_queue = pipe
        self.logger = logging.getLogger('DMR.Merger')
        self.kwargs = kwargs
        self.stoped = True
        self._piperecvprocess = None
        # 合并很重，串行执行（与 cleaner 同思路）
        self.merge_executors = ThreadPoolExecutor(max_workers=1)

    def _pipeSend(self, event, msg, target='engine', request_id=None, dtype=None, data=None, **kwargs):
        if self.send_queue:
            self.send_queue.put(PipeMessage(
                source='merger', target=target, event=event,
                request_id=request_id, msg=msg, dtype=dtype, data=data, **kwargs,
            ))

    def _pipeRecvMonitor(self):
        while self.stoped is False and self.recv_queue is not None:
            message: PipeMessage = self.recv_queue.get()
            try:
                if message.target == 'merger' and message.event == 'newtask':
                    self.add_task(message)
            except Exception as e:
                self.logger.error(f'Message:{message} raise an error.')
                self.logger.exception(e)

    def start(self):
        self.stoped = False
        self._piperecvprocess = threading.Thread(target=self._pipeRecvMonitor, daemon=True)
        self._piperecvprocess.start()

    def add_task(self, msg: PipeMessage):
        task = {
            'source'    : msg.source,
            'request_id': msg.request_id,
            'data'      : msg.data or {},
        }
        self.merge_executors.submit(self._merge_subprocess, task)

    def _merge_subprocess(self, task):
        data = task['data']
        vt = data.get('merge_type')
        args = data.get('args', {}) or {}        # 整个 merge_args，参数都从这里取
        m = data.get('new_video_meta', {})
        # 重试：失败最多重试 retry 次（默认3），都失败才回 merger/error → liveevents 挂起转人工。
        # 注意：merge_mp4 成功后会删源文件，故若失败发生在删源之后（amplify/rename 阶段），
        # 重试会因源已不在而继续失败，最终转人工——这种情况人工那边其实合并产物可能已生成。
        retry = args.get('retry', 3)
        last_err = None
        for attempt in range(retry + 1):
            try:
                paths = list(data.get('paths') or [])
                if len(paths) < 1:
                    raise RuntimeError('合并任务没有可用的文件路径')

                self.logger.info(f'开始合并类型 {vt}：共 {len(paths)} 段')
                output_path = merge_mp4(paths, remover=args.get('remove_source', True))

                if args.get('is_amplify'):
                    output_path = amplify_mp4(
                        output_path, target_db=-1,
                        extra_gain_db=args.get('extra_gain_db', 0), remover=True)

                # 用模板 + stime 生成目标文件名并安全重命名
                tmpl = args.get('file_name_template')
                if tmpl:
                    try:
                        final_name = tmpl.format(stime=m.get('stime'))
                    except Exception:
                        final_name = None
                    if final_name:
                        op = Path(output_path)
                        dst_path = str(op.parent / f'{final_name}{op.suffix}')
                        renamed = rename_safe(str(output_path), dst_path)
                        output_path = renamed if renamed else output_path

                meta = probe_media(output_path)

                newvideo = VideoInfo(
                    path       = str(output_path),
                    dtype      = m.get('dtype', vt),
                    file_id    = uuid(),
                    size       = getsize(output_path),
                    ctime      = datetime.now(),
                    dm_file_id = "",
                    duration   = meta.get('duration') or 0,
                    segment_id = m.get('segment_id'),
                    taskname   = m.get('taskname'),
                    group_id   = m.get('group_id'),
                    streamer   = m.get('streamer'),
                    title      = m.get('title'),
                    resolution = meta.get('resolution') or (0, 0),
                )

                self.logger.info(f'类型 {vt} 合并成功 → {output_path}')
                self._pipeSend('end', f'视频合并完成：{output_path}',
                               target=task['source'], request_id=task['request_id'],
                               dtype='dict', data={'output': newvideo, 'merge_type': vt})
                return
            except Exception as e:
                last_err = e
                self.logger.exception(e)
                if attempt < retry:
                    self.logger.warning(f'合并类型 {vt} 失败，重试 {attempt + 1}/{retry}')
                    time.sleep(5)
        self._pipeSend('error', f'合并类型 {vt} 失败（重试{retry}次后）: {last_err}',
                       target=task['source'], request_id=task['request_id'],
                       dtype='Exception', data={'merge_type': vt, 'error': str(last_err)})

    def stop(self):
        self.stoped = True
