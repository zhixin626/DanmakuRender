import os
import sys
import queue
import logging
import threading
import time
import subprocess

from .Cleaner import Cleaner
from .Downloader import Downloader
from .Render import Render
from .Uploader import Uploader
from .Task import ReplayTask
from .WebService import WebService
from .utils import *


class DMREngine(): # 被上层__init__调用 先被init初始化，后add_plugin，add_task
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.task_dict = {}
        self.plugin_dict = {}
        self.recv_queue = None
        self.stoped = True
        
    def pipeSend(self, message:PipeMessage):  # 第一次是被自己的add_task调用，发送给replay_task一个ready信息
        target = message.target
        self.logger.debug(message)
        if target == 'engine':
            self.recv_queue.put(message)
        elif target.startswith('replay/'):
            taskname = target.split('/')[1]
            if taskname not in self.task_dict:
                self.logger.error(f'Task {taskname} not exists.')
                return
            self.task_dict[taskname]['send_queue'].put(message)
        elif target == 'render':
            self.plugin_dict['render']['send_queue'].put(message)
        elif target == 'uploader':
            self.plugin_dict['uploader']['send_queue'].put(message)
        elif target == 'cleaner':
            self.plugin_dict['cleaner']['send_queue'].put(message)
        elif target == 'downloader':  # onReady 的信息会走这里（target=downloader）
            self.plugin_dict['downloader']['send_queue'].put(message)
        else:
            # raise Exception(f'Unknown target {target}.')
            self.logger.error(f'Unknown target {target}.')

    def _pipeRecvMonitor(self):
        while not self.stoped:
            message:PipeMessage = self.recv_queue.get()
            try:
                if message.target == 'engine':
                    if message.event == 'info':
                        self.logger.info(message.msg)
                    elif message.event == 'addtask':
                        self.add_task(message.data['taskname'], message.data['config'])
                    elif message.event == 'deltask':
                        self.del_task(message.data)
                else:
                    self.pipeSend(message) # onReady 的信息会走这里（target=downloader）
            except Exception as e:
                self.logger.error(f'Message:{message} raise an error.')
                self.logger.exception(e)
    
    def start(self): # 被上层__init__调用（1）
        self.stoped = False
        self.recv_queue = queue.Queue()
        self._piperecvprocess = threading.Thread(target=self._pipeRecvMonitor, daemon=True)
        self._piperecvprocess.start()
        self.logger.debug('DMR engine started.')

        for name, plugin in self.plugin_dict.values():
            if plugin['status'] == 0:
                plugin['class'].start()
                self.plugin_dict['name']['status'] = 1
                self.logger.debug(f'Plugin {name} started.')
        
        for name, task in self.task_dict.values():
            if task['status'] == 0:
                task['class'].start()
                self.task_dict['name']['status'] = 1
                self.pipeSend(PipeMessage('engine', f'replay/{name}', 'ready'))
                self.logger.debug(f'Task {name} started.')

    def add_plugin(self, name, config): # 被上层__init__调用 （2）
        send_queue = queue.Queue()
        if name == 'render':
            plugin = Render((self.recv_queue, send_queue), **config)
        elif name == 'uploader':
            plugin = Uploader((self.recv_queue, send_queue), **config)
        elif name == 'cleaner':
            plugin = Cleaner((self.recv_queue, send_queue), **config)
        elif name == 'downloader':
            plugin = Downloader((self.recv_queue, send_queue), **config)
        elif name == 'webservice':
            plugin = WebService((self.recv_queue, send_queue), engine=self, **config)
        else:
            self.logger.error(f'Unknown plugin {name}.')
            # raise Exception(f'Unknown plugin {name}.')
        if self.stoped == False:
            plugin.start()
            self.logger.debug(f'Plugin {name} started.')
        else:
            self.logger.debug(f'Plugin {name} created.')
        self.plugin_dict[name] = {
            'class': plugin,
            'config': config,
            'send_queue': send_queue,
            'status': 0 if self.stoped else 1,
        }

    def add_task(self, taskname, config): # 被上层DanmakuRender()调用 （3） ，这里传入的config就是每一个任务的config
        send_queue = queue.Queue()
        task = ReplayTask(taskname, config, (self.recv_queue, send_queue))  # 初始化
        self.task_dict[taskname] = {
            'class': task,
            'config': config,
            'send_queue': send_queue,
            'status': 0 if self.stoped else 1,
        }
        if self.stoped == False:
            task.start()                                                    # 初始化后直接开启（task开启了聆听）
            self.pipeSend(PipeMessage('engine', f'replay/{taskname}', 'ready')) # 重点！！发送ready信息给replaytask
            self.logger.debug(f'Task {taskname} started.')
        else:
            self.logger.debug(f'Task {taskname} created.')

    def del_task(self, taskname):
        if taskname in self.task_dict:
            self.pipeSend(PipeMessage('engine', f'replay/{taskname}', 'exit'))
            # self.task_dict[taskname]['class'].stop()
            del self.task_dict[taskname]
            self.logger.debug(f'Task {taskname} deleted.')
        else:
            self.logger.debug(f'Task {taskname} not exists.')

    def is_task_idle(self, taskname: str) -> bool:
        """检查指定任务是否空闲（无直播、无渲染/上传队列）。"""
        task_info = self.task_dict.get(taskname)
        if not task_info:
            return True  # 任务不存在视为空闲
        event_class = task_info['class'].event_class
        for group_status in event_class.live_status.values():
            if not group_status.get('is_live_end', True):
                return False
        if event_class.state_dict:
            return False
        if event_class.ended_dict:
            return False
        return True

    def is_idle(self) -> bool:
        """
        检查所有任务是否空闲：
        1. 无活跃直播（live_status 里所有组 is_live_end=True）
        2. 所有任务的 state_dict 和 ended_dict 均为空（渲染/上传已完成）
        回放/下播中的监控任务不影响判断。
        """
        for task_info in self.task_dict.values():
            event_class = task_info['class'].event_class

            # 有正在直播的组（is_live_end=False）→ 不空闲
            for group_status in event_class.live_status.values():
                if not group_status.get('is_live_end', True):
                    return False

            # 有未处理完的渲染/上传 → 不空闲
            if event_class.state_dict:
                return False
            if event_class.ended_dict:
                return False

        return True

    def restart_when_idle(self, check_interval: int = 30):
        """
        等待所有任务空闲后原地重启程序（os.execv，PID 不变）。
        重复调用无效，只有第一次生效。
        check_interval: 轮询间隔（秒），默认 30 秒
        """
        if getattr(self, '_restart_pending', False):
            self.logger.info('已有空闲重启任务在等待中，忽略重复请求。')
            return
        self._restart_pending = True

        def _wait_and_restart():
            self.logger.info('已设置空闲重启，等待所有任务完成...')
            while not self.stoped:
                if self.is_idle():
                    self.logger.info('所有任务已完成，正在重启程序...')
                    # 标记重启子进程，让 WebService 不再自动开新浏览器标签（原标签会自动重连）
                    subprocess.Popen([sys.executable] + sys.argv,
                                     env=dict(os.environ, DMR_NO_BROWSER='1'))
                    os._exit(0)
                time.sleep(check_interval)
            self._restart_pending = False  # engine 被 stop 时取消重启

        threading.Thread(target=_wait_and_restart, daemon=True, name='restart-watcher').start()

    def stop(self):
        self.stoped = True
        for taskname in list(self.task_dict.keys()):
            self.del_task(taskname)
        for name in self.plugin_dict.keys():
            try:
                self.plugin_dict[name]['class'].stop()
            except Exception as e:
                self.logger.exception(e)
        self.task_dict.clear()
        self.plugin_dict.clear()
        self.recv_queue.put(PipeMessage('engine', 'engine', 'exit'))
        self.logger.info('DMR engine stoped.')
