import logging
import threading
import queue
import secrets
import os
import time
import json
import yaml
import subprocess
import math
import gzip


def _abspath(p):
    """安全转绝对路径；空或占位值返回空串。"""
    if not p or p == 'Unknown':
        return ''
    try:
        return os.path.abspath(p)
    except Exception:
        return ''


def _disp_task(source):
    """路由用的 source 是 'replay/{任务名}'（见 ReplayTask 加前缀）；展示时剥掉前缀，
    只显示任务名，与「已完成/人工处理」队列里的任务名保持一致。"""
    s = source or ''
    return s[len('replay/'):] if s.startswith('replay/') else s


def _format_synctitle_result(result: dict) -> str:
    """把 sync_section_episode_titles 的结果格式化成纯文本（从原 api.py 迁入），方便快捷指令展示。"""
    lines = []
    if result.get("message"):
        lines.append(result["message"])
        lines.append("")
    changed = result.get("changed") or []
    errors = result.get("errors") or []
    if changed:
        lines.append(f"✅ 本次已成功同步 {len(changed)} 个分P 标题：")
        lines.append("")
        for idx, c in enumerate(changed, start=1):
            lines.append(f"{idx}️⃣ {c.get('old_title','')}➜➜➜：\n{c.get('new_title','')}")
            lines.append("")
    if errors:
        lines.append(f"❌ 以下 {len(errors)} 个分P 修改失败：")
        lines.append("")
        for idx, e in enumerate(errors, start=1):
            block = [f"{idx}️⃣ {e.get('old_title','')}➜➜➜：", f"{e.get('new_title','')}",
                     f" 错误：{e.get('error','未知错误')}"]
            if e.get("aid"):
                block.append(f"    aid：{e.get('aid')}")
            lines.append("\n".join(block))
            lines.append("")
    if not lines:
        lines.append("本次没有需要修改的分P。")
    return "\n".join(lines).rstrip()


def _detect_ahk_exe():
    """探测 AutoHotkey v2 可执行程序路径，找不到返回 None。"""
    for p in (r'C:\Program Files\AutoHotkey\v2\AutoHotkey.exe',
              r'C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe',
              r'C:\Program Files\AutoHotkey\AutoHotkey.exe'):
        if os.path.isfile(p):
            return p
    return None
from flask import Flask, request, render_template, redirect, url_for, flash, session, Response
from functools import wraps
from datetime import datetime

