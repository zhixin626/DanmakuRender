import logging
import threading
import queue
import time
import json
from concurrent.futures import ThreadPoolExecutor
from os.path import join, exists
from datetime import datetime
from typing import Tuple

from DMR.utils import VideoInfo, PipeMessage, DateTimeEncoder, DateTimeDecoder, uuid


class Uploader():
    def __init__(self,
                 pipe:Tuple[queue.Queue, queue.Queue],
                 nuploaders:int=1,
                 **kwargs,
                 ) -> None:
        
        self.nuploaders = int(nuploaders)
        self.send_queue, self.recv_queue = pipe
        self.logger = logging.getLogger(__name__)
        self.kwargs = kwargs

        self.stoped = True
        self._piperecvprocess = None
        self._uploader_pool = {}
        self.upload_tasks = {}
        self.failed_tasks = {}
        self.failed_tasks_file = '.temp/failed_uploads.json'
        # self.load_failed_tasks()
        
        self.upload_executors = ThreadPoolExecutor(max_workers=self.nuploaders)
        self._lock = threading.Lock()

    def load_failed_tasks(self):
        if exists(self.failed_tasks_file):
            try:
                with open(self.failed_tasks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f, cls=DateTimeDecoder)
                    # Restore datetime objects and VideoInfo objects
                    for uuid, task in data.items():
                        if 'files' in task:
                            restored_files = []
                            # Wrap in VideoInfo
                            for f in task['files']:
                                restored_files.append(VideoInfo(**f))
                            task['files'] = restored_files
                            # Also update files in config
                            # if 'config' in task and 'files' in task['config']:
                            #     task['config']['files'] = restored_files
                        self.failed_tasks[uuid] = task
                self.logger.info(f'Loaded {len(self.failed_tasks)} failed upload tasks.')
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
                self.upload_tasks[task['uuid']] = task
                if task.get('stream_queue'):
                    threading.Thread(target=self._upload_subprocess, args=(task,), daemon=True).start()
                else:
                    self.upload_executors.submit(self._upload_subprocess, task)
                return True
            return False

    def delete_failed_task(self, uuid):
        with self._lock:
            if uuid in self.failed_tasks:
                self.failed_tasks.pop(uuid)
                # self.save_failed_tasks()
                return True
            return False

    def stop_task(self, uuid):
        """手动停止一个正在排队或正在上传的任务。"""
        with self._lock:
            task = self.upload_tasks.get(uuid)
            if not task:
                return False
            task['_cancelled'] = True
            task['status'] = 'cancelled'
            uploader = task.get('_uploader')
            upload_group = task.get('upload_group')

        # 在锁外停止底层上传进程，避免阻塞
        if uploader is not None:
            try:
                uploader.stop()
            except Exception as e:
                self.logger.warning(f'停止上传任务 {uuid} 时出错: {e}')
            # 该 group 的上传器实例已被打断，移出池子，后续任务会重建一个干净的实例
            with self._lock:
                pooled = self._uploader_pool.get(upload_group)
                if pooled and pooled['class'] is uploader:
                    self._uploader_pool.pop(upload_group, None)
        else:
            # 还在排队、尚未开始上传：直接从队列移除（执行线程启动时会因 _cancelled 标志跳过）
            with self._lock:
                self.upload_tasks.pop(uuid, None)

        self.logger.info(f'已发送停止指令到上传任务 {uuid}.')
        return True

    def _pipeRecvMonitor(self):
        while self.stoped == False and self.recv_queue is not None:
            message:PipeMessage = self.recv_queue.get()
            try:
                if message.target == 'uploader':
                    if message.event == 'newtask':
                        self.add_task(message)
            except Exception as e:
                self.logger.error(f'Message:{message} raise an error.')
                self.logger.exception(e)

    def start(self):
        self.stoped = False
        self._piperecvprocess = threading.Thread(target=self._pipeRecvMonitor, daemon=True)
        self._piperecvprocess.start()

    def _free_uploader_pool(self):
        with self._lock:
            for upload_group in list(self._uploader_pool.keys()):
                expire = self._uploader_pool[upload_group]['expire']
                if expire > 0 and time.time() - self._uploader_pool[upload_group]['ctime'] > expire:
                    self._uploader_pool[upload_group]['class'].stop()
                    self._uploader_pool.pop(upload_group)
                    self.logger.debug(f'Uploader {upload_group} expired and removed.')

    def add_task(self, msg:PipeMessage):
        with self._lock:
            # 这里的msg是liveevents里checkforupload里构造的upload_msg
            # 这里的config是 upload_msg 里的 data
            config = msg.data
            file = config.get('files')[0]
            stream_queue = None
            if hasattr(file, 'stream_queue') and file.stream_queue is not None:
                stream_queue = file.stream_queue
            task = {
                'uuid': uuid(),
                'source': msg.source,
                'request_id': msg.request_id,
                'upload_group': config.get('upload_group'),
                'engine': config.get('engine', 'biliuprs'),
                'args': config.get('args', {}),
                'files': config.get('files'),
                'stream_queue': stream_queue,
                'config': config, # task里的config是 upload_msg 里的 data
                'status': 'waiting',
            }
            self.upload_tasks[task['uuid']] = task
            if stream_queue:
                threading.Thread(target=self._upload_subprocess, args=(task,), daemon=True).start()
            else:
                self.upload_executors.submit(self._upload_subprocess, task)

    def _pipeSend(self, event, msg, target='engine', request_id=None, dtype=None, data=None, **kwargs):
        if self.send_queue:
            msg = PipeMessage(
                source='uploader',
                target=target,
                event=event,
                request_id=request_id,
                msg=msg,
                dtype=dtype,
                data=data,
                **kwargs,
            )
            self.send_queue.put(msg)

    def _gather(self, task, status, desc=''):
        with self._lock:
            self.upload_tasks.pop(task['uuid'], None)
            if status == 'cancelled':
                # 手动停止：不计入失败任务，仅通知来源方释放等待状态
                if task.get('stream_queue'):
                    task['stream_queue'] = None
                self._pipeSend(
                    event='error',
                    msg=f"上传已被手动停止: {[f.path for f in task['files']]}",
                    target=task['source'],
                    request_id=task['request_id'],
                    dtype='str',
                    data='manually stopped',
                )
            elif status == 'error':
                # Save to failed tasks
                # ignore stream uploads
                if task.get('stream_queue'):
                    task['stream_queue'] = None
                    # task['config']['stream_queue'] = None
                else:
                    self.failed_tasks[task['uuid']] = task
                    # self.save_failed_tasks()

                self._pipeSend(
                    event='error',
                    msg=f"上传视频 {[f.path for f in task['files']]} 时出现错误:{desc}",
                    target=task['source'],
                    request_id=task['request_id'],
                    dtype=str(type(desc)),
                    data=desc,
                )
            else:
                self._pipeSend(
                    event='end',
                    msg=f"视频 {[f.path for f in task['files']]} 上传完成: {desc}",
                    target=task['source'],
                    request_id=task['request_id'],
                    dtype='dict',
                    data={
                        'config': task['config'], # task里的config是 upload_msg 里的 data
                        "bvid":desc, # zhixin 增加bvid
                    },
                )

    def _upload_subprocess(self, task): # 这里的task是add_task里构造的那个字典
        if task.get('_cancelled'):       # 启动前已被手动停止，直接跳过
            self._gather(task, 'cancelled')
            return
        task['status'] = 'uploading'
        try:
            upload_args = task['args']  # 重要参数传入biliwebapi和biliuprs（初始化+upload函数）
            upload_group:str = task['upload_group']

            with self._lock:
                if upload_group in self._uploader_pool:
                    target_uploader = self._uploader_pool[upload_group]['class']
                    self._uploader_pool[upload_group]['ctime'] = time.time()
                else:
                    engine:str = task['engine']
                    if engine == 'biliuprs':
                        from .biliuprs import biliuprs as TargetUploader
                    elif engine == 'subprocess':
                        from .subprocess_uploader import SubprocessUploader as TargetUploader
                    elif engine == 'youtubev3':
                        from .youtubev3 import youtubev3 as TargetUploader
                    elif engine == 'biliwebapi':
                        from .biliwebapi import BiliWebApi as TargetUploader
                    elif engine == 'acfun':
                        from .acfun import acfun as TargetUploader
                    else:
                        raise ValueError(f'Unknown engine: {engine}')
                    
                    target_uploader = TargetUploader(**upload_args)
                    self._uploader_pool[upload_group] = {
                        'class': target_uploader,
                        'ctime': time.time(),
                        'expire': task.get('expire', 7*24*3600)
                    }

            task['_uploader'] = target_uploader   # 记录底层上传器，便于手动停止

            # 取得上传器后、真正开始上传前再次检查是否已被手动停止
            if task.get('_cancelled'):
                self._gather(task, 'cancelled')
                self._free_uploader_pool()
                return

            files = task['files']
            stream_queue = task.get('stream_queue', None)
            retry = upload_args.get('retry', 0)
            if stream_queue:
                retry = 0       # 流式上传无法重试
            status = info = None

            while retry >= 0:
                try:
                    if stream_queue:
                        self.logger.info(f"正在同步上传 {files[0].title} 至 {upload_args.get('account')}")
                    else:
                        self.logger.info(f"正在上传 {[f.path for f in files]} 至 {upload_args.get('account')}")
                    # logging.debug(task)
                    res = target_uploader.upload(files=files, stream_queue=stream_queue, **upload_args)
                    
                    if len(res) == 3:
                        status, info, _ = res
                    else:
                        status, info = res

                except KeyboardInterrupt:
                    target_uploader.stop()
                    self.stop()
                    return
                except Exception as e:
                    status, info = False, e
                    self.logger.exception(e)
                
                retry -= 1
                if task.get('_cancelled'):   # 被手动停止，立即结束，不再重试
                    break
                if status or self.stoped:
                    break
                elif retry < 0:
                    self.logger.warning(f'上传 {[f.path for f in files]} 时出现错误，跳过上传.')
                    break
                else:
                    self.logger.warning(f'上传 {[f.path for f in files]} 时出现错误，即将重传.')
                    self.logger.debug(info)
                    time.sleep(60)
            
            if task.get('_cancelled'):
                self._gather(task, 'cancelled')
            elif status:
                self._gather(task, 'info', desc=info)
            else:
                self._gather(task, 'error', desc=info)

            self._free_uploader_pool()
        
        except Exception as e:
            self.logger.exception(e)
            self._gather(task, 'error', desc=e)

    def stop(self):
        self.stoped = True
        self.upload_executors.shutdown(wait=False)
        for upload_group in self._uploader_pool:
            self._uploader_pool[upload_group]['class'].stop()
        
        with self._lock:
            for uuid, task in self.upload_tasks.items():
                if uuid not in self.failed_tasks:
                    self.failed_tasks[uuid] = task
            # self.save_failed_tasks()

        self.logger.info('Uploader stopped.')
