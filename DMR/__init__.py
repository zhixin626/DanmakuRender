import logging
import os
import shutil
import threading
import json
import time
import logging

from .engine import DMREngine
from .Config import Config
from .utils import filename_to_taskname

class DanmakuRender(): # 被 main.py直接 DanmakuRender(config, logger=logger, debug=args.debug) 并 start()
    def __init__(self, config:Config, **kwargs) -> None:
        self.logger = logging.getLogger('DMR')
        self.config = config
        self.kwargs = kwargs
        self.stoped = True
        self.engine_args = self.config.get_config('dmr_engine_args')
        self.engine = DMREngine()
        # 让 WebService 能反向拿到本实例（用于网页启用/停用任务）
        self.engine.dmr = self

    def start(self):
        # engine初始化，给engine添加插件，给engine添加回放任务
        self.stoped = False
        os.makedirs('.temp', exist_ok=True)
        
        self.logger.debug(f'Global Config:\n{json.dumps(self.config.global_config, indent=4, ensure_ascii=False)}')
        # 重点关注 self.config.replay_config（是个字典）（里面有common_event_args）
        self.logger.debug(f'Replay Config:\n{json.dumps(self.config.replay_config, indent=4, ensure_ascii=False)}')

        self.engine.start()
        plugin_enabled = self.config.get_config('dmr_engine_args')['enabled_plugins']

        ws_port = self.config.get_config('webservice_kernel_args').get('port', '')
        for plugin_name in plugin_enabled:
            plugin_config = self.config.get_config(plugin_name+'_kernel_args')
            # 把端口传给 Cleaner，用于区分多实例的 pending_cleanup 文件
            if plugin_name == 'cleaner':
                plugin_config = {**plugin_config, 'port': ws_port}
            self.engine.add_plugin(plugin_name, plugin_config)

        # 恢复上次程序退出时未完成的清理任务（可通过 cleaner_kernel_args.resume_pending: false 关闭）
        if 'cleaner' in self.engine.plugin_dict:
            cleaner = self.engine.plugin_dict['cleaner']['class']
            if cleaner.resume_pending:
                cleaner.execute_pending()

        for taskname in self.config.get_replaytasks():
            replay_config = self.config.get_replay_config(taskname)
            self.engine.add_task(taskname, replay_config)  # 调用engine的add_task

        threading.Thread(target=self._scheduler, daemon=True, name='dmr-scheduler').start()

    def toggle_task(self, filename, enabled: bool):
        """启用/停用某任务（网页开关触发）：改写配置文件的 enable 字段，并即时增删任务。"""
        try:
            path = self.config.find_task_file(filename)
            if not path:
                return {'ok': False, 'message': f'{os.path.basename(filename)} 不存在'}
            self.config.set_task_enabled(path, enabled)
            taskname = filename_to_taskname(path)
            if enabled:
                if self.config.add_task_config(path):
                    self.engine.add_task(taskname, self.config.get_replay_config(taskname))
                    self.logger.info(f'已启用任务: {taskname}')
                    return {'ok': True, 'taskname': taskname, 'enabled': True,
                            'message': f'已启用并启动任务「{taskname}」'}
                return {'ok': False, 'message': '配置解析失败，请检查日志'}
            else:
                self.engine.del_task(taskname)
                self.config.replay_config.pop(taskname, None)
                self.logger.info(f'已停用任务: {taskname}')
                return {'ok': True, 'taskname': taskname, 'enabled': False,
                        'message': f'已停用并停止任务「{taskname}」'}
        except Exception as e:
            self.logger.error('启用/停用任务失败:')
            self.logger.exception(e)
            return {'ok': False, 'message': f'操作失败: {e}'}

    def _scheduler(self):
        """统一的定时任务线程：取代原先各开一条 while+sleep 的 _monintor / _pending_restart_watcher。
        每 TICK 秒醒一次，到点就跑对应的活；以后要加周期任务，只往 jobs 里加一行即可。
        各 job 内部已自捕异常，单个出错不影响其它 job 与后续轮次。"""
        TICK = 10
        # [间隔秒, 函数, 上次运行时刻]；初始 last=now → 首轮在 now+间隔 触发（与旧行为一致：
        # 配置检查/清临时首跑约 +60s，延迟重启首跑约 +30s）
        now = time.time()
        jobs = [
            [60, self._job_clean_temp, now],
        ]
        while not self.stoped:
            time.sleep(TICK)
            now = time.time()
            for job in jobs:
                interval, fn, last = job
                if now - last >= interval:
                    job[2] = now
                    try:
                        fn()
                    except Exception as e:
                        self.logger.error(f'定时任务 {fn.__name__} 执行出错:')
                        self.logger.exception(e)

    def _job_clean_temp(self):
        """定时任务：清理 .temp 下已过期的临时文件/文件夹（文件名末尾的时间戳为过期时刻）。"""
        for file in os.listdir('.temp'):
            try:
                basename = os.path.splitext(os.path.basename(file))[0]
                expired_time = basename.split('_')[-1]
                if expired_time.isdigit():
                    expired_time = int(expired_time)
                else:
                    expired_time = 0
                # 只清理2024.01.01之后的过期文件，过早的文件认为不是程序创建的不清理
                if expired_time > 1704038400 and expired_time < int(time.time()):
                    file = os.path.join('.temp', file)
                    if os.path.isfile(file):
                        os.remove(file)
                        self.logger.debug(f'已清理临时文件: {file}')
                    elif os.path.isdir(file):
                        shutil.rmtree(file)
                        self.logger.debug(f'已清理临时文件夹: {file}')
            except Exception as e:
                self.logger.debug(f'清理临时文件{file}失败: {e}')

    def stop(self):
        self.stoped = True
        self.engine.stop()
