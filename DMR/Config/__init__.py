import os
import re
import glob
import yaml
import logging
from copy import deepcopy
from typing import List
from DMR.utils import filename_to_taskname, merge_dict, ToolsList

# __all__ = ['Config', 'new_config']

class Config():
    _base_config_path = 'DMR/Config/default.yml'

    def __init__(self, global_config_path:str) -> None:
        self.global_config_path = global_config_path
        self.replay_config_path_raw = []
        self.logger = logging.getLogger(__name__)

        self._init_config()
        self.load_all_tasks()

    def _init_config(self):
        with open(self._base_config_path, 'r', encoding='utf-8') as f:
            self._base_config = yaml.safe_load(f)

        self.global_config = deepcopy(self._base_config)
        self.replay_config = {}

        with open(self.global_config_path, 'r', encoding='utf-8') as f:
            _global_config = yaml.safe_load(f)

        self.global_config = merge_dict(self.global_config, _global_config)

        for toolname, path in self.global_config.get('executable_tools_path',{}).items():
            if not path:
                ToolsList.get(toolname, auto_install=True)
            else:
                ToolsList.set(toolname, path)

        dmr_engine_args = self.global_config.get('dmr_engine_args', {})
        self.replay_config_path_raw = dmr_engine_args.get('config_path', ['./configs'])

        if isinstance(self.replay_config_path_raw, str):
            self.replay_config_path_raw = [self.replay_config_path_raw]

        # zhixin 新增
        self.global_config['webservice_kernel_args']['config_dir'] = self.replay_config_path_raw[0]

    def task_config_files(self):
        """配置目录下所有任务配置文件（*.yml，排除全局配置）。
        是否启用由文件内顶层 enable 字段决定（缺省视为启用），不再靠文件名前缀。"""
        files = []
        global_name = os.path.basename(self.global_config_path)
        for raw_path in self.replay_config_path_raw:
            if os.path.isfile(raw_path):
                files.append(raw_path)
            elif os.path.isdir(raw_path):
                for f in sorted(glob.glob(os.path.join(raw_path, '*.yml'))):
                    if os.path.basename(f) != global_name:
                        files.append(f)
        return files

    def find_task_file(self, filename):
        """按文件名（basename）在配置目录里找到任务配置路径，找不到返回 None。"""
        fname = os.path.basename(filename)
        for f in self.task_config_files():
            if os.path.basename(f) == fname:
                return f
        return None

    def load_all_tasks(self):
        """启动时加载所有「启用」的任务配置（enable 不为 false）。"""
        for f in self.task_config_files():
            self.add_task_config(f)

    def add_task_config(self, config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                _replay_config = yaml.safe_load(f)

            # 顶层 enable: false 的任务直接跳过（不加载、不启动）
            if not _replay_config or _replay_config.get('enable', True) is False:
                return None

            taskname = filename_to_taskname(config_path)
            replay_config = {}

            common_args = _replay_config.get('common_event_args')
            if not common_args: return None
            replay_config['common_event_args'] = deepcopy(common_args)

            global_download_args = self.global_config['download_args']
            dltype = _replay_config.get('download_args', {}).get('dltype', 'live')
            replay_config['download_args'] = deepcopy(global_download_args.get(dltype, {}))
            if _replay_config.get('download_args'):
                replay_config['download_args'] = merge_dict(replay_config['download_args'], _replay_config['download_args'])

            if common_args.get('auto_render') or common_args.get('auto_transcode'):
                replay_config['render_args'] = deepcopy(self.global_config['render_args'])
                if _replay_config.get('render_args'):
                    replay_config['render_args'] = merge_dict(replay_config['render_args'], _replay_config['render_args'])

            if common_args.get('auto_upload'):
                global_upload_args = self.global_config['upload_args']
                replay_config['upload_args'] = {}
                if _replay_config.get('upload_args'):
                    for upload_file_types, upload_args in _replay_config['upload_args'].items():
                        if isinstance(upload_args, dict):
                            upload_args = [upload_args]
                        replay_config['upload_args'][upload_file_types] = []
                        for upload_arg in upload_args:
                            target = upload_arg.get('target', 'bilibili')
                            if not global_upload_args.get(target):
                                raise ValueError(f'不存在可用的上传目标 {target}.')
                            upload_config = deepcopy(global_upload_args[target])
                            upload_config.update(upload_arg)
                            replay_config['upload_args'][upload_file_types].append(upload_config)

            if common_args.get('auto_clean'):
                global_clean_args = self.global_config['clean_args']
                replay_config['clean_args'] = {}
                if _replay_config.get('clean_args'):
                    for clean_file_types, clean_args in _replay_config['clean_args'].items():
                        if isinstance(clean_args, dict):
                            clean_args = [clean_args]
                        replay_config['clean_args'][clean_file_types] = []
                        for clean_arg in clean_args:
                            method = clean_arg.get('method')
                            if not method:
                                continue
                            if not global_clean_args.get(method):
                                raise ValueError(f'不存在可用的清理方法 {method}.')
                            clean_config = deepcopy(global_clean_args[method])
                            clean_config.update(clean_arg)
                            replay_config['clean_args'][clean_file_types].append(clean_config)

            # merge_args / bark_args：顶层独立配置块（全局默认 + 任务覆盖）
            for key in ('merge_args', 'bark_args'):
                global_default = self.global_config.get(key) or {}
                task_value = _replay_config.get(key)
                if global_default or task_value:
                    merged = deepcopy(global_default)
                    if task_value:
                        merged = merge_dict(merged, task_value)
                    replay_config[key] = merged

            self.replay_config[taskname] = deepcopy(replay_config)
            return taskname
        except Exception as e:
            self.logger.error(f"Error loading config {config_path}:")
            self.logger.exception(e)
            return None

    def set_task_enabled(self, config_path, enabled: bool) -> bool:
        """改写某任务配置文件顶层的 enable 字段（文本级修改，保留其余内容/注释）。"""
        val = 'true' if enabled else 'false'
        try:
            with open(config_path, encoding='utf-8') as f:
                lines = f.readlines()
            for i, ln in enumerate(lines):
                if re.match(r'^enable\s*:', ln):
                    lines[i] = f'enable: {val}\n'
                    break
            else:
                lines.insert(0, f'enable: {val}\n')
            with open(config_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return True
        except Exception as e:
            self.logger.warning(f'设置 enable 失败 {config_path}: {e}')
            return False

    def is_task_enabled(self, config_path) -> bool:
        try:
            with open(config_path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            return data.get('enable', True) is not False
        except Exception:
            return True

    def get_config(self, name):
        return self.global_config.get(name)

    def get_replay_config(self, taskname):
        return self.replay_config.get(taskname)

    def get_replaytasks(self):
        return list(self.replay_config.keys())
