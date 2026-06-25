import json
import logging
import os
import queue
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from os.path import exists, isdir, isfile, abspath, dirname
from typing import Tuple
from DMR.utils import *

# pending_cleanup.json 存放目录（项目根目录）
_PENDING_CLEANUP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _fmt_delay(seconds: float) -> str:
    """把秒数格式化为自适应的人类可读时间，根据量级显示合适的精度。"""
    seconds = max(0, int(seconds))
    months  = seconds // 2592000          # 30天/月
    days    = (seconds % 2592000) // 86400
    hours   = (seconds % 86400)  // 3600
    minutes = (seconds % 3600)   // 60
    secs    = seconds % 60

    if months:
        return f'{months}个月{days}天' if days else f'{months}个月'
    if days:
        return f'{days}天{hours}小时' if hours else f'{days}天'
    if hours:
        return f'{hours}小时{minutes}分钟' if minutes else f'{hours}小时'
    if minutes:
        return f'{minutes}分{secs}秒' if secs else f'{minutes}分钟'
    return f'{secs}秒'


class Cleaner():
    def __init__(self,
                 pipe: Tuple[queue.Queue, queue.Queue],
                 **kwargs,
                 ) -> None:

        self.send_queue, self.recv_queue = pipe
        self.logger = logging.getLogger('DMR.Cleaner')
        self.kwargs = kwargs
        self.stoped = True
        self.resume_pending = kwargs.get('resume_pending', True)  # 启动时是否恢复未完成的清理任务

        # pending_cleanup 文件名含端口号，多实例互不干扰
        port = kwargs.get('port', '')
        suffix = f'_{port}' if port else ''
        self._pending_file = os.path.join(_PENDING_CLEANUP_DIR, f'pending_cleanup{suffix}.json')

        self._piperecvprocess = None
        self.clean_executors = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()

    def _pipeSend(self, event, msg, target='engine', request_id=None, dtype=None, data=None, **kwargs):
        if self.send_queue:
            msg = PipeMessage(
                source='cleaner',
                target=target,
                event=event,
                request_id=request_id,
                msg=msg,
                dtype=dtype,
                data=data,
                **kwargs,
            )
            self.send_queue.put(msg)

    def _pipeRecvMonitor(self):
        while self.stoped == False and self.recv_queue is not None:
            message: PipeMessage = self.recv_queue.get()
            try:
                if message.target == 'cleaner':
                    if message.event == 'newtask':
                        self.add_task(message)
            except Exception as e:
                self.logger.error(f'Message:{message} raise an error.')
                self.logger.exception(e)

    def start(self):
        self.stoped = False
        self._piperecvprocess = threading.Thread(target=self._pipeRecvMonitor, daemon=True)
        self._piperecvprocess.start()

    # ------------------------------------------------------------------
    # pending_cleanup.json 读写
    # ------------------------------------------------------------------

    def _read_pending(self) -> list:
        """读取 pending_cleanup.json，返回任务列表；文件不存在或损坏则返回空列表"""
        if not exists(self._pending_file):
            return []
        try:
            with open(self._pending_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.warning(f'读取 pending_cleanup.json 失败: {e}')
            return []

    def _save_pending(self, tasks: list):
        """将任务列表写回 pending_cleanup.json"""
        try:
            with open(self._pending_file, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f'写入 pending_cleanup.json 失败: {e}')

    def _append_pending(self, entry: dict):
        """追加一条清理任务到 pending_cleanup.json"""
        with self._lock:
            tasks = self._read_pending()
            tasks.append(entry)
            self._save_pending(tasks)

    def _remove_pending(self, task_id: str):
        """从 pending_cleanup.json 中删除已完成的任务"""
        with self._lock:
            tasks = self._read_pending()
            tasks = [t for t in tasks if t.get('id') != task_id]
            self._save_pending(tasks)

    # ------------------------------------------------------------------
    # 任务管理
    # ------------------------------------------------------------------

    def add_task(self, msg: PipeMessage):
        with self._lock:
            config = msg.data
            method = config.get('method')
            if not method:
                return

            task_id = uuid()
            now = time.time()
            delay = config.get('delay', 0)
            clean_args = config.get('args', {})
            files_info = config.get('files') or []

            # 解析所有实际文件路径（主文件 + 弹幕文件），过滤不存在的文件
            file_paths = []
            for f in files_info:
                if f and f.get('path'):
                    p = abspath(f.get('path'))
                    if exists(p):
                        file_paths.append(p)
                    else:
                        self.logger.debug(f'清理跳过（文件不存在）: {p}')
                dm = f.get('dm_file_id') if f else None
                if dm and exists(dm):
                    file_paths.append(dm)

            # 所有文件都不存在，直接跳过
            if not file_paths:
                self.logger.debug(f'清理任务跳过（所有文件均不存在）: {[f.get("path") for f in files_info if f]}')
                return

            # 解析 dest（move/copy 目标目录），用第一个文件的元信息替换关键字
            dest = clean_args.get('dest', '')
            if dest and files_info and not dest.startswith('*'):
                try:
                    dest = abspath(replace_keywords(dest, files_info[0], replace_invalid=True))
                except Exception:
                    pass

            # 写入 pending_cleanup.json（不加锁，_append_pending 内部有锁）
            pending_entry = {
                'id'      : task_id,
                'method'  : method,
                'files'   : file_paths,
                'dest'    : dest,       # move/copy 目标目录，delete/send2trash 为空
                'created_at': now,
                'delay'   : delay,
                'note'    : f'将于 {datetime.fromtimestamp(now + delay).strftime("%Y-%m-%d %H:%M:%S")} 执行（等待 {_fmt_delay(delay)}）',
            }

        # 写文件在锁外执行（_append_pending 内部有自己的锁）
        self._append_pending(pending_entry)

        task = {
            'id'        : task_id,
            'source'    : msg.source,
            'request_id': msg.request_id,
            'method'    : method,
            'args'      : clean_args,
            'files'     : files_info,
            'config'    : config,
        }

        if delay > 0:
            threading.Timer(delay, self.clean_executors.submit,
                            args=(self._clean_subprocess, task)).start()
        else:
            self.clean_executors.submit(self._clean_subprocess, task)

    def _clean_subprocess(self, task):
        try:
            method = task['method']
            clean_args = task['args']
            dst = clean_args.get('dest')
            cleaned_files = []
            for file in task['files']:
                file: FileInfo
                if not exists(file.path):
                    self.logger.debug(f'文件 {file.path} 不存在，跳过清理.')
                    continue

                self.logger.info(f'正在清理文件: {method} {file.path}.')
                src = abspath(file.path)
                if dst and not dst.startswith('*'):
                    dst = abspath(replace_keywords(dst, file, replace_invalid=True))
                    if not exists(dst):
                        self.logger.info(f'目标文件夹 {dst} 不存在，即将自动创建.')
                        os.makedirs(dst)

                files = [src]
                dm_file = file.get('dm_file_id')
                if dm_file and exists(dm_file):
                    self.logger.info(f'正在清理弹幕文件: {method} {dm_file}.')
                    files.append(dm_file)
                cleaned_files.extend(files)

                for f in files:
                    if method == 'move':
                        from .move import move
                        move.move(f, dst)
                    elif method == 'copy':
                        from .copy import copy
                        copy.copy(f, dst)
                    elif method == 'delete':
                        from .delete import delete
                        delete.delete(f)
                    elif method == 'send2trash':
                        from send2trash import send2trash
                        send2trash(f)
                    elif method == 'custom':
                        from ..utils import runcmd
                        cmds = [replace_keywords(str(x), file) for x in clean_args.get('command')]
                        wait = clean_args.get('wait', True)
                        p = runcmd.runcmd(cmds, wait=wait, **clean_args.get('subprocess_kwargs', {}))
                        if wait and p.returncode != 0:
                            raise RuntimeError(f'命令执行失败: {cmds}')

            self._pipeSend('end', f'清理完成：{method} {cleaned_files} -> {dst}.',
                           target=task['source'], request_id=task['request_id'])
        except Exception as e:
            self.logger.exception(e)
            self._pipeSend('error', f'清理错误 {e}.', target=task['source'],
                           request_id=task['request_id'], dtype='Exception', data=e)
        finally:
            # 无论成功失败，都从 pending 中移除（失败的任务不重试）
            self._remove_pending(task['id'])

    def execute_pending(self):
        """
        程序启动时调用：读取 pending_cleanup.json，重新提交尚未执行的清理任务。
        根据 created_at + delay 计算剩余等待时间，已过期则立即执行。
        """
        tasks = self._read_pending()
        if not tasks:
            return

        self.logger.info(f'发现 {len(tasks)} 条未完成的清理任务，开始恢复执行...')
        for entry in tasks:
            try:
                task_id    = entry['id']
                method     = entry['method']
                files      = entry.get('files', [])
                dest       = entry.get('dest', '')
                created_at = entry.get('created_at', 0)
                delay      = entry.get('delay', 0)
                note       = entry.get('note', '')

                # 计算剩余等待时间
                elapsed  = time.time() - created_at
                remaining = max(0, delay - elapsed)

                exec_time = datetime.fromtimestamp(created_at + delay).strftime('%Y-%m-%d %H:%M:%S')
                self.logger.debug(
                    f'恢复清理任务: {method} {files}'
                    f'（原定 {exec_time} 执行，剩余等待 {_fmt_delay(remaining)}）'
                )

                # 过滤不存在的文件，若全部不存在则直接删除记录
                from DMR.utils.dataclass import FileInfo
                existing_files = [p for p in files if exists(p)]
                if not existing_files:
                    self.logger.debug(f'清理任务 {task_id} 所有文件均不存在，直接移除记录。')
                    self._remove_pending(task_id)
                    continue

                task = {
                    'id'        : task_id,
                    'source'    : 'engine',
                    'request_id': None,
                    'method'    : method,
                    'args'      : {'dest': dest},
                    'files'     : [FileInfo(path=p) for p in existing_files],
                    'config'    : {},
                }

                if remaining > 0:
                    threading.Timer(remaining, self.clean_executors.submit,
                                    args=(self._clean_subprocess, task)).start()
                else:
                    self.clean_executors.submit(self._clean_subprocess, task)

            except Exception as e:
                self.logger.warning(f'恢复清理任务失败，跳过: {e}')

    def stop(self):
        self.stoped = True
