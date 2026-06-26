import os
import glob
import yaml
import logging
import hashlib
from copy import deepcopy
from typing import List
from DMR.utils import filename_to_taskname, merge_dict, ToolsList

# __all__ = ['Config', 'new_config']

class Config():
    _base_config_path = 'DMR/Config/default.yml'

    def __init__(self, global_config_path:str) -> None:
        self.global_config_path = global_config_path
        self.replay_config_path_raw = []
        self.replay_config_paths:List[str] = []
        self.file_hashes = {}
        # 已应用版本的原文快照：path -> 文本。与 file_hashes 同步更新，供「查看改动」做 diff。
        self.file_contents = {}
        # 用户选择“推迟到重启”的文件：path -> 当时磁盘hash（删除则为 'DELETED'）
        self.deferred = {}
        self.logger = logging.getLogger(__name__)
        
        self._init_config()
        self.check_update()

    def _get_file_hash(self, path):
        try:
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return None

    def _read_text(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return ''

    def _init_config(self):
        with open(self._base_config_path, 'r', encoding='utf-8') as f:
            self._base_config = yaml.safe_load(f)

        self.global_config = deepcopy(self._base_config)
        self.replay_config = {}

        with open(self.global_config_path, 'r', encoding='utf-8') as f:
            _global_config = yaml.safe_load(f)
        self.file_hashes[self.global_config_path] = self._get_file_hash(self.global_config_path)
        self.file_contents[self.global_config_path] = self._read_text(self.global_config_path)

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

    def add_task_config(self, config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                _replay_config = yaml.safe_load(f)
            
            current_hash = self._get_file_hash(config_path)
            self.file_hashes[config_path] = current_hash
            self.file_contents[config_path] = self._read_text(config_path)

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

    def _current_task_files(self):
        """当前磁盘上的任务配置文件集合（与 check_update 使用同一套 glob 规则）。"""
        current_files = set()
        for raw_path in self.replay_config_path_raw:
            if os.path.isfile(raw_path):
                current_files.add(raw_path)
            elif os.path.isdir(raw_path):
                for f in glob.glob(os.path.join(raw_path, 'DMR-**.yml')):
                    current_files.add(f)
        return current_files

    def find_task_file(self, filename):
        """按文件名（basename）在磁盘上找到对应的任务配置路径，找不到返回 None。"""
        fname = os.path.basename(filename)
        for f in self._current_task_files():
            if os.path.basename(f) == fname:
                return f
        return None

    def get_all_states(self):
        """给网页用的完整状态：磁盘上所有任务文件 + 已加载但被删的文件。
        status: None(已同步) / new(新增) / modified(已修改) / deleted(已删除) / deferred(已推迟到重启)。"""
        result = []
        current_files = self._current_task_files()
        loaded_files = set(self.replay_config_paths)
        for f in sorted(current_files):
            h = self._get_file_hash(f)
            if f not in loaded_files:
                status = 'new'
            elif h != self.file_hashes.get(f):
                status = 'modified'
            else:
                status = None
            # 已“推迟到重启”的文件：之后再怎么改也保持 deferred、不再红点（反正重启统一应用最新版）
            if status in ('new', 'modified') and f in self.deferred:
                status = 'deferred'
            result.append({'path': f, 'filename': os.path.basename(f),
                           'taskname': filename_to_taskname(f),
                           'status': status, 'exists': True})
        for f in sorted(loaded_files - current_files):
            status = 'deferred' if f in self.deferred else 'deleted'
            result.append({'path': f, 'filename': os.path.basename(f),
                           'taskname': filename_to_taskname(f),
                           'status': status, 'exists': False})
        return result

    def get_pending_changes(self):
        """需要用户处理（未推迟）的改动列表，用于红点计数。"""
        return [s for s in self.get_all_states()
                if s['status'] in ('new', 'modified', 'deleted')]

    def get_diff(self, filename):
        """某配置文件「已应用版本 → 当前磁盘版本」的统一 diff（git 风格 +/- 行列表，供 WebUI 查看改动）。
        新增=全部 +；删除=全部 -；修改=逐行增删。"""
        import difflib
        fname = os.path.basename(filename)
        # 当前磁盘路径：任务文件，或全局配置
        disk_path = self.find_task_file(fname)
        if disk_path is None and fname == os.path.basename(self.global_config_path):
            disk_path = self.global_config_path
        # 已应用版本的原文（按 basename 在快照里找）
        loaded_path = next((p for p in self.file_contents if os.path.basename(p) == fname), None)
        old_text = self.file_contents.get(loaded_path, '') if loaded_path else ''
        new_text = self._read_text(disk_path) if (disk_path and os.path.exists(disk_path)) else ''
        old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
        # n 设成全文长度 → 整份文件都展示（不只改动附近的几行），未改的行作为上下文一并带上
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f'{fname}（已应用）', tofile=f'{fname}（当前）',
            lineterm='', n=max(len(old_lines), len(new_lines), 1))
        return list(diff)

    def revert(self, filename):
        """撤销某文件的未应用改动，回到「上次已应用」的状态：
        修改/删除 → 把已应用版本的原文写回磁盘；新增（无已应用快照）→ 删除该文件。
        同时清掉它的「推迟」标记。返回是否成功。"""
        fname = os.path.basename(filename)
        self.deferred.pop(next((p for p in self.deferred if os.path.basename(p) == fname), None), None)
        loaded_path = next((p for p in self.file_contents if os.path.basename(p) == fname), None)
        disk_path = self.find_task_file(fname)
        try:
            if loaded_path is not None and self.file_contents.get(loaded_path) is not None:
                target = disk_path or loaded_path   # 删除场景 disk_path 为空，用原路径恢复
                with open(target, 'w', encoding='utf-8') as f:
                    f.write(self.file_contents[loaded_path])
                return True
            # 没有已应用快照 = 新增文件的撤销 → 删除磁盘文件
            if disk_path and os.path.exists(disk_path):
                os.remove(disk_path)
                return True
        except Exception as e:
            self.logger.warning(f'撤销配置改动失败 {fname}: {e}')
        return False

    def defer(self, filename):
        """把某文件标记为“推迟到重启”。一旦推迟就黏住：该文件之后再被修改也保持 deferred、不再红点，
        直到重启时统一应用最新版（__init__ 应用后会 pop 掉）或用户撤销推迟。只记在不在名单，不记哈希。"""
        fname = os.path.basename(filename)
        disk_path = self.find_task_file(fname)
        if disk_path is not None:
            self.deferred[disk_path] = True
            return True
        # 磁盘已删除：在已加载列表里找到原路径，同样标记为推迟
        loaded_path = next((p for p in self.replay_config_paths
                            if os.path.basename(p) == fname), None)
        if loaded_path is not None:
            self.deferred[loaded_path] = True
            return True
        return False

    def undefer(self, filename):
        """撤销“推迟到重启”，让该文件重新进入待应用提醒。"""
        fname = os.path.basename(filename)
        for p in [p for p in self.deferred if os.path.basename(p) == fname]:
            self.deferred.pop(p, None)
        return True

    def check_update(self):
        updated_tasks = []
        new_tasks = []
        deleted_tasks = []
        
        # Check global config
        try:
            current_hash = self._get_file_hash(self.global_config_path)
            if current_hash != self.file_hashes.get(self.global_config_path):
                self.file_hashes[self.global_config_path] = current_hash
                self.file_contents[self.global_config_path] = self._read_text(self.global_config_path)
                return 'global', None
        except Exception:
            pass

        # get replay configs
        current_files = set()
        for raw_path in self.replay_config_path_raw:
            if os.path.isfile(raw_path):
                current_files.add(raw_path)
            elif os.path.isdir(raw_path):
                config_files = glob.glob(os.path.join(raw_path, 'DMR-**.yml'))
                for f in config_files:
                    current_files.add(f)

        old_files = set(self.replay_config_paths)
        
        for f in current_files - old_files:
            if self.add_task_config(f):
                new_tasks.append(f)
        
        for f in old_files - current_files:
            deleted_tasks.append(f)
            t = filename_to_taskname(f)
            self.replay_config.pop(t, None)
            self.file_hashes.pop(f, None)
            self.file_contents.pop(f, None)
            
        for f in current_files & old_files:
            try:
                current_hash = self._get_file_hash(f)
                if current_hash != self.file_hashes.get(f) and self.add_task_config(f):
                    updated_tasks.append(f)
            except Exception:
                pass
        
        if new_tasks or deleted_tasks or updated_tasks:
            self.replay_config_paths = list(current_files)
            return 'tasks', {
                'new': list(new_tasks),
                'deleted': list(deleted_tasks),
                'updated': list(updated_tasks)
            }
        
        return None, None

    def get_config(self, name):
        return self.global_config.get(name)
    
    def get_replay_config(self, taskname):
        return self.replay_config.get(taskname)
    
    def get_replaytasks(self):
        return list(self.replay_config.keys())