from DMR.utils import *
from DMR.utils.console_buffer import (
    get_console_lines,
    get_console_history,
    subscribe as console_subscribe,
    unsubscribe as console_unsubscribe,
)
from DMR.utils.session_record import encode_dt, humanize_dt
from DMR.Login import CookieManager
from DMR.EmojiPack import bilibili as emoji_bilibili, douyin as emoji_douyin, douyu as emoji_douyu, common as emoji_common

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
        # 本次进程启动的唯一ID：前端轮询 /api/instance，发现ID变了(=程序已重启完成)就自动刷新页面
        self.instance_id = secrets.token_hex(8)
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
                    return redirect(url_for('record'))
                else:
                    return render_template('login.html', error='Invalid credentials')
            return render_template('login.html')

        @app.route('/logout')
        def logout():
            session.pop('logged_in', None)
            return redirect(url_for('login'))

        @app.route('/api/instance')
        def api_instance():
            # 不需登录：只返回本次进程启动的随机ID，供前端检测「程序是否已重启完成」以自动刷新页面。
            # 无任何敏感信息，且刻意不加登录校验——这样即便重启后登录密钥变了、session 失效，也能检测到。
            return {'id': self.instance_id}

        @app.route('/')
        @self.login_required
        def record():   # 录制页(首页)：正在录制的任务
            return render_template('record.html', **self.get_tasks_data())

        # 保留之前的兼容性
        @app.route('/api/put_message', methods=['POST'])
        @self.login_required
        def api_v1_func():
            req_data = request.get_json()
            message = PipeMessage(**req_data['data'])
            self.send_queue.put(message)
            return 'success', 200

        @app.route('/process')
        @self.login_required
        def process():   # 处理页：渲染/上传/清理/挂起 等录制后的处理队列
            return render_template('process.html', **self.get_tasks_data())

        @app.route('/api/tasks')
        @self.login_required
        def tasks_api():
            return self.get_tasks_data()

        # 人工处理完挂起任务后回传结果：render/merge 传 mp4 路径，upload 传 'done'
        @app.route('/api/resume', methods=['POST'])
        @self.login_required
        def resume_api():
            req = request.get_json() or {}
            taskname, hold_id = req.get('task'), req.get('hold_id')
            result = req.get('result')
            # 清洗用户填的路径：去掉首尾空格和引号（Windows「复制为路径」会带 "..."），
            # 否则带引号的路径会让后续 rename/合并报 WinError 123。'done'/'__ignore__' 不受影响。
            if isinstance(result, str):
                result = result.strip().strip('"\'').strip()
            if not taskname or not hold_id:
                return 'missing task/hold_id', 400
            # 提交前校验，当场打回错误：render/merge 回传的必须是真实存在的视频文件，
            # 否则错误路径会流进管线，在下游的上传/合并处炸出难排查的失败
            info = (getattr(self.engine, 'task_dict', {}) or {}).get(taskname) if self.engine else None
            ev = getattr(info.get('class'), 'event_class', None) if info else None
            hold = (getattr(ev, 'held_tasks', {}) or {}).get(hold_id)
            if hold is None:
                return '挂起任务不存在或已被处理，请刷新页面', 400
            if hold.get('stage') in ('render', 'merge') and result not in ('done', '__ignore__'):
                if not result or not os.path.isfile(result):
                    return f'文件不存在: {result}', 400
                if not isvideo(result.lower()):
                    return f'不是视频文件: {result}', 400
            self.send_queue.put(PipeMessage(
                source='webservice', target=f'replay/{taskname}', event='resume',
                data={'hold_id': hold_id, 'result': result},
            ))
            return 'success', 200

        # 人工处理「重试」：把挂起任务原来的渲染/合并/上传操作重新跑一遍
        @app.route('/api/retry', methods=['POST'])
        @self.login_required
        def retry_api():
            req = request.get_json() or {}
            taskname, hold_id = req.get('task'), req.get('hold_id')
            if not taskname or not hold_id:
                return 'missing task/hold_id', 400
            self.send_queue.put(PipeMessage(
                source='webservice', target=f'replay/{taskname}', event='retry',
                data={'hold_id': hold_id},
            ))
            return 'success', 200

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

        @app.route('/api/console/stream')
        @self.login_required
        def api_console_stream():
            """带序号的 SSE 日志，断线后从 Last-Event-ID 继续。"""
            try:
                last_id = int(request.headers.get('Last-Event-ID', request.args.get('last_id', '0')) or 0)
            except ValueError:
                last_id = 0
            def gen():
                q, history = console_subscribe(5000, last_id)
                try:
                    yield f'event: history\ndata: {json.dumps(history, ensure_ascii=False)}\n\n'
                    while True:
                        try:
                            item = q.get(timeout=15)
                            yield f'id: {item["id"]}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n'
                        except queue.Empty:
                            yield ': ping\n\n'   # 心跳，防连接被中途判超时断开
                finally:
                    console_unsubscribe(q)
            return Response(gen(), mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

        @app.route('/api/console/history')
        @self.login_required
        def api_console_history():
            try:
                before = int(request.args.get('before', '0') or 0)
                limit = min(1000, max(1, int(request.args.get('limit', '500') or 500)))
            except ValueError:
                return {'error': 'invalid pagination'}, 400
            payload = json.dumps(
                {'lines': get_console_history(before, limit)},
                ensure_ascii=False,
            ).encode('utf-8')
            return Response(
                gzip.compress(payload),
                mimetype='application/json',
                headers={'Content-Encoding': 'gzip', 'Cache-Control': 'no-cache'},
            )

        @app.route('/api/notifications')
        @self.login_required
        def api_notifications():
            """只取红点通知（日志页改用 SSE 后，徽标单独轻量轮询这个）。"""
            return {'notifications': self.get_notifications()}

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

        @app.route('/config')
        @self.login_required
        def config_list():
            # 页面内容由前端轮询 /api/config/list 实时构建
            if not os.path.exists(self.config_dir):
                os.makedirs(self.config_dir)
            return render_template('config_list.html')

        @app.route('/api/config/list')
        @self.login_required
        def config_list_api():
            """配置文件实时状态：直接照 Config.get_all_states() 渲染（global + 各任务的启用/变更状态）。
            Config 是唯一权威，这里只转发，不再自己扫目录。"""
            dmr = getattr(self.engine, 'dmr', None)
            configs = []
            if dmr is not None:
                try:
                    configs = dmr.config.get_all_states()
                except Exception as e:
                    self.logger.debug(f'读取配置状态失败: {e}')
            return {'configs': configs, 'notifications': self.get_notifications()}

        @app.route('/api/config/toggle/<filename>', methods=['POST'])
        @self.login_required
        def config_toggle_api(filename):
            """启用/停用某任务：写回配置文件的 enable 字段并即时增删任务。body: {"enabled": true/false}"""
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            enabled = bool((request.get_json(silent=True) or {}).get('enabled', True))
            return dmr.toggle_task(os.path.basename(filename), enabled)

        def _resolve_config_path(dmr, filename):
            """按文件名找到配置文件的实际磁盘路径：任务配置 or 全局配置，找不到返回 None。"""
            fname = os.path.basename(filename)
            path = dmr.config.find_task_file(fname)
            if path is None and fname == os.path.basename(dmr.config.global_config_path):
                path = dmr.config.global_config_path
            return path

        @app.route('/api/config/content/<filename>')
        @self.login_required
        def config_content_api(filename):
            """读取配置文件原始 YAML 文本，供网页「编辑」弹窗展示。"""
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            path = _resolve_config_path(dmr, filename)
            if not path or not os.path.exists(path):
                return {'ok': False, 'message': '找不到该配置文件'}, 404
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                return {'ok': False, 'message': f'读取失败: {e}'}, 500
            return {'ok': True, 'content': content}

        @app.route('/api/config/save/<filename>', methods=['POST'])
        @self.login_required
        def config_save_api(filename):
            """保存编辑后的 YAML 文本到磁盘。不在这里热应用——写回磁盘后交给现有的
            变更检测/查看改动/应用/撤销机制处理，跟外部直接改文件效果一样。"""
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            fname = os.path.basename(filename)
            if fname.startswith('example-'):
                return {'ok': False, 'message': '示例文件不支持保存修改'}, 400
            content = (request.get_json(silent=True) or {}).get('content', '')
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as e:
                return {'ok': False, 'message': f'YAML 格式错误: {e}'}, 400
            path = _resolve_config_path(dmr, filename)
            if not path:
                return {'ok': False, 'message': '找不到该配置文件'}, 404
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
            except Exception as e:
                return {'ok': False, 'message': f'保存失败: {e}'}, 500
            return {'ok': True, 'message': '已保存'}

        # ---- 配置变更：提醒(红点) + 查看改动 + 手动应用 + 撤销 + 推迟（不自动热重载）----
        @app.route('/api/config/pending')
        @self.login_required
        def config_pending_api():
            dmr = getattr(self.engine, 'dmr', None)
            return {'pending': dmr.config.get_pending_changes() if dmr else []}

        @app.route('/api/config/diff/<filename>')
        @self.login_required
        def config_diff_api(filename):
            dmr = getattr(self.engine, 'dmr', None)
            return {'diff': dmr.config.get_diff(os.path.basename(filename)) if dmr else []}

        @app.route('/api/config/apply/<filename>', methods=['POST'])
        @self.login_required
        def config_apply_api(filename):
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            return dmr.apply_config_change(os.path.basename(filename))

        @app.route('/api/config/revert/<filename>', methods=['POST'])
        @self.login_required
        def config_revert_api(filename):
            dmr = getattr(self.engine, 'dmr', None)
            if dmr is None:
                return {'ok': False, 'message': '引擎未就绪'}, 503
            ok = dmr.config.revert(os.path.basename(filename))
            return {'ok': ok, 'message': '已撤销改动' if ok else '撤销失败（无可回退的版本）'}

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
            cnt = sum(1 for s in dmr.config.get_pending_changes() if dmr.config.defer(s['filename']))
            return {'ok': True, 'message': f'已推迟 {cnt} 个文件到重启'}

        # ---- Cookie 续期 + 表情包检查：已并入配置页(config_list.html 的 Cookie/Emoji 卡片)，
        #      这里只保留 /cookies 旧地址重定向到配置页，避免老书签 404 ----
        @app.route('/cookies')
        @self.login_required
        def cookies_list():
            return redirect(url_for('config_list'))

        @app.route('/api/cookies/list')
        @self.login_required
        def cookies_list_api():
            return {'cookies': CookieManager().list_cookies()}

        @app.route('/api/cookies/renew/<filename>', methods=['POST'])
        @self.login_required
        def cookies_renew_api(filename):
            ok, message = CookieManager().renew(os.path.basename(filename))
            return {'ok': ok, 'message': message}

        # ---- 表情包检查：查缺失 -> 前端展示 -> 用户确认后再下载 ----
        EMOJI_PLATFORMS = [
            {'platform': 'bilibili', 'label': 'B站', 'dir': emoji_bilibili.PACK_DIR, 'supported': True},
            {'platform': 'douyin',   'label': '抖音', 'dir': emoji_douyin.PACK_DIR,  'supported': True},
            {'platform': 'douyu',    'label': '斗鱼', 'dir': emoji_douyu.PACK_DIR,   'supported': True},
        ]

        @app.route('/api/emoji/list')
        @self.login_required
        def emoji_list_api():
            return {'emojis': [{
                'platform': p['platform'], 'label': p['label'],
                'dir': _abspath(p['dir']), 'count': emoji_common.count_pack(p['dir']),
                'supported': p['supported'],
            } for p in EMOJI_PLATFORMS]}

        @app.route('/api/emoji/check', methods=['POST'])
        @self.login_required
        def emoji_check_api():
            body = request.get_json(silent=True) or {}
            platform = body.get('platform')
            try:
                if platform == 'bilibili':
                    result = emoji_bilibili.check()
                elif platform == 'douyin':
                    result = emoji_douyin.check()
                elif platform == 'douyu':
                    result = emoji_douyu.check()
                else:
                    return {'ok': False, 'message': f'未知平台: {platform}'}, 400
                return {'ok': True, **result}
            except Exception as e:
                self.logger.exception('表情包检查失败：platform=%s', platform)
                message = str(e).strip() or repr(e)
                return {
                    'ok': False,
                    'message': f'{type(e).__name__}: {message}',
                }

        @app.route('/api/emoji/download', methods=['POST'])
        @self.login_required
        def emoji_download_api():
            body = request.get_json(silent=True) or {}
            platform = body.get('platform')
            items = body.get('items') or []
            try:
                if platform == 'bilibili':
                    result = emoji_bilibili.download(items)
                elif platform == 'douyin':
                    result = emoji_douyin.download(items)
                elif platform == 'douyu':
                    result = emoji_douyu.download(items)
                else:
                    return {'ok': False, 'message': f'未知平台: {platform}'}, 400
                return {'ok': True, **result}
            except Exception as e:
                return {'ok': False, 'message': str(e)}

        @app.route('/api/emoji/delete-extras', methods=['POST'])
        @self.login_required
        def emoji_delete_extras_api():
            body = request.get_json(silent=True) or {}
            platform = body.get('platform')
            items = body.get('items') or []
            try:
                modules = {
                    'bilibili': emoji_bilibili,
                    'douyin': emoji_douyin,
                    'douyu': emoji_douyu,
                }
                module = modules.get(platform)
                if not module:
                    return {'ok': False, 'message': f'未知平台: {platform}'}, 400
                return {'ok': True, **module.delete_extras(items)}
            except Exception as e:
                self.logger.exception('删除多余表情失败：platform=%s', platform)
                message = str(e).strip() or repr(e)
                return {'ok': False, 'message': f'{type(e).__name__}: {message}'}

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

        @app.route('/api/tasks/<taskname>/check_live', methods=['POST'])
        @self.login_required
        def api_task_check_live(taskname):
            dl = _get_downloader()
            if dl:
                ok, message = dl.cmd_check_live(taskname)
                return {'status': 'success' if ok else 'error', 'message': message}
            return {'status': 'error', 'message': 'Downloader not available'}, 500

        @app.route('/api/tasks/<taskname>/pipeline')
        @self.login_required
        def api_task_pipeline(taskname):
            """该任务在内存里的全部会话状态（liveevents 的 state_dict/session_data 现场序列化）。
            组处理完释放内存后即消失；重启后为空——与四个队列的行为一致。"""
            info = (getattr(self.engine, 'task_dict', {}) or {}).get(taskname) if self.engine else None
            ev = getattr(info.get('class'), 'event_class', None) if info else None
            groups = []
            if ev is not None:
                now_str = datetime.now().isoformat(timespec='seconds')
                session_data = getattr(ev, 'session_data', None) or {}
                state_dict = getattr(ev, 'state_dict', None) or {}
                # 取两者并集：session_data 在【开播时】就建好，state_dict 要等【第一个分段录完】才建。
                # 只遍历 state_dict 的话，开播后到切出首段之前这里会是空的（分段多长就空多久）。
                group_ids = list(dict.fromkeys(list(session_data.keys()) + list(state_dict.keys())))
                for group_id in group_ids:
                    state_list = state_dict.get(group_id) or []
                    sd = humanize_dt(encode_dt(session_data.get(group_id) or {}))
                    rows = []
                    for i, seg in enumerate(list(state_list)):
                        row = {'seg': i + 1}
                        for vt, slot in (seg or {}).items():
                            f = slot.get('file')
                            p = getattr(f, 'path', None) if f is not None else None
                            row[vt] = {
                                'status': slot.get('status'),
                                'file': os.path.basename(p) if p else None,
                            }
                        rows.append(row)
                    groups.append({
                        'group_id':    str(group_id),
                        'is_live_end': sd.get('is_live_end'),
                        'updated_at':  now_str,
                        'session':     sd,
                        'segments':    rows,
                    })
            return {'groups': groups}

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

        # ---- B站合集(season)工具：从原独立的 api.py(FastAPI) 迁入，纯文本返回，方便 iPhone 快捷指令调用 ----
        # 故意不加 @login_required：快捷指令直接请求即可。参数三种方式都支持：URL query、表单、JSON 请求体。
        def _param(key):
            """从 URL query / 表单 / JSON 请求体里取参数，统一转成去空白的字符串。"""
            v = request.values.get(key)
            if v is None:
                v = (request.get_json(silent=True) or {}).get(key)
            return str(v).strip() if v is not None else ''

        @app.route('/api/seasons', methods=['GET', 'POST'])
        def api_seasons():
            """列出某账号的全部合集。account 可走 query / 表单 / JSON。"""
            from DMR.utils.bili_season import list_seasons
            account = _param('account')
            if not account:
                return Response('缺少参数 account', mimetype='text/plain', status=400)
            try:
                seasons = list_seasons(account)
            except FileNotFoundError:
                return Response(f'找不到账号 {account} 的登录信息(.login_info/{account}.json)',
                                mimetype='text/plain', status=404)
            except Exception as e:
                return Response(f'获取合集失败: {e}', mimetype='text/plain', status=500)
            text = "\n".join(f"{s['id']} - {s['title']}" for s in seasons) or '该账号下没有合集。'
            return Response(text, mimetype='text/plain')

        @app.route('/api/synctitle', methods=['GET', 'POST'])
        def api_synctitle():
            """把合集内各分P标题同步成与稿件一致。account/season_id 可走 query / 表单 / JSON。"""
            from DMR.utils.bili_season import sync_section_episode_titles
            account = _param('account')
            season_id = _param('season_id')
            if not account or not season_id.isdigit():
                return Response('缺少参数 account 或 season_id(需为数字)', mimetype='text/plain', status=400)
            try:
                result = sync_section_episode_titles(account, int(season_id))
            except Exception as e:
                return Response(f'同步失败: {e}', mimetype='text/plain', status=500)
            return Response(_format_synctitle_result(result), mimetype='text/plain')

        return app

    def get_tasks_data(self):
        # Get Recording Tasks
        recording_tasks = []
        def _remaining_seconds(deadline):
            if deadline is None:
                return None
            try:
                return max(0, int(math.ceil(float(deadline) - time.monotonic())))
            except (TypeError, ValueError):
                return None

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
                        if seg_start:
                            duration = str(now - seg_start).split('.')[0]
                        if live_start:
                            live_duration = str(now - live_start).split('.')[0]

                    recording_tasks.append({
                        'name': taskname,
                        'platform': task.plat,
                        'url': task.url,
                        'status': status,
                        'duration': duration,
                        'live_duration': live_duration,
                        'next_check_remaining': _remaining_seconds(
                            getattr(task, 'next_check_deadline', None)),
                        'stop_wait_remaining': _remaining_seconds(
                            getattr(task, 'stop_confirm_deadline', None)),
                        'force_offline_time': getattr(task, 'force_offline_time', '') or '',
                        'output_dir': _abspath(getattr(task, 'output_dir', '')),
                    })
        recording_tasks.sort(key=lambda t: t['name'])   # 默认按任务名排序（前端可再按列重排）

        # ===== 队列改为 state_dict 派生：每个文件按「当前阶段」只出现一次（互斥）=====
        # 进行中(带进度%/倒计时)取自插件活动字典；已完成子集(已渲染/已合并/已上传)取自各任务
        # event_class.state_dict 的状态。组完成被回收后自然消失（符合预期）。
        def _row(taskname, f):
            p = getattr(f, 'path', '') or ''
            return {'task': taskname,
                    'file': os.path.join(os.path.basename(os.path.dirname(p)), os.path.basename(p)) if p else '',
                    'path': _abspath(p)}

        rendered_done, merged_done, uploaded_done, merging_active = [], [], [], []
        if self.engine:
            for taskname, info in getattr(self.engine, 'task_dict', {}).items():
                ev = getattr(info.get('class'), 'event_class', None)
                for segs in (getattr(ev, 'state_dict', None) or {}).values():
                    for vs in segs:
                        for vtype, slot in vs.items():
                            f = slot.get('file')
                            if not f or not getattr(f, 'path', None):
                                continue
                            st = slot.get('status')
                            if st == 'ready' and vtype != 'src_video_pre':          # 渲染产物(原始录制源不算)
                                rendered_done.append(_row(taskname, f))             # 已渲染(待合并/上传)
                            elif st == 'merged':
                                merged_done.append(_row(taskname, f))               # 已合并(产物待上传/清理)
                            elif st == 'merging':
                                merging_active.append(_row(taskname, f))            # 合并中
                            elif st == 'uploaded':
                                uploaded_done.append(_row(taskname, f))            # 已上传(待清理或终态)

        # 渲染中（插件活动字典，带渲染%）
        render_active = []
        if self.engine and 'render' in self.engine.plugin_dict:
            render = self.engine.plugin_dict['render']['class']
            if render:
                for _u, task in render.render_tasks.items():
                    vp = task.get('video').path if task.get('video') else ''
                    outp = task.get('output', '') or ''
                    render_active.append({
                        'task': _disp_task(task.get('source', '')),
                        'video': os.path.join(os.path.basename(os.path.dirname(vp)), os.path.basename(vp)) if vp else '',
                        'output': os.path.join(os.path.basename(os.path.dirname(outp)), os.path.basename(outp)) if outp else '',
                        'video_path': _abspath(vp), 'output_path': _abspath(outp),
                        'mode': task.get('mode', 'Unknown'),
                        'status': task.get('status', 'waiting'),
                        'progress': task.get('progress'),   # 渲染百分比（dmrender/emoji 回报；transcode 现在也有）
                    })

        # 上传中（插件活动字典，带上传%）
        upload_active = []
        if self.engine and 'uploader' in self.engine.plugin_dict:
            uploader = self.engine.plugin_dict['uploader']['class']
            if uploader:
                for u, task in uploader.upload_tasks.items():
                    upload_active.append({
                        'uuid': u, 'task': _disp_task(task.get('source', '')),
                        # 带完整 path，前端用 pathCell 渲染成可点击打开（与其它队列统一）
                        'files': [{'path': _abspath(f.path)} for f in task.get('files', [])],
                        'account': task.get('args', {}).get('account', 'Unknown'),
                        'engine': task.get('engine', 'Unknown'),
                        'status': task.get('status', 'waiting'),
                        'progress': task.get('progress'),
                    })

        # Get Pending Cleanup Tasks
        pending_clean_list = []
        if self.engine and 'cleaner' in self.engine.plugin_dict:
            cleaner = self.engine.plugin_dict['cleaner']['class']
            for entry in list(cleaner._read_pending()):
                pending_clean_list.append({
                    'method'    : entry.get('method', ''),
                    # 发完整路径，由前端 pathCell 切「文件夹\文件名」并拼可打开的链接（与渲染任务一致）
                    'files'     : list(entry.get('files', [])),
                    'note'      : entry.get('note', ''),
                    'execute_at': entry.get('created_at', 0) + entry.get('delay', 0),
                })

        # Held tasks（需人工处理：某步重试用尽后被挂起的失败任务）
        held_tasks_list = []
        if self.engine:
            for taskname, info in getattr(self.engine, 'task_dict', {}).items():
                rt = info.get('class')
                ev = getattr(rt, 'event_class', None)
                for h in (getattr(ev, 'held_tasks', {}) or {}).values():
                    t = h.get('time')
                    held_tasks_list.append({
                        'task'   : taskname,
                        'hold_id': h.get('hold_id'),
                        'stage'  : h.get('stage'),
                        'info'   : h.get('info', {}),
                        'time'   : t.strftime('%Y-%m-%d %H:%M:%S') if hasattr(t, 'strftime') else '',
                    })

        return {
            'recording_tasks'   : recording_tasks,
            # 四个阶段队列：每个含 active(进行中,带%/倒计时) + done(已完成子集)
            'render_queue'      : {'active': render_active, 'done': rendered_done},
            'merge_queue'       : {'active': merging_active, 'done': merged_done},
            'upload_queue'      : {'active': upload_active, 'done': uploaded_done},
            'clean_queue'       : pending_clean_list,
            'held_tasks'        : held_tasks_list,
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
        n = {'config': 0, 'failed': 0, 'logs_err': 0, 'restart_pending': False}
        # 是否已设置「空闲后重启」：供前端把重启按钮恢复成绿色「已设置」态（刷新后也保持）
        n['restart_pending'] = bool(getattr(self.engine, '_restart_pending', False))
        dmr = getattr(self.engine, 'dmr', None)
        if dmr is not None:
            try:
                n['config'] = len(dmr.config.get_pending_changes())   # 配置增删改红点
            except Exception:
                pass
        try:
            # 待办计数 = 各任务"需人工处理"的挂起任务数（失败重试用尽后转人工）
            for info in (getattr(self.engine, 'task_dict', {}).values() if self.engine else []):
                ev = getattr(info.get('class'), 'event_class', None)
                n['failed'] += len(getattr(ev, 'held_tasks', {}) or {})
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
        # threaded=True：SSE 实时日志会常开一条连接，必须多线程，否则单连接会阻塞其它请求
        self.webapp.run(host=self.host, port=self.port, debug=False, use_reloader=False, threaded=True)

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
