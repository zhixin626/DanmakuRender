"""Merger 插件：把合并从 liveevents 里抽出来的独立执行模块。

与 render/uploader/cleaner 对等：收 'newtask'（合并 job）→ 后台线程做实际合并
→ 回发 'merger/end'（带合并后的 VideoInfo 有序列表）或 'merger/error'。
**只负责执行，不读 state_dict**，编排仍在 liveevents（由 request_id 关联）。

合并策略（_merge_pipeline，三段式）：
  1. 计划：probe_seg 探测所有段（全程只探测一次），group_by_format 按「连续同分辨率、同编码格式」分组，
     split_by_duration 再按时长上限（max_duration，默认 10 小时）切开，每组一个产物；
  2. 执行：每组由 merge_group 产出一个中间产物——timescale 无损对齐后 concat_copy，
     单段组直接复制直通（不重编码、不改动源文件）；放大音频时整组音频统一增益、只编码一次；
  3. 提交：模板命名（多产物自动加 _partN）→ probe 建 VideoInfo →
     **全部成功后**才统一 send2trash 源文件（remove_source 时）。
  源文件在提交前绝不被修改/移动，失败重试安全。

job data 约定（由 liveevents 发来；pipe 为进程内 queue，可直接传 Python 对象）：
  {
    'taskname'        : str,
    'paths'           : [str, ...],     # 有序的待合并文件路径
    'merge_type'      : str,            # 'dm_video' / 'src_video'，回显给 onMergeEnd
    'args'            : {               # 整个 merge_args（仿 uploader 的 'args'），merger 自己取：
        'is_amplify': bool, 'extra_gain_db': float,
        'file_name_template': str,      # 用 stime 格式化成输出文件名（多产物自动加 _partN）
        'remove_source': bool,          # 全部合并成功后是否删源（send2trash，默认 True）
        'max_duration': float,          # 单个产物时长上限（秒），默认 36000=10 小时；0 不限制
    },
    'new_video_meta'  : {               # 用于构造合并后的 VideoInfo（会话/分组级字段）
        'dtype'     : str, 'stime': datetime, 'etime': datetime, 'totaltime': str,
        'segment_id': int, 'taskname': str, 'group_id': str,
        'streamer'  : StreamerInfo, 'title': str,
    }
  }
回发 'end' 的 data：{'outputs': [<VideoInfo>, ...], 'merge_type': vt}，列表按时间顺序排列。
"""
import logging
import queue
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from os.path import getsize
from pathlib import Path
from typing import List, Tuple

from send2trash import send2trash

from DMR.utils import *
from DMR.utils.ffprobe import probe_media
from DMR.utils.utils import rename_safe
from .video_merge import (probe_seg, group_by_format, describe_format, merge_group,
                          split_by_duration, DEFAULT_MAX_DURATION)


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

    def _merge_pipeline(self, paths: List[str], args: dict, m: dict) -> List[VideoInfo]:
        """执行一次完整合并，返回按时间顺序排列的 VideoInfo 列表。
        探测→分组→逐组产出→(可选)响度→命名→全部成功后统一删源；
        源文件在删源前绝不被修改/移动，本函数可安全整体重试（每次自带独立工作目录）。"""
        metas = [probe_seg(p) for p in paths]
        groups = group_by_format(metas)
        if len(groups) > 1:
            self.logger.warning(
                '分段格式不一致，强行无损拼接会花屏，按连续一致的段分成 %d 组分别合并：%s',
                len(groups),
                '；'.join(f'{len(g)} 段 {describe_format(g[0])}' for g in groups))
        max_duration = args.get('max_duration', DEFAULT_MAX_DURATION)
        n_before = len(groups)
        groups = split_by_duration(groups, max_duration)
        if len(groups) > n_before:
            self.logger.info(
                '单个视频时长上限 %.1f 小时，按时长切成 %d 个产物：%s',
                max_duration / 3600, len(groups),
                '；'.join(f'{len(g)} 段 {sum(x.get("dur") or 0 for x in g) / 3600:.2f} 小时' for g in groups))
        for g in groups:
            if len(g) == 1 and max_duration and (g[0].get('dur') or 0) > max_duration:
                self.logger.warning('单段 %s 本身就超过时长上限，无法无损再切，原样输出', g[0]['path'])

        out_dir = Path(paths[0]).parent
        # 工作目录建在输出同盘：避免跨盘复制，收尾改名是瞬时原地 rename
        workdir = Path(tempfile.mkdtemp(prefix='.merge_tmp_', dir=str(out_dir)))
        try:
            # === 执行：每组产出一个中间产物（都落在工作目录里） ===
            products = []          # (中间产物路径, 无模板时的兜底文件名)
            amplify = ((-1, args.get('extra_gain_db', 0))
                       if args.get('is_amplify') else None)
            for gi, group in enumerate(groups):
                product = merge_group(group, workdir / f'group_{gi:02d}.mp4', workdir, amplify)
                products.append((product, f"{Path(group[0]['path']).stem}_merged"))

            # === 收尾：模板命名（多产物加 _partN）+ probe 元信息 + 构造 VideoInfo ===
            tmpl = args.get('file_name_template')
            outputs = []
            for i, (product, fallback) in enumerate(products, 1):
                final_name = None
                if tmpl:
                    try:
                        final_name = tmpl.format(stime=m.get('stime'))
                    except Exception:
                        final_name = None
                if not final_name:
                    final_name = fallback
                if len(products) > 1:
                    final_name = f'{final_name}_part{i}'
                dst_path = str(out_dir / f'{final_name}{product.suffix}')
                renamed = rename_safe(str(product), dst_path)
                if not renamed:
                    raise RuntimeError(f'合并产物改名失败：{product} → {dst_path}')
                output_path = renamed

                meta = probe_media(output_path)
                outputs.append(VideoInfo(
                    path       = str(output_path),
                    dtype      = m.get('dtype'),
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
                ))

            # === 提交：全部成功才删源。删源失败只告警不中断——产物已成型，
            # 重试整条流水线反而会在半删状态上重跑，留个源文件是更小的代价 ===
            if args.get('remove_source', True):
                for p in paths:
                    try:
                        send2trash(str(p))
                    except Exception as e:
                        self.logger.warning(f'删除源文件失败（已跳过）：{p}，{e}')
            return outputs
        finally:
            # 成功时产物已改名移出，目录里只剩残渣；失败时把本轮中间产物全部清掉
            shutil.rmtree(workdir, ignore_errors=True)

    def _merge_subprocess(self, task):
        data = task['data']
        vt = data.get('merge_type')
        args = data.get('args', {}) or {}        # 整个 merge_args，参数都从这里取
        m = data.get('new_video_meta', {})
        # 重试：失败最多重试 retry 次（默认3），都失败才回 merger/error → liveevents 挂起转人工。
        # 源文件在流水线提交前不被改动，整体重试是安全的。
        retry = args.get('retry', 3)
        last_err = None
        for attempt in range(retry + 1):
            try:
                paths = list(data.get('paths') or [])
                if len(paths) < 1:
                    raise RuntimeError('合并任务没有可用的文件路径')

                self.logger.info(f'开始合并类型 {vt}：共 {len(paths)} 段')
                outputs = self._merge_pipeline(paths, args, m)

                self.logger.info(f'类型 {vt} 合并成功 → {[o.path for o in outputs]}')
                self._pipeSend('end', f'视频合并完成：{[o.path for o in outputs]}',
                               target=task['source'], request_id=task['request_id'],
                               dtype='dict', data={'outputs': outputs, 'merge_type': vt})
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
