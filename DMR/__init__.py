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
        # 让 WebService 能反向拿到本实例（用于网页手动应用配置）
        self.engine.dmr = self
        self._pending_restarts = {}

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

        self._pending_restarts = {}
        threading.Thread(target=self._scheduler, daemon=True, name='dmr-scheduler').start()

    def check_config_update(self):
        try:
            update_type, update_info = self.config.check_update()

            if update_type == 'global':
                self.logger.info('检测到全局配置更新，请重启程序以生效。')

            elif update_type == 'tasks':
                for config_path in update_info['new']:
                    taskname = filename_to_taskname(config_path)
                    self.logger.info(f'检测到新任务配置文件: {taskname}，正在添加任务...')
                    self.engine.add_task(taskname, self.config.get_replay_config(taskname))

                for config_path in update_info['deleted']:
                    taskname = filename_to_taskname(config_path)
                    self.logger.info(f'检测到任务配置文件删除: {taskname}，正在停止任务...')
                    self.engine.del_task(taskname)

                for config_path in update_info['updated']:
                    taskname = filename_to_taskname(config_path)
                    if self.engine.is_task_idle(taskname):
                        self.logger.info(f'检测到任务配置文件更新: {taskname}，任务空闲，正在重启任务...')
                        self.engine.del_task(taskname)
                        time.sleep(5)
                        self.engine.add_task(taskname, self.config.get_replay_config(taskname))
                    else:
                        if taskname not in self._pending_restarts:
                            self.logger.info(f'检测到任务配置文件更新: {taskname}，任务正忙，将在空闲后重启。')
                        else:
                            self.logger.info(f'任务配置文件再次更新: {taskname}，已更新待重启配置。')
                        self._pending_restarts[taskname] = config_path

        except Exception as e:
            self.logger.error(f'动态载入配置文件错误:')
            self.logger.exception(e)

    def _job_pending_restarts(self):
        """定时任务：等忙碌任务空闲后，执行其排队中的延迟重启（由 _scheduler 每 30s 调一次）。"""
        if not self._pending_restarts:
            return
        done = []
        for taskname, config_path in list(self._pending_restarts.items()):
            if self.engine.is_task_idle(taskname):
                try:
                    self.logger.info(f'任务 {taskname} 已空闲，正在应用配置更新并重启...')
                    self.engine.del_task(taskname)
                    time.sleep(5)
                    self.engine.add_task(taskname, self.config.get_replay_config(taskname))
                    done.append(taskname)
                except Exception as e:
                    self.logger.error(f'延迟重启任务 {taskname} 失败:')
                    self.logger.exception(e)
                    done.append(taskname)
        for taskname in done:
            self._pending_restarts.pop(taskname, None)

    def _swap_task(self, taskname):
        """删旧任务、等待后用最新配置重建（在后台线程执行，避免阻塞网页请求）。"""
        try:
            self.engine.del_task(taskname)
            time.sleep(5)
            self.engine.add_task(taskname, self.config.get_replay_config(taskname))
        except Exception as e:
            self.logger.error(f'重启任务 {taskname} 失败:')
            self.logger.exception(e)

    def apply_config_change(self, filename):
        """手动应用单个任务配置文件的改动（由网页按钮触发，不依赖 dynamic_config）。
        空闲任务立即重启；忙碌任务排队等空闲；磁盘新增/删除立即处理。"""
        try:
            fname = os.path.basename(filename)
            disk_path = self.config.find_task_file(fname)
            loaded_path = next((p for p in self.config.replay_config_paths
                                if os.path.basename(p) == fname), None)
            # 一旦真正应用，清掉该文件的“推迟到重启”标记
            for p in (disk_path, loaded_path):
                if p:
                    self.config.deferred.pop(p, None)

            # 磁盘已删除 + 之前已加载 → 停止并移除任务
            if disk_path is None:
                if loaded_path is None:
                    return {'ok': False, 'message': f'{fname} 不存在'}
                taskname = filename_to_taskname(loaded_path)
                self.engine.del_task(taskname)
                self.config.replay_config.pop(taskname, None)
                self.config.file_hashes.pop(loaded_path, None)
                if loaded_path in self.config.replay_config_paths:
                    self.config.replay_config_paths.remove(loaded_path)
                self.logger.info(f'已应用配置删除: {taskname}')
                return {'ok': True, 'taskname': taskname, 'action': 'deleted',
                        'message': f'已停止并移除任务「{taskname}」'}

            # 重新解析（add_task_config 内部会更新 file_hashes，从而清除“待应用”标记）
            taskname = self.config.add_task_config(disk_path)
            if not taskname:
                return {'ok': False, 'message': '配置解析失败，请检查日志'}
            if disk_path not in self.config.replay_config_paths:
                self.config.replay_config_paths.append(disk_path)

            # 新任务
            if loaded_path is None:
                self.engine.add_task(taskname, self.config.get_replay_config(taskname))
                self.logger.info(f'已应用新配置: {taskname}')
                return {'ok': True, 'taskname': taskname, 'action': 'added',
                        'message': f'已添加并启动任务「{taskname}」'}

            # 已存在任务的修改：空闲立即重启，忙碌排队
            if self.engine.is_task_idle(taskname):
                threading.Thread(target=self._swap_task, args=(taskname,), daemon=True).start()
                self.logger.info(f'任务 {taskname} 空闲，正在应用配置并重启...')
                return {'ok': True, 'taskname': taskname, 'action': 'restarting',
                        'message': f'任务空闲，已应用并重启「{taskname}」'}
            self._pending_restarts[taskname] = disk_path
            self.logger.info(f'任务 {taskname} 忙碌，配置将在空闲后应用。')
            return {'ok': True, 'taskname': taskname, 'action': 'pending',
                    'message': f'任务忙碌，将在空闲后自动应用「{taskname}」'}
        except Exception as e:
            self.logger.error('手动应用配置失败:')
            self.logger.exception(e)
            return {'ok': False, 'message': f'应用失败: {e}'}

    def _scheduler(self):
        """统一的定时任务线程：取代原先各开一条 while+sleep 的 _monintor / _pending_restart_watcher。
        每 TICK 秒醒一次，到点就跑对应的活；以后要加周期任务，只往 jobs 里加一行即可。
        各 job 内部已自捕异常，单个出错不影响其它 job 与后续轮次。"""
        TICK = 10
        # [间隔秒, 函数, 上次运行时刻]；初始 last=now → 首轮在 now+间隔 触发（与旧行为一致：
        # 配置检查/清临时首跑约 +60s，延迟重启首跑约 +30s）
        now = time.time()
        jobs = [
            [60, self._job_dynamic_config, now],
            [60, self._job_clean_temp,     now],
            [30, self._job_pending_restarts, now],
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

    def _job_dynamic_config(self):
        """定时任务：开启 dynamic_config 时，检查配置文件增删改并热应用。"""
        if self.config.get_config('dmr_engine_args')['dynamic_config']:
            self.check_config_update()

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
