import logging
import threading
import queue
import secrets
import os
import time
import yaml
import glob
import subprocess


def _abspath(p):
    """安全转绝对路径；空或占位值返回空串。"""
    if not p or p == 'Unknown':
        return ''
    try:
        return os.path.abspath(p)
    except Exception:
        return ''


def _detect_ahk_exe():
    """探测 AutoHotkey v2 可执行程序路径，找不到返回 None。"""
    for p in (r'C:\Program Files\AutoHotkey\v2\AutoHotkey.exe',
              r'C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe',
              r'C:\Program Files\AutoHotkey\AutoHotkey.exe'):
        if os.path.isfile(p):
            return p
    return None
from flask import Flask, request, render_template, redirect, url_for, flash, session
from functools import wraps
from datetime import datetime

from DMR.utils import *
from DMR.utils.console_buffer import get_console_lines

class WebApi:
    def __init__(
            self,
            pipe:Tuple[queue.Queue, queue.Queue],
            engine=None,
            host='0.0.0.0',
            port=5000,
            force_login=True,
            username='admin',
            password='admin',
            config_dir='./configs',
            **kwargs,
        ) -> None:
        self.send_queue, self.recv_queue = pipe
        self.engine = engine
        self.kwargs = kwargs
        
        # WebAPI Config
        self.host = host
        self.port = port
        self.force_login = force_login
        self.username = username
        self.password = password
        
        # 强制使用高强度随机密钥，每次启动自动生成，极大提高安全性
        # 注意：这意味着每次重启程序后，所有已登录用户都需要重新登录
        self.secret_key = secrets.token_hex(32)
        self.logger = logging.getLogger(__name__)
        self.stoped = True

        self.webapp = None
        self.webapp_thread = None
        self.config_dir=config_dir
        # 打开文件夹用的 AutoHotkey 脚本（复用资源管理器窗口）；留空则回退 os.startfile（每次新开窗口）
        self.folder_opener_ahk = kwargs.get('folder_opener_ahk')
        self.ahk_exe = kwargs.get('ahk_exe') or _detect_ahk_exe()

    def login_required(self, f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if self.force_login and 'logged_in' not in session:
                return redirect(url_for('login', next=request.url))
            return f(*args, **kwargs)
        return decorated_function

    def create_app(self):
        # Silence Flask/Werkzeug non-error logs
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        
        app = Flask(__name__, template_folder='templates')
        app.secret_key = self.secret_key
        app.logger = self.logger

        @app.route('/login', methods=['GET', 'POST'])
        def login():
            if request.method == 'POST':
                username = request.form['username']
                password = request.form['password']
                if username == self.username and password == self.password:
                    session['logged_in'] = True
                    return redirect(url_for('index'))
                else:
                    return render_template('login.html', error='Invalid credentials')
            return render_template('login.html')

        @app.route('/logout')
        def logout():
            session.pop('logged_in', None)
            return redirect(url_for('login'))

        @app.route('/')
        @self.login_required
        def index():
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return self.get_tasks_data()
            return render_template('index.html', **self.get_tasks_data())

        # 保留之前的兼容性
        @app.route('/api/put_message', methods=['POST'])
        @self.login_required
        def api_v1_func():
            req_data = request.get_json()
            message = PipeMessage(**req_data['data'])
            self.send_queue.put(message)
            return 'success', 200

        @app.route('/tasks')
        @self.login_required
        def tasks_page():
            return render_template('tasks.html', **self.get_tasks_data())

        @app.route('/api/tasks')
        @self.login_required
        def tasks_api():
            return self.get_tasks_data()

        @app.route('/logs')
        @self.login_required
        def logs_page():
            return render_template('logs.html')

        @app.route('/api/console')
        @self.login_required
        def api_console():
            try:
                lines = min(2000, int(request.args.get('lines', 500)))
            except ValueError:
                lines = 500
            return {'lines': get_console_lines(lines), 'notifications': self.get_notifications()}

        @app.route('/api/open', methods=['POST'])
        @self.login_required
        def api_open():
            # 仅允许本机访问：打开的是服务端机器的资源管理器/播放器，远程点没意义
            if request.remote_addr not in ('127.0.0.1', '::1', 'localhost'):
                return {'ok': False, 'message': '仅本机可用'}, 403
            data = request.get_json(silent=True) or {}
            path = data.get('path', '')
            mode = data.get('mode', 'folder')
            if not path:
                return {'ok': False, 'message': '缺少路径'}, 400
            path = os.path.abspath(path)
            if not os.path.exists(path):
                return {'ok': False, 'message': f'路径不存在: {path}'}, 404
            try:
                if mode == 'file':
                    os.startfile(path)            # 默认关联程序（播放器）打开
                else:
                    self._open_folder(path)
                return {'ok': True}
            except Exception as e:
                self.logger.error(f'打开路径失败 ({mode}): {path}: {e}')
                return {'ok': False, 'message': str(e)}, 500

        @app.route('/api/check_config', methods=['POST'])
        @self.login_required
        def check_config_api():
            try:
                content = request.json.get('content')
                yaml.safe_load(content)
                return {'valid': True, 'message': '配置文件格式正确 (Valid YAML)'}
            except Exception as e:
                return {'valid': False, 'message': f'配置文件格式错误: {e}'}

        @app.route('/config')
        @self.login_required
        def config_list():
            # 页面内容由前端轮询 /api/config/list 实时构建
            if not os.path.exists(self.config_dir):
                os.makedirs(self.config_dir)
            return render_template('config_list.html')

        @app.route('/api/config/pending')
        @self.login_required
        def config_pending_api():
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'pending': []}
            return {'pending': dmr.config.get_pending_changes()}

        @app.route('/api/config/apply/<filename>', methods=['POST'])
        @self.login_required
        def config_apply_api(filename):
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            # 安全：只允许 config_dir 内的文件名，去掉任何路径成分
            safe = os.path.basename(filename)
            result = dmr.apply_config_change(safe)
            return result

        @app.route('/api/config/list')
        @self.login_required
        def config_list_api():
            """配置文件实时状态（供配置页轮询重建表格）+ 红点汇总。
            列出目录下所有可编辑 yml；其中 DMR 任务文件叠加“待应用/推迟”等真实状态。"""
            dmr = getattr(self.engine, 'dmr', None)
            states = {}          # filename -> 状态（仅磁盘上存在的任务文件）
            extra_deleted = []   # 已加载但磁盘已删的任务文件
            if dmr is not None:
                try:
                    for s in dmr.config.get_all_states():
                        if s['exists']:
                            states[s['filename']] = s
                        else:
                            extra_deleted.append(s)
                except Exception as e:
                    self.logger.debug(f'读取配置状态失败: {e}')

            configs = []
            if os.path.isdir(self.config_dir):
                for f in sorted(glob.glob(os.path.join(self.config_dir, '*.yml'))):
                    fn = os.path.basename(f)
                    if fn.lower() == 'global.yml':
                        continue
                    taskname = fn[4:-4] if fn.startswith('DMR-') else fn[:-4]
                    st = states.get(fn)
                    configs.append({
                        'filename': fn,
                        'taskname': st['taskname'] if st else taskname,
                        'status': st['status'] if st else None,
                        'exists': True,
                        'is_example': fn.startswith('example-'),
                    })
            for s in extra_deleted:
                configs.append({
                    'filename': s['filename'], 'taskname': s['taskname'],
                    'status': s['status'], 'exists': False,
                    'is_example': s['filename'].startswith('example-'),
                })
            return {'configs': configs, 'notifications': self.get_notifications()}

        @app.route('/api/config/defer/<filename>', methods=['POST'])
        @self.login_required
        def config_defer_api(filename):
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            ok = dmr.config.defer(os.path.basename(filename))
            return {'ok': ok, 'message': '已推迟到重启时生效' if ok else '未找到该文件'}

        @app.route('/api/config/undefer/<filename>', methods=['POST'])
        @self.login_required
        def config_undefer_api(filename):
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            dmr.config.undefer(os.path.basename(filename))
            return {'ok': True, 'message': '已撤销推迟'}

        @app.route('/api/config/defer_all', methods=['POST'])
        @self.login_required
        def config_defer_all_api():
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            cnt = 0
            for s in dmr.config.get_pending_changes():
                if dmr.config.defer(s['filename']):
                    cnt += 1
            return {'ok': True, 'message': f'已推迟 {cnt} 个文件到重启'}

        @app.route('/config/create', methods=['GET', 'POST'])
        @self.login_required
        def config_create():
            return config_edit(filename=None)

        @app.route('/config/edit/<filename>', methods=['GET', 'POST'])
        @self.login_required
        def config_edit(filename):
            content = ""
            check_result = None
            
            if filename:
                filepath = os.path.join(self.config_dir, filename)
                if not os.path.exists(filepath):
                    flash(f'File {filename} not found.', 'error')
                    return redirect(url_for('config_list'))
                
                if request.method == 'GET':
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()

            if request.method == 'POST':
                content = request.form['content']
                # Normalize line endings to avoid triple spacing (CRLF -> LF)
                content = content.replace('\r\n', '\n')
                action = request.form['action']
                new_filename = request.form.get('new_filename', filename)
                
                # Validation
                try:
                    yaml.safe_load(content)
                    valid = True
                    check_result = "YAML Format OK"
                except Exception as e:
                    valid = False
                    check_result = f"YAML Error: {e}"
                
                if action == 'save':
                    if filename and filename.startswith('example-'):
                        flash('示例文件不支持修改。', 'error')
                        return redirect(url_for('config_list'))
                    
                    if valid:
                        if not new_filename.endswith('.yml'):
                             new_filename += '.yml'
                        
                        save_path = os.path.join(self.config_dir, new_filename)
                        try:
                            with open(save_path, 'w', encoding='utf-8') as f:
                                f.write(content)
                            flash(f'Config {new_filename} saved successfully.', 'success')
                            return redirect(url_for('config_list'))
                        except Exception as e:
                            flash(f'Error saving file: {e}', 'error')
                    else:
                        flash('Invalid YAML format. Please fix errors before saving.', 'error')

            return render_template('config_edit.html', filename=filename, content=content, check_result=check_result)

        @app.route('/config/delete/<filename>', methods=['POST'])
        @self.login_required
        def config_delete(filename):
            if filename:
                if filename.startswith('example-'):
                    flash('示例文件不支持删除。', 'error')
                    return redirect(url_for('config_list'))
                
                filepath = os.path.join(self.config_dir, filename)
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        flash(f'Config {filename} deleted successfully.', 'success')
                    except Exception as e:
                        flash(f'Error deleting file: {e}', 'error')
                else:
                    flash(f'File {filename} not found.', 'error')
            return redirect(url_for('config_list'))

        @app.route('/api/failed_uploads/retry/<uuid>', methods=['POST'])
        @self.login_required
        def failed_uploads_retry(uuid):
            if self.engine and 'uploader' in self.engine.plugin_dict:
                uploader = self.engine.plugin_dict['uploader']['class']
                if uploader:
                    if uploader.retry_task(uuid):
                        return {'status': 'success', 'message': 'Task retry scheduled.'}
                    else:
                        return {'status': 'error', 'message': 'Task not found or failed to retry.'}
            return {'status': 'error', 'message': 'Uploader not available.'}

        @app.route('/api/upload_tasks/<uuid>/stop', methods=['POST'])
        @self.login_required
        def upload_task_stop(uuid):
            if self.engine and 'uploader' in self.engine.plugin_dict:
                uploader = self.engine.plugin_dict['uploader']['class']
                if uploader:
                    if uploader.stop_task(uuid):
                        return {'status': 'success', 'message': '已停止上传任务'}
                    else:
                        return {'status': 'error', 'message': '任务不存在或已结束'}
            return {'status': 'error', 'message': 'Uploader not available.'}

        @app.route('/api/failed_uploads/delete/<uuid>', methods=['POST'])
        @self.login_required
        def failed_uploads_delete(uuid):
            if self.engine and 'uploader' in self.engine.plugin_dict:
                uploader = self.engine.plugin_dict['uploader']['class']
                if uploader:
                    if uploader.delete_failed_task(uuid):
                        return {'status': 'success', 'message': 'Task deleted.'}
                    else:
                        return {'status': 'error', 'message': 'Task not found.'}
            return {'status': 'error', 'message': 'Uploader not available.'}

        @app.route('/api/failed_renders/retry/<uuid>', methods=['POST'])
        @self.login_required
        def failed_renders_retry(uuid):
            if self.engine and 'render' in self.engine.plugin_dict:
                render = self.engine.plugin_dict['render']['class']
                if render:
                    if render.retry_task(uuid):
                        return {'status': 'success', 'message': 'Task retry scheduled.'}
                    else:
                        return {'status': 'error', 'message': 'Task not found or failed to retry.'}
            return {'status': 'error', 'message': 'Render not available.'}

        def _get_downloader():
            if self.engine and 'downloader' in self.engine.plugin_dict:
                return self.engine.plugin_dict['downloader']['class']
            return None

        @app.route('/api/tasks/<taskname>/segment', methods=['POST'])
        @self.login_required
        def api_task_segment(taskname):
            dl = _get_downloader()
            if dl:
                dl.cmd_segment(taskname)
                return {'status': 'success', 'message': f'{taskname} 手动分段指令已发送'}
            return {'status': 'error', 'message': 'Downloader not available'}, 500

        @app.route('/api/tasks/<taskname>/offline', methods=['POST'])
        @self.login_required
        def api_task_offline(taskname):
            dl = _get_downloader()
            if dl:
                dl.cmd_offline(taskname)
                return {'status': 'success', 'message': f'{taskname} 强制下播指令已发送'}
            return {'status': 'error', 'message': 'Downloader not available'}, 500

        @app.route('/api/tasks/<taskname>/force_offline_time', methods=['POST'])
        @self.login_required
        def api_task_force_offline_time(taskname):
            dl = _get_downloader()
            if dl:
                time_str = (request.get_json() or {}).get('time', '')
                dl.cmd_force_offline_time(taskname, time_str)
                msg = f'{taskname} 定时下播已取消' if time_str == '' else f'{taskname} 定时下播设置为 {time_str}'
                return {'status': 'success', 'message': msg}
            return {'status': 'error', 'message': 'Downloader not available'}, 500

        @app.route('/api/tasks/<taskname>/force_live', methods=['POST'])
        @self.login_required
        def api_task_force_live(taskname):
            dl = _get_downloader()
            if dl:
                dl.cmd_force_live(taskname)
                return {'status': 'success', 'message': f'{taskname} 已收到手动开播指令'}
            return {'status': 'error', 'message': 'Downloader not available'}, 500

        @app.route('/api/restart', methods=['POST'])
        @self.login_required
        def api_restart():
            """等所有任务空闲后重启程序"""
            if self.engine:
                self.engine.restart_when_idle()
                return {'status': 'success', 'message': '已设置空闲重启，等待所有任务完成后自动重启'}
            return {'status': 'error', 'message': 'Engine not available'}, 500

        @app.route('/api/shutdown', methods=['POST'])
        @self.login_required
        def api_shutdown():
            """停止所有任务并立即退出程序"""
            def _shutdown():
                time.sleep(1)  # 给浏览器留出接收响应的时间
                self.logger.info('收到退出指令，正在停止所有任务并退出程序...')
                try:
                    if self.engine:
                        self.engine.stop()
                except Exception as e:
                    self.logger.exception(e)
                os._exit(0)
            threading.Thread(target=_shutdown, daemon=True).start()
            return {'status': 'success', 'message': '程序正在退出...'}

        @app.route('/api/failed_renders/delete/<uuid>', methods=['POST'])
        @self.login_required
        def failed_renders_delete(uuid):
            if self.engine and 'render' in self.engine.plugin_dict:
                render = self.engine.plugin_dict['render']['class']
                if render:
                    if render.delete_failed_task(uuid):
                        return {'status': 'success', 'message': 'Task deleted.'}
                    else:
                        return {'status': 'error', 'message': 'Task not found.'}
            return {'status': 'error', 'message': 'Render not available.'}

        return app

    def get_tasks_data(self):
        # Get Recording Tasks
        recording_tasks = []
        if self.engine and 'downloader' in self.engine.plugin_dict:
            downloader = self.engine.plugin_dict['downloader']['class']
            if downloader:
                for taskname, task in downloader.download_tasks.items():
                    live_state = getattr(task, 'live_state', None)
                    live_state_str = str(live_state).lower() if live_state is not None else ''
                    if 'replay' in live_state_str:
                        status = 2   # 回放中
                    elif 'stopping' in live_state_str:
                        status = 3   # 等待确认下播
                    elif not getattr(task, 'stoped', True):
                        status = 1   # 录制中
                    else:
                        status = 0   # 未开播/离线
                    duration = "未开播"
                    live_duration = ""   # 从本场开播到现在的录制时长

                    if status == 1:
                        now = datetime.now()
                        seg_start = getattr(task, 'segment_start_time', None)
                        live_start = getattr(task, 'live_start_time', None)
                        # 第一段:分段即整场。取流前的 GetRoomInfo/GetStreamerInfo 会让
                        # segment_start_time 比 live_start_time 晚约1秒,导致"分段时长比本场时长少1秒"。
                        # 这里第一段直接对齐到本场起点,后续分段各用自己的起点;并统一用一次 now 避免跨秒抖动。
                        if getattr(task, 'segment_id', 1) <= 1 and live_start:
                            seg_start = live_start
                        if seg_start:
                            duration = str(now - seg_start).split('.')[0]
                        if live_start:
                            live_duration = str(now - live_start).split('.')[0]

                    record_windows_ranges = (task.advanced_video_args or {}).get('record_windows', {}).get('ranges')
                    if record_windows_ranges:
                        record_windows_str = ', '.join(f"{r['start']}-{r['end']}" for r in record_windows_ranges)
                    else:
                        record_windows_str = '全天'

                    recording_tasks.append({
                        'name': taskname,
                        'platform': task.plat,
                        'url': task.url,
                        'status': status,
                        'duration': duration,
                        'live_duration': live_duration,
                        'force_offline_time': getattr(task, 'force_offline_time', '') or '',
                        'record_windows': record_windows_str,
                        'stop_wait_time': int(getattr(task, 'stop_wait_time', 0)),
                        'start_check_interval': (task.advanced_video_args or {}).get('start_check_interval', 60),
                        'stop_check_interval': (task.advanced_video_args or {}).get('stop_check_interval', 60),
                        'trust_onair_at_startup': (task.advanced_video_args or {}).get('trust_onair_at_startup', True),
                        'output_dir': _abspath(getattr(task, 'output_dir', '')),
                    })

        # Get Upload Tasks
        upload_tasks_list = []
        failed_tasks_list = []
        if self.engine and 'uploader' in self.engine.plugin_dict:
            uploader = self.engine.plugin_dict['uploader']['class']
            if uploader:
                for uuid, task in uploader.upload_tasks.items():
                    upload_tasks_list.append({
                        'uuid': uuid,
                        'files': [{'name': os.path.join(os.path.basename(os.path.dirname(f.path)), os.path.basename(f.path))} for f in task.get('files', [])],
                        'account': task.get('args', {}).get('account', 'Unknown'),
                        'engine': task.get('engine', 'Unknown'),
                        'is_sync': bool(task.get('stream_queue')),
                        'status': task.get('status', 'waiting')
                    })
                
                for uuid, task in uploader.failed_tasks.items():
                    failed_tasks_list.append({
                        'uuid': uuid,
                        'files': [{'name': os.path.join(os.path.basename(os.path.dirname(f.path)), os.path.basename(f.path))} for f in task.get('files', [])],
                        'account': task.get('args', {}).get('account', 'Unknown'),
                        'engine': task.get('engine', 'Unknown'),
                        'command': task.get('command'),
                    })

        # Get Render Tasks
        render_tasks_list = []
        failed_renders_list = []
        completed_renders_list = []
        if self.engine and 'render' in self.engine.plugin_dict:
            render = self.engine.plugin_dict['render']['class']
            if render:
                # 已渲染成功的视频（最新在前，最多展示 100 条）
                for rec in list(getattr(render, 'completed_tasks', []))[::-1][:100]:
                    out_p = rec.get('output') or 'Unknown'
                    vid_p = rec.get('video') or 'Unknown'
                    rec_time = rec.get('time')
                    out_abs = _abspath(out_p)
                    vid_abs = _abspath(vid_p)
                    completed_renders_list.append({
                        'video': os.path.join(os.path.basename(os.path.dirname(vid_p)), os.path.basename(vid_p)),
                        'output': os.path.join(os.path.basename(os.path.dirname(out_p)), os.path.basename(out_p)),
                        'video_path': vid_abs,
                        'output_path': out_abs,
                        'video_exists': bool(vid_abs) and os.path.exists(vid_abs),
                        'output_exists': bool(out_abs) and os.path.exists(out_abs),
                        'mode': rec.get('mode', 'Unknown'),
                        'time': rec_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(rec_time, 'strftime') else '',
                    })

                for uuid, task in render.render_tasks.items():
                    video_path = task.get('video').path if task.get('video') else 'Unknown'
                    render_tasks_list.append({
                        'video': os.path.join(os.path.basename(os.path.dirname(video_path)), os.path.basename(video_path)),
                        'output': os.path.join(os.path.basename(os.path.dirname(task.get('output', 'Unknown'))), os.path.basename(task.get('output', 'Unknown'))),
                        'video_path': _abspath(video_path),
                        'output_path': _abspath(task.get('output', '')),
                        'mode': task.get('mode', 'Unknown'),
                        'status': task.get('status', 'waiting')
                    })
                
                for uuid, task in render.failed_tasks.items():
                    video_path = task.get('video').path if task.get('video') else 'Unknown'
                    failed_renders_list.append({
                        'uuid': uuid,
                        'video': os.path.join(os.path.basename(os.path.dirname(video_path)), os.path.basename(video_path)),
                        'output': os.path.join(os.path.basename(os.path.dirname(task.get('output', 'Unknown'))), os.path.basename(task.get('output', 'Unknown'))),
                        'video_path': _abspath(video_path),
                        'output_path': _abspath(task.get('output', '')),
                        'mode': task.get('mode', 'Unknown'),
                    })
        
        # Get Pending Cleanup Tasks
        pending_clean_list = []
        if self.engine and 'cleaner' in self.engine.plugin_dict:
            cleaner = self.engine.plugin_dict['cleaner']['class']
            for entry in list(cleaner._read_pending()):
                pending_clean_list.append({
                    'method'    : entry.get('method', ''),
                    'files'     : [os.path.join(os.path.basename(os.path.dirname(f)), os.path.basename(f)) for f in entry.get('files', [])],
                    'note'      : entry.get('note', ''),
                    'execute_at': entry.get('created_at', 0) + entry.get('delay', 0),
                })

        # Get Merged Videos（合并产物记录在各任务的 LiveEvents 实例上）
        completed_merges_list = []
        if self.engine:
            for taskname, info in getattr(self.engine, 'task_dict', {}).items():
                rt = info.get('class')
                ev = getattr(rt, 'event_class', None)
                for rec in list(getattr(ev, 'merged_videos', []))[::-1][:100]:
                    out_p = rec.get('output')
                    out_abs = _abspath(out_p)
                    rec_time = rec.get('time')
                    completed_merges_list.append({
                        'task': taskname,
                        'output': os.path.join(os.path.basename(os.path.dirname(out_p)), os.path.basename(out_p)) if out_p else '',
                        'output_path': out_abs,
                        'output_exists': bool(out_abs) and os.path.exists(out_abs),
                        'type': rec.get('type', ''),
                        'segments': rec.get('segments', 0),
                        'time': rec_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(rec_time, 'strftime') else '',
                    })

        return {
            'recording_tasks'   : recording_tasks,
            'upload_tasks'      : upload_tasks_list,
            'failed_tasks'      : failed_tasks_list,
            'render_tasks'      : render_tasks_list,
            'failed_renders'    : failed_renders_list,
            'completed_renders' : completed_renders_list,
            'completed_merges'  : completed_merges_list,
            'pending_clean_tasks': pending_clean_list,
            'notifications'     : self.get_notifications(),
        }

    def _open_folder(self, path):
        """打开文件夹：优先用 AutoHotkey 脚本复用资源管理器窗口（参考用户的 Sublime 插件做法），
        否则回退 os.startfile（每次新开窗口）。"""
        ahk_script = self.folder_opener_ahk
        if ahk_script and os.path.isfile(ahk_script):
            try:
                if self.ahk_exe and os.path.isfile(self.ahk_exe):
                    subprocess.Popen([self.ahk_exe, ahk_script, path])
                else:
                    # 退回系统关联方式执行 .ahk
                    subprocess.Popen([ahk_script, path], shell=True)
                self.logger.info(f'[open_folder] 用 AHK 打开文件夹: {path}')
                return
            except Exception as e:
                self.logger.error(f'[open_folder] AHK 调用失败，回退 os.startfile: {e}')
        else:
            self.logger.info(f'[open_folder] 未配置/找不到 AHK 脚本(folder_opener_ahk={ahk_script})，'
                             f'用 os.startfile 打开（会新开窗口）: {path}')
        os.startfile(path)

    def get_notifications(self):
        """导航红点汇总：搭便车塞进各页面已有的轮询响应，不新增请求。
        config/failed 为“待办型”(数量>0亮)；logs_err 为“新消息型”(前端用 localStorage 比对已读)。"""
        n = {'config': 0, 'failed': 0, 'logs_err': 0}
        dmr = getattr(self.engine, 'dmr', None)
        if dmr is not None:
            try:
                n['config'] = len(dmr.config.get_pending_changes())
            except Exception:
                pass
        try:
            if self.engine and 'uploader' in self.engine.plugin_dict:
                up = self.engine.plugin_dict['uploader']['class']
                if up:
                    n['failed'] += len(getattr(up, 'failed_tasks', {}))
            if self.engine and 'render' in self.engine.plugin_dict:
                rd = self.engine.plugin_dict['render']['class']
                if rd:
                    n['failed'] += len(getattr(rd, 'failed_tasks', {}))
        except Exception:
            pass
        try:
            from DMR.utils.console_buffer import get_error_count
            n['logs_err'] = get_error_count()
        except Exception:
            pass
        return n

    def start_helper(self):
        self.webapp = self.create_app()
        self.webapp.run(host=self.host, port=self.port, debug=False, use_reloader=False)

    def start(self):
        self.webapp_thread = threading.Thread(target=self.start_helper, daemon=True)
        self.webapp_thread.start()
        display_host = '127.0.0.1' if self.host == '0.0.0.0' else self.host
        url = f'http://{display_host}:{self.port}'
        self.logger.info(f'DanmakuRender 5 started at {url}')
        # 延迟用默认浏览器打开 WebService（等服务起好）；重启的子进程带 DMR_NO_BROWSER 标记，跳过以免重复开标签
        if os.environ.get('DMR_NO_BROWSER') != '1':
            import webbrowser
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    def stop(self):
        self.stoped = True
        # Flask doesn't have a clean stop method when running with .run(), 
        # but since it's a daemon thread, it will die when main process dies.
        # Alternatively, we could use a production server like waitress/gunicorn but that adds dependencies.
