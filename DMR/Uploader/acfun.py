# -*- coding: utf-8 -*-
import json
import logging
import os
import requests
from math import ceil
from pathlib import Path

from DMR.utils import replace_keywords

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AcFun 核心上传逻辑
# ---------------------------------------------------------------------------

class _AcFunClient:
    def __init__(self):
        self.stoped = False
        self.show_progress = False
        self._username = ''
        self._password = ''
        self._cookie_file = ''
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Origin': 'https://member.acfun.cn',
            'Referer': 'https://member.acfun.cn/video/upload',
            'X-Requested-With': 'XMLHttpRequest',
        })
        self.LOGIN_URL    = 'https://id.app.acfun.cn/rest/web/login/signin'
        self.TOKEN_URL    = 'https://member.acfun.cn/video/api/getKSCloudToken'
        self.FRAGMENT_URL = 'https://mediacloud.kuaishou.com/api/upload/fragment'
        self.COMPLETE_URL = 'https://mediacloud.kuaishou.com/api/upload/complete'
        self.FINISH_URL   = 'https://member.acfun.cn/video/api/uploadFinish'
        self.C_VIDEO_URL  = 'https://member.acfun.cn/video/api/createVideo'
        self.C_DOUGA_URL  = 'https://member.acfun.cn/video/api/createDouga'
        self.QINIU_URL    = 'https://member.acfun.cn/common/api/getQiniuToken'
        self.GET_URL_AFTER= 'https://member.acfun.cn/common/api/getUrlAfterUpload'

    def login(self, username: str, password: str, cookie_file: str = ''):
        self._username = username
        self._password = password
        self._cookie_file = cookie_file

        if cookie_file and self._load_cookies(cookie_file) and self._validate_session():
            logger.info('AcFun 使用已保存的 cookie 登录')
            return

        self._do_login()

    def _load_cookies(self, cookie_file: str) -> bool:
        if not os.path.exists(cookie_file):
            return False
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            self.session.cookies.update(cookies)
            return True
        except Exception as e:
            logger.warning(f'加载 cookie 失败: {e}')
            return False

    def _save_cookies(self):
        if not self._cookie_file:
            return
        try:
            os.makedirs(os.path.dirname(self._cookie_file) or '.', exist_ok=True)
            with open(self._cookie_file, 'w', encoding='utf-8') as f:
                json.dump(dict(self.session.cookies), f, ensure_ascii=False, indent=2)
            logger.debug(f'cookie 已保存到 {self._cookie_file}')
        except Exception as e:
            logger.warning(f'保存 cookie 失败: {e}')

    def _validate_session(self) -> bool:
        try:
            r = self.session.post(
                url=self.TOKEN_URL,
                data={'fileName': 'test', 'size': 1, 'template': '1'},
                timeout=10,
            )
            return r.status_code == 200 and 'taskId' in r.json()
        except Exception:
            return False

    def _do_login(self):
        r = self.session.post(
            url=self.LOGIN_URL,
            data={'username': self._username, 'password': self._password, 'key': '', 'captcha': ''},
        )
        resp = r.json()
        if resp.get('result') == 0:
            logger.info('AcFun 登录成功')
            self._save_cookies()
        else:
            raise RuntimeError(f'AcFun 登录失败: {r.text}')

    def get_token(self, filename: str, filesize: int) -> tuple:
        r = self.session.post(
            url=self.TOKEN_URL,
            data={'fileName': filename, 'size': filesize, 'template': '1'},
        )
        resp = r.json()
        if 'taskId' not in resp:
            raise RuntimeError(f'getKSCloudToken 失败: {r.text}')
        return resp['taskId'], resp['token'], resp['uploadConfig']['partSize']

    def upload_chunk(self, block: bytes, fragment_id: int, upload_token: str, pbar=None, max_retries: int = 5):
        if self.stoped:
            raise InterruptedError('上传已停止')
        for attempt in range(max_retries):
            try:
                r = requests.post(
                    url=self.FRAGMENT_URL,
                    params={'fragment_id': fragment_id, 'upload_token': upload_token},
                    data=block,
                    timeout=120,
                )
                if r.json()['result'] == 1:
                    if pbar:
                        pbar.update(1)
                    return
                else:
                    logger.warning(f'分块 {fragment_id + 1} 上传失败，第 {attempt + 1} 次重试: {r.text}')
            except requests.exceptions.RequestException as e:
                logger.warning(f'分块 {fragment_id + 1} 网络错误，第 {attempt + 1} 次重试: {e}')
                if attempt == max_retries - 1:
                    raise
        raise RuntimeError(f'分块 {fragment_id + 1} 上传失败，已重试 {max_retries} 次')

    def complete(self, fragment_count: int, upload_token: str):
        r = requests.post(
            url=self.COMPLETE_URL,
            params={'fragment_count': fragment_count, 'upload_token': upload_token},
        )
        if r.json().get('result') != 1:
            raise RuntimeError(f'complete 失败: {r.text}')

    def upload_finish(self, task_id: int):
        for attempt in range(2):
            r = self.session.post(url=self.FINISH_URL, data={'taskId': task_id})
            if r.status_code == 401 and attempt == 0:
                logger.warning('uploadFinish 返回 401，尝试重新登录...')
                self._do_login()
                continue
            if r.json().get('result') != 0:
                raise RuntimeError(f'uploadFinish 失败: {r.text}')
            return

    def create_video(self, video_key: int, filename: str) -> int:
        for attempt in range(2):
            r = self.session.post(
                url=self.C_VIDEO_URL,
                data={'videoKey': video_key, 'fileName': filename, 'vodType': 'ksCloud'},
            )
            if r.status_code == 401 and attempt == 0:
                logger.warning('createVideo 返回 401，session 可能已过期，尝试重新登录...')
                self._do_login()
                continue
            resp = r.json()
            if resp.get('result') != 0:
                raise RuntimeError(f'createVideo 失败: {r.text}')
            self.upload_finish(video_key)
            return resp['videoId']

    def upload_cover(self, image_path: str) -> str:
        """上传封面图片，返回封面 URL。image_path 为空时返回空字符串。"""
        if not image_path:
            return ''

        fname = os.path.basename(image_path)
        r = self.session.post(self.QINIU_URL, json={'fileName': fname})
        r.raise_for_status()
        info = r.json()['info']
        upload_token = info['token']
        host = info['httpEndpointList'][0]
        base = f'https://{host}/api/upload'

        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Cache-Control': 'no-cache',
            'Origin': 'https://member.acfun.cn',
            'Referer': 'https://member.acfun.cn/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        }

        requests.get(f'{base}/resume', params={'upload_token': upload_token}).raise_for_status()

        with open(image_path, 'rb') as f:
            image_data = f.read()

        requests.post(
            f'{base}/fragment',
            params={'upload_token': upload_token, 'fragment_id': 0},
            data=image_data,
            headers=headers,
        ).raise_for_status()

        requests.post(
            f'{base}/complete',
            params={'fragment_count': 1, 'upload_token': upload_token},
            headers=headers,
        ).raise_for_status()

        ra = self.session.post(
            self.GET_URL_AFTER,
            data={'bizFlag': 'web-douga-cover', 'token': upload_token, 'fileName': fname},
        )
        ra.raise_for_status()
        j = ra.json()
        if j.get('result') != 0 or 'url' not in j:
            raise RuntimeError(f'getUrlAfterUpload 失败: {j}')
        return j['url']

    def create_douga(self, file_paths, title: str, channel_id: int,
                     cover: str = '', desc: str = '', dynamic: str = '',
                     tags: list = None, creation_type: int = 1,
                     original_link_url: str = '', original_declare=None,
                     watermark_position: int = 3, watermark_signature: bool = False) -> str:
        if tags is None:
            tags = []
        if isinstance(file_paths, str):
            file_paths = [file_paths]

        video_infos = []
        for idx, file_path in enumerate(file_paths):
            if self.stoped:
                raise InterruptedError('上传已停止')
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            task_id, token, part_size = self.get_token(file_name, file_size)
            fragment_count = ceil(file_size / part_size)

            logger.info(f'[P{idx+1}] 开始上传: {file_name} ({file_size / 1024**3:.2f} GB, {fragment_count} 块)')
            if self.show_progress and tqdm:
                pbar = tqdm(total=fragment_count, desc=f'[P{idx+1}] {file_name}', unit='块', ncols=80)
            else:
                pbar = None
            try:
                with open(file_path, 'rb') as f:
                    for fragment_id in range(fragment_count):
                        chunk_data = f.read(part_size)
                        if not chunk_data:
                            break
                        self.upload_chunk(chunk_data, fragment_id, token, pbar)
            finally:
                if pbar:
                    pbar.close()

            self.complete(fragment_count, token)
            video_id = self.create_video(task_id, file_name)
            video_infos.append({'videoId': video_id, 'title': Path(file_path).stem})
            logger.info(f'[P{idx+1}] 上传完成，videoId={video_id}')

        cover_url = self.upload_cover(cover)

        data = {
            'title': title,
            'description': desc,
            'fansOnlyDesc': dynamic,
            'tagNames': json.dumps(tags),
            'creationType': creation_type,
            'channelId': channel_id,
            'coverUrl': cover_url,
            'videoInfos': json.dumps(video_infos),
            'isJoinUpCollege': '0',
            'isSyncKs': 'false',
            'watermarkSetting': json.dumps({'position': watermark_position, 'withSignature': watermark_signature}),
        }
        if creation_type == 1:
            data['originalLinkUrl'] = original_link_url
            data['originalDeclare'] = str(original_declare) if original_declare is not None else '0'
        else:
            data['originalDeclare'] = str(original_declare) if original_declare is not None else '1'

        for attempt in range(2):
            r = self.session.post(url=self.C_DOUGA_URL, data=data)
            if r.status_code == 401 and attempt == 0:
                logger.warning('createDouga 返回 401，尝试重新登录...')
                self._do_login()
                continue
            try:
                resp = r.json()
            except Exception:
                raise RuntimeError(f'createDouga 响应非 JSON: {r.text}')
            if resp.get('result') == 0:
                ac_id = resp['dougaId']
                logger.info(f'投稿成功！AC号: {ac_id}，共 {len(video_infos)} P')
                return ac_id
            else:
                raise RuntimeError(f'createDouga 失败: {r.text}')


