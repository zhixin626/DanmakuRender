import os
import re
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
        self.config_dir = ''                       # 存任务配置的文件夹
        self.loaded_task_files:List[str] = []      # 已加载(启用且解析成功)的任务配置文件路径
        self.file_hashes = {}                      # path -> 已应用版本的 md5，用于检测磁盘改动
        # 已应用版本的原文快照：path -> 文本。与 file_hashes 同步，供「查看改动」做 diff、「撤销」写回。
        self.file_contents = {}
        # 用户选择“推迟到重启”的文件：path -> True
        self.deferred = {}
        self.logger = logging.getLogger(__name__)

        self._init_config()
        self.load_all_tasks()

    def _init_config(self):
        # default.yml：程序自带的默认配置（基底）
        with open(self._base_config_path, 'r', encoding='utf-8') as f:
            default_config = yaml.safe_load(f)
        # global.yml：用户的全局配置文件，覆盖在默认之上
        with open(self.global_config_path, 'r', encoding='utf-8') as f:
            user_global = yaml.safe_load(f)

        # 最终生效的全局配置 = 默认 + 用户覆盖。
        # merge_dict 每层都生成新字典、不会改动传入的原字典，所以无需先 deepcopy。
        self.global_config = merge_dict(default_config, user_global)
        self.task_configs = {}

        # 记录 global.yml 的快照（变更检测 / 查看改动 / 撤销用）
        self.file_hashes[self.global_config_path] = self._get_file_hash(self.global_config_path)
        self.file_contents[self.global_config_path] = self._read_text(self.global_config_path)

        for toolname, path in self.global_config.get('executable_tools_path',{}).items():
            if not path:
                ToolsList.get(toolname, auto_install=True)
            else:
                ToolsList.set(toolname, path)

        dmr_engine_args = self.global_config.get('dmr_engine_args', {})
        self.config_dir = dmr_engine_args.get('config_path', './configs')

        self.global_config['webservice_kernel_args']['config_dir'] = self.config_dir

    def load_all_tasks(self):
        """启动时加载所有「启用」的任务配置（enable 不为 false）。"""
        for f in self.task_config_files():
            if self.add_task_config(f) and f not in self.loaded_task_files:
                self.loaded_task_files.append(f)

    def _get_file_hash(self, path):
        try:
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return None

    def _read_text(self, path):
        # newline='' 关闭文本模式的换行符自动转换(否则读进来 CRLF 被统一成 LF)。
        # 必须和 _get_file_hash(读原始字节)保持一致：撤销时把这份快照原样写回磁盘，
        # 才能还原出和当初 hash 一致的字节；否则 LF 文件在 Windows 上会被写成 CRLF，
        # 撤销后 hash 对不上，状态一直卡在「待应用」。
        try:
            with open(path, 'r', encoding='utf-8', newline='') as f:
                return f.read()
        except Exception:
            return ''

    def task_config_files(self):
        """配置目录下所有任务配置文件（*.yml，排除全局配置）。
        是否启用由文件内顶层 enable 字段决定（缺省视为启用），不再靠文件名前缀。"""
        if os.path.isfile(self.config_dir):       # 也允许直接指向单个文件
            return [self.config_dir]
        if not os.path.isdir(self.config_dir):
            return []
        global_name = os.path.basename(self.global_config_path)
        return [f for f in sorted(glob.glob(os.path.join(self.config_dir, '*.yml')))
                if os.path.basename(f) != global_name]

    def find_task_file(self, filename):
        """按文件名（basename）在配置目录里找到任务配置路径，找不到返回 None。"""
        fname = os.path.basename(filename)
        for f in self.task_config_files():
            if os.path.basename(f) == fname:
                return f
        return None

    def add_task_config(self, config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                _replay_config = yaml.safe_load(f)

            # 顶层 enable: false 的任务直接跳过（不加载、不记录快照）
            if not _replay_config or _replay_config.get('enable', True) is False:
                return None

            # 记录“已应用版本”的快照（用于变更检测/diff/撤销）
            self.file_hashes[config_path] = self._get_file_hash(config_path)
            self.file_contents[config_path] = self._read_text(config_path)

            taskname = filename_to_taskname(config_path)
            replay_config = {}

            # common_event_args 与其它配置块保持相同的三层继承关系：
            # default.yml -> global.yml -> 单任务配置。任务文件只需要写与全局不同的开关。
            common_args = merge_dict(
                self.global_config.get('common_event_args') or {},
                _replay_config.get('common_event_args') or {},
            )
            if not common_args:
                return None
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
                            # 上传配置也递归合并，任务可以只覆盖 cover_args、
                            # extra_kwargs 等嵌套配置中的个别字段。
                            upload_config = merge_dict(
                                deepcopy(global_upload_args[target]),
                                upload_arg,
                            )
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
                            clean_config = merge_dict(
                                deepcopy(global_clean_args[method]),
                                clean_arg,
                            )
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

            self.task_configs[taskname] = deepcopy(replay_config)
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

    # ---- 变更检测 / 查看改动 / 撤销 / 推迟（用户手动应用，不再自动热重载）----
    def _state_entry(self, path, *, taskname, status, enabled, exists, is_global=False):
        """统一构造一条配置状态记录（path 用绝对路径，供网页显示/打开）。"""
        return {'path': os.path.abspath(path), 'filename': os.path.basename(path),
                'taskname': taskname, 'status': status,
                'enabled': enabled, 'exists': exists, 'is_global': is_global}

    def get_all_states(self):
        """网页用的完整权威清单（webapi 直接照这个画，不再自己扫目录）：
        global 全局配置 + 磁盘上所有任务文件 + 已加载但磁盘已删的任务文件。
        status: None(已同步/已停用) / new(新增) / modified(已修改) / deleted(已删除) / deferred(已推迟到重启)。
        停用的文件不报变更（由启用/停用开关管理）；global 恒为启用、只会是 modified 或 None。"""
        result = []

        # ① global 全局配置：必须启用、不能停用、不会 new/deleted，改了只能整程序重启生效；可「忽略」消红点
        g = self.global_config_path
        g_status = 'modified' if self._get_file_hash(g) != self.file_hashes.get(g) else None
        if g_status == 'modified' and g in self.deferred:
            g_status = 'deferred'
        result.append(self._state_entry(g, taskname='全局配置', status=g_status,
                                        enabled=True, exists=True, is_global=True))

        # ② 磁盘上的任务文件
        # enabled 表示「此刻真加载进引擎在跑」(在 loaded_files 里)，而非文件 enable 字段——
        # 否则刚拖进来还没 apply 的新文件(enable 缺省 true)会立刻显示「已启用」，与「待应用(新增)」自相矛盾。
        # 文件 enable 字段(file_enabled)只用来抑制停用文件的变更提醒。
        current_files = set(self.task_config_files())
        loaded_files = set(self.loaded_task_files)
        for f in sorted(current_files):
            file_enabled = self.is_task_enabled(f)
            is_loaded = f in loaded_files
            if not file_enabled:
                status = None
            elif not is_loaded:
                status = 'new'
            elif self._get_file_hash(f) != self.file_hashes.get(f):
                status = 'modified'
            else:
                status = None
            if status in ('new', 'modified') and f in self.deferred:
                status = 'deferred'
            result.append(self._state_entry(f, taskname=filename_to_taskname(f),
                                            status=status, enabled=is_loaded, exists=True))

        # ③ 已加载但磁盘已删的任务文件
        for f in sorted(loaded_files - current_files):
            status = 'deferred' if f in self.deferred else 'deleted'
            result.append(self._state_entry(f, taskname=filename_to_taskname(f),
                                            status=status, enabled=False, exists=False))
        return result

    def get_pending_changes(self):
        """需要用户处理（未推迟）的改动列表，用于红点计数。"""
        return [s for s in self.get_all_states()
                if s['status'] in ('new', 'modified', 'deleted')]

    def get_diff(self, filename):
        """某配置文件「已应用版本 → 当前磁盘版本」的统一 diff（git 风格 +/- 行列表，供 WebUI 查看改动）。"""
        import difflib
        fname = os.path.basename(filename)
        disk_path = self.find_task_file(fname)
        if disk_path is None and fname == os.path.basename(self.global_config_path):
            disk_path = self.global_config_path
        loaded_path = next((p for p in self.file_contents if os.path.basename(p) == fname), None)
        old_text = self.file_contents.get(loaded_path, '') if loaded_path else ''
        new_text = self._read_text(disk_path) if (disk_path and os.path.exists(disk_path)) else ''
        old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f'{fname}（已应用）', tofile=f'{fname}（当前）',
            lineterm='', n=6)   # 每处改动上下各保留 6 行上下文，只展示改动段落而非整个文件
        return list(diff)

    def revert(self, filename):
        """撤销某文件的未应用改动，回到「上次已应用」的状态：
        修改/删除 → 把已应用版本的原文写回磁盘；新增（无已应用快照）→ 删除该文件。同时清掉「推迟」标记。"""
        fname = os.path.basename(filename)
        self.deferred.pop(next((p for p in self.deferred if os.path.basename(p) == fname), None), None)
        loaded_path = next((p for p in self.file_contents if os.path.basename(p) == fname), None)
        disk_path = self.find_task_file(fname)
        try:
            if loaded_path is not None and self.file_contents.get(loaded_path) is not None:
                target = disk_path or loaded_path
                # newline='' 原样写回快照(不转换换行)，配合 _read_text 保证还原出的字节与原 hash 一致
                with open(target, 'w', encoding='utf-8', newline='') as f:
                    f.write(self.file_contents[loaded_path])
                return True
            if disk_path and os.path.exists(disk_path):
                os.remove(disk_path)
                return True
        except Exception as e:
            self.logger.warning(f'撤销配置改动失败 {fname}: {e}')
        return False

    def defer(self, filename):
        """把某文件标记为“忽略”：消红点、不再提醒，改动在下次重启时自然生效，直到撤销忽略。
        global 也可忽略（它本就只能重启生效）。"""
        fname = os.path.basename(filename)
        if fname == os.path.basename(self.global_config_path):
            self.deferred[self.global_config_path] = True
            return True
        disk_path = self.find_task_file(fname)
        if disk_path is not None:
            self.deferred[disk_path] = True
            return True
        loaded_path = next((p for p in self.loaded_task_files
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

    def get_config(self, name):
        return self.global_config.get(name)

    def get_task_config(self, taskname):
        return self.task_configs.get(taskname)

    def get_task_names(self):
        return list(self.task_configs.keys())