# ---------------------------------------------------------------------------
# DMR Uploader 接口
# ---------------------------------------------------------------------------

class acfun:
    def __init__(self,
                 account: str = None,
                 login_info: str = None,
                 **kwargs):
        if not account:
            raise ValueError('account 必须设置')

        self.account = account
        self.logger = logging.getLogger(__name__)
        self.stoped = False
        self._client = _AcFunClient()
        self._client.show_progress = bool(kwargs.get('show_progress', False))

        cookie_file = f'.login_info/{account}.json'

        if not self._client._load_cookies(cookie_file) or not self._client._validate_session():
            raise RuntimeError(
                f'Cookie 无效或已过期，请重新运行 acfun_login.py --account {account}\n'
                f'  cookie 文件: {cookie_file}'
            )
        self._client._cookie_file = cookie_file
        self.logger.info(f'AcFun 使用已保存的 cookie 登录 ({account})')

    def format_config(self, config: dict, video_info=None) -> dict:
        config = config.copy()
        if config.get('title') and video_info:
            config['title'] = replace_keywords(config['title'], video_info, replace_invalid=True)
        if config.get('desc') and video_info:
            config['desc'] = replace_keywords(config['desc'], video_info)
        if config.get('dynamic') and video_info:
            config['dynamic'] = replace_keywords(config['dynamic'], video_info)
        return config

    def upload(self, files: list, **kwargs) -> tuple:
        if not isinstance(files, list):
            files = [files]

        config        = self.format_config(kwargs, files[0] if files else None)
        file_paths    = [f.path for f in files]
        title         = config.get('title') or Path(file_paths[0]).stem
        channel_id    = int(config.get('channel_id', 218))
        creation_type = int(config.get('creation_type', 3))
        desc          = config.get('desc', '')
        tags          = config.get('tag', [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        dynamic = config.get('dynamic', '')
        copyright = config.get('copyright', None)
        original_declare = int(copyright) if copyright is not None else None
        watermark_position  = int(config.get('watermark_position', 3))
        watermark_signature = bool(config.get('watermark_signature', False))
        cover         = config.get('cover', '')

        if not cover:
            try:
                cover = extract_best_frame(
                    file_paths[0],
                    output_dir=str(Path(file_paths[0]).parent),
                )
                self.logger.info(f'封面已自动提取: {cover}')
            except Exception as e:
                self.logger.warning(f'封面自动提取失败，将不设封面: {e}')
                cover = ''

        try:
            ac_id = self._client.create_douga(
                file_paths=file_paths,
                title=title,
                channel_id=channel_id,
                cover=cover,
                desc=desc,
                dynamic=dynamic,
                tags=tags,
                creation_type=creation_type,
                original_declare=original_declare,
                watermark_position=watermark_position,
                watermark_signature=watermark_signature,
            )
            return True, ac_id
        except InterruptedError:
            return False, '上传被中止'
        except Exception as e:
            self.logger.exception(e)
            return False, e

    def stop(self):
        self.stoped = True
        self._client.stoped = True

    def __del__(self):
        self.stop()
