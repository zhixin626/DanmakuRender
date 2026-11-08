import asyncio
import base64
import concurrent.futures
import hashlib
from io import BytesIO
import json
import logging
import math
import os
import re
import sys
import threading
import time
import copy
import yaml
from dataclasses import asdict, dataclass, field, InitVar
from json import JSONDecodeError
from os.path import splitext, basename
from typing import Callable, Dict, Union, Any, List
from urllib import parse

# import aiohttp
from DMR.utils import VideoInfo, replace_keywords
from DMR.utils.cover import prepare_cover
from concurrent.futures.thread import ThreadPoolExecutor
import concurrent
import requests.utils
import rsa
from requests.adapters import HTTPAdapter, Retry

from .biliuprs import biliuprs
from .bvid_history import read_period_bvid, write_period_bvid


logger = logging.getLogger(__name__)
_METADATA_LOCK = threading.RLock()


class _UploadEndpointPool:
    """为同一个 UPOS 上传会话管理可用的上传地址。"""

    def __init__(self, endpoints, upos_uri):
        object_path = upos_uri.replace('upos://', '').lstrip('/')
        unique_endpoints = list(dict.fromkeys(filter(None, endpoints)))
        self._urls = [f"https:{endpoint.rstrip('/')}/{object_path}" for endpoint in unique_endpoints]
        self._disabled = set()
        self._lock = threading.Lock()

    def current(self):
        with self._lock:
            return next((url for url in self._urls if url not in self._disabled), None)

    def disable(self, url):
        with self._lock:
            self._disabled.add(url)
            return next((candidate for candidate in self._urls if candidate not in self._disabled), None)


class _MetadataDumper(yaml.SafeDumper):
    pass


def _represent_metadata_string(dumper, value):
    style = '|' if '\n' in value else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', value, style=style)


_MetadataDumper.add_representer(str, _represent_metadata_string)


class BiliWebApi:
    def __init__(self,
        cookies:str=None,
        account:str=None,
        limit=3,
        sort_videos:bool=False,
        base_bvid="",
        **kwargs,
    ):
        self.cookies = cookies
        self.account = account
        self.limit = limit
        self.sort_videos = sort_videos

        self.app_key = 'ae57252b0c09105d'
        self.appsec = 'c75875c596a69eb55bd119e74b07cfe3'
        self._session = requests.Session()
        self._session.mount('https://', HTTPAdapter(max_retries=Retry(total=5)))
        self._session.headers.update({
            'user-agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/63.0.3239.108",
            'referer': "https://www.bilibili.com/",
            'connection': 'keep-alive'
        })
        self._auto_os = None
        login_info = self.login_by_biliuprs()
        self.cookies = {}
        for item in login_info['cookie_info']['cookies']:
            self.cookies[item['name']] = item['value']
        self.__bili_jct = self.cookies['bili_jct']
        self._session.cookies = requests.utils.cookiejar_from_dict(self.cookies)
        self.access_token = login_info['token_info']['access_token']
        self.refresh_token = login_info['token_info']['refresh_token']

        self.videos = Data(bvid=base_bvid) if base_bvid else None
        self.stoped = False


    def login_by_biliuprs(self):
        biliup_rs = biliuprs(cookies=self.cookies, account=self.account)
        with open(biliup_rs.cookies, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        return cookies

    @staticmethod
    def _apply_publish_options(videos, options):
        """远端稿件刷新后重新应用本次任务的投稿级开关。"""
        if not options:
            return videos
        if 'watermark' in options:
            videos.watermark = {'state': options['watermark']}
        if 'space_hidden' in options:
            videos.space_hidden = options['space_hidden']
        return videos

    @staticmethod
    def _append_remote_desc(videos, append_desc, limit=2000):
        """把本场简介追加到刚刷新的远端简介；超限时保留原简介。"""
        if not append_desc:
            return False

        remote_desc = str(videos.desc or '').replace('\r\n', '\n').replace('\r', '\n').rstrip()
        current_desc = str(append_desc).replace('\r\n', '\n').replace('\r', '\n').strip()
        if not current_desc:
            return False

        if remote_desc == current_desc or remote_desc.endswith(f'\n{current_desc}'):
            logger.info('远端简介已包含本次 append_desc，跳过重复追加')
            return False

        combined_desc = f'{remote_desc}\n{current_desc}' if remote_desc else current_desc
        if len(combined_desc) > limit:
            logger.warning(
                '简介追加后超过%d字符：远端desc=%d，本次append_desc=%d，合并后=%d；'
                '放弃追加简介，视频分P仍正常追加',
                limit, len(remote_desc), len(current_desc), len(combined_desc),
            )
            return False

        videos.desc = combined_desc
        videos.desc_v2 = [{
            'raw_text': combined_desc,
            'biz_id': '',
            'type': 1,
        }]
        logger.info(
            '已追加简介：远端desc=%d，本次append_desc=%d，合并后=%d',
            len(remote_desc), len(current_desc), len(combined_desc),
        )
        return True

    @staticmethod
    def _part_numbers(existing_count, upload_count):
        """返回本次上传任务覆盖的分P，例如 p2,p3。"""
        start = existing_count + 1
        return ','.join(f'p{number}' for number in range(start, start + upload_count))

    def sign(self, param):
        return hashlib.md5(f"{param}{self.appsec}".encode()).hexdigest()

    def get_key(self):
        url = "https://passport.bilibili.com/x/passport-login/web/key"
        payload = {
            'appkey': f'{self.app_key}',
            'sign': self.sign(f"appkey={self.app_key}"),
        }
        response = self._session.get(url, data=payload, timeout=5)
        r = response.json()
        if r and r["code"] == 0:
            return r['data']['hash'], rsa.PublicKey.load_pkcs1_openssl_pem(r['data']['key'].encode())

    def probe(self):
        ret = self._session.get('https://member.bilibili.com/preupload?r=probe', timeout=5).json()
        logger.debug(f"线路:{ret['lines']}")
        method = 'get' if ret['probe'].get('get') else 'post'
        data = None if method == 'get' else bytes(int(1024 * 0.1 * 1024))
        best = None

        for line in ret['lines']:
            try:
                start = time.perf_counter()
                response = self._session.request(
                    method, f"https:{line['probe_url']}", data=data, timeout=30,
                )
                cost = time.perf_counter() - start
            except requests.RequestException as e:
                logger.warning("上传线路测速失败，跳过 %s: %s", line['probe_url'], e)
                continue

            if response.status_code != 200:
                logger.warning(
                    "上传线路测速返回 %s，跳过 %s",
                    response.status_code, line['probe_url'],
                )
                continue
            if best is None or cost < best['cost']:
                best = {**line, 'cost': cost}

        if best is None:
            raise RuntimeError('没有可用的B站上传线路')
        return best
    
    def creditsToDesc_v2(self, desc:str, credits:List[Dict[str, Union[str, int]]]):
        desc_v2 = []
        desc_v2_tmp = desc
        for credit in credits:
            try:
                num = desc_v2_tmp.index("@credit")
                desc_v2.append({
                    "raw_text": " " + desc_v2_tmp[:num],
                    "biz_id": "",
                    "type": 1
                })
                desc_v2.append({
                    "raw_text": credit["username"],
                    "biz_id": str(credit["uid"]),
                    "type": 2
                })
                desc = desc.replace(
                    "@credit", "@" + credit["username"] + "  ", 1)
                desc_v2_tmp = desc_v2_tmp[num + 7:]
            except IndexError:
                logger.error('简介中的@credit占位符少于credits的数量,替换失败')
        desc_v2.append({
            "raw_text": " " + desc_v2_tmp,
            "biz_id": "",
            "type": 1
        })
        desc_v2[0]["raw_text"] = desc_v2[0]["raw_text"][1:]  # 开头空格会导致识别简介过长
        return desc, desc_v2

    def videoinfo_to_videos(self, video_info, config, session: dict=None):
        # 合并会话级"格式化标本"（stime/etime/gifts_revenue/.../top_ranking），供 replace_keywords 解析
        video_info = {**video_info, **(session or {})}
        video = Data(
            tid=config.get('tid', 21),
            copyright=config.get('copyright', 1),
            dolby=config.get('dolby', 0),
            hires=config.get('hires', 0),
            no_reprint=config.get('no_reprint', 0),
            is_only_self=config.get('is_only_self', 0),
            charging_pay=config.get('charging_pay', 0),
            open_subtitle=config.get('open_subtitle', False),
            no_disturbance=config.get('no_disturbance', 0),
            watermark={'state': config.get('watermark', 0)},
            space_hidden=config.get('space_hidden', 0),
            extra_kwargs=config.get('extra_kwargs', {}),
        )
        if config.get('dtime') and config['dtime'] >= 14400:
            video.delay_time(int(time.time() + config['dtime']))
        if config.get('title'):
            video.title = replace_keywords(config['title'], video_info)
            if len(video.title) > 80:
                video.title = video.title[:80]
                logger.warning(f'视频标题超过80字符，已自动截取为: {video.title}.')
        if config.get('desc'):
            video.desc = replace_keywords(config['desc'], video_info)
        if config.get('credits'):
            video.desc, video.desc_v2 = self.creditsToDesc_v2(video.desc, config['credits'])
        else:
            video.desc_v2 = [{
                "raw_text": video.desc,
                "biz_id": "",
                "type": 1
            }]
        if config.get('dynamic'):
            video.dynamic = replace_keywords(config['dynamic'], video_info)
        if config.get('tag'):
            if isinstance(config['tag'], list):
                video.tag = ','.join(config['tag'])
            else:
                video.tag = config['tag']
            video.tag = replace_keywords(video.tag, video_info)
        if config.get('source'):
            video.source = replace_keywords(config['source'], video_info)
        # 先只准备本地封面；实际上传由 upload() 在确认不是 skip_upload 后执行。
        cover_file, generated = prepare_cover(
            video_info.get('path'),
            config.get('cover'),
            stime=video_info.get('stime'),
            account=config.get('account', ''),
            context=video_info,
        )
        # 只记录本次动态生成的封面；cover.path 指向的静态图片不归我管。
        # 上传成功后删除，失败则把它留给 WebUI 人工处理用。
        if generated and cover_file:
            cover_file = self._rename_generated_cover(cover_file, video_info, config.get('account'))
        self._gen_cover = cover_file if generated else None
        self._cover_file = cover_file
        return video

    @staticmethod
    def _safe_filename_part(value):
        return re.sub(r'[\\/:*?"<>|]', '_', str(value or 'unknown'))

    def _record_key(self, video_info):
        file_id = video_info.get('file_id') or video_info.get('dm_video_id')
        if not file_id:
            file_id = hashlib.sha1(os.path.abspath(video_info['path']).encode('utf-8')).hexdigest()[:16]
        return str(file_id), f'{file_id}:{self.account or "unknown"}'

    def _rename_generated_cover(self, cover_file, video_info, account):
        """Rename generated covers so archived covers are unique and repeatable."""
        if not isinstance(cover_file, str) or not os.path.isfile(cover_file):
            return cover_file
        file_id, _ = self._record_key(video_info)
        token = hashlib.sha1(f'{file_id}|{account}'.encode('utf-8')).hexdigest()[:8]
        video_stem = self._safe_filename_part(splitext(basename(video_info['path']))[0])
        account_part = self._safe_filename_part(account)
        ext = splitext(cover_file)[1] or '.jpg'
        destination = os.path.join(
            os.path.dirname(os.path.abspath(video_info['path'])),
            f'{video_stem}__{account_part}__{token}{ext}',
        )
        if os.path.abspath(cover_file) != os.path.abspath(destination):
            os.replace(cover_file, destination)
        return destination

    @staticmethod
    def _cover_reference(cover_file, video_path):
        if not isinstance(cover_file, str):
            return ''
        if os.path.dirname(os.path.abspath(cover_file)) == os.path.dirname(os.path.abspath(video_path)):
            return basename(cover_file)
        return cover_file

    def _record_metadata(self, video_info, video, cover_file=None):
        with _METADATA_LOCK:
            self._record_metadata_locked(video_info, video, cover_file)

    def _record_metadata_locked(self, video_info, video, cover_file=None):
        """Upsert formatted publish data into metadata.yml beside the final video."""
        output_dir = os.path.dirname(os.path.abspath(video_info['path']))
        metadata_path = os.path.join(output_dir, 'metadata.yml')
        try:
            if os.path.exists(metadata_path):
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = yaml.safe_load(f) or {}
            else:
                metadata = {}
        except (OSError, yaml.YAMLError) as e:
            logger.warning(f'读取投稿资料文件失败，将重新创建: {metadata_path}: {e}')
            metadata = {}

        # 兼容此前生成的 version/records 结构：下次写入时自动转换为
        # ``视频文件名 -> 账号 -> 投稿资料``，不保留内部 file_id。
        legacy_records = metadata.pop('records', None)
        metadata.pop('version', None)
        if isinstance(legacy_records, dict):
            for legacy in legacy_records.values():
                if not isinstance(legacy, dict):
                    continue
                legacy_video = legacy.get('video')
                legacy_account = legacy.get('account')
                if not legacy_video or not legacy_account:
                    continue
                legacy_tag = legacy.get('tag', '')
                if isinstance(legacy_tag, list):
                    legacy_tag = ','.join(str(tag) for tag in legacy_tag)
                metadata.setdefault(legacy_video, {})[legacy_account] = {
                    'cover': legacy.get('cover', ''),
                    'title': legacy.get('title', ''),
                    'dynamic': str(legacy.get('dynamic', '')).rstrip('\r\n'),
                    'tag': str(legacy_tag),
                    'desc': str(legacy.get('desc', '')).rstrip('\r\n'),
                }

        video_name = basename(video_info['path'])
        account = self.account or ''
        account_records = metadata.get(video_name)
        if not isinstance(account_records, dict):
            account_records = {}
            metadata[video_name] = account_records
        if cover_file is None:
            cover_file = self._cover_file
        account_records[account] = {
            'cover': self._cover_reference(cover_file, video_info['path']),
            'title': video.title,
            'dynamic': str(video.dynamic or '').rstrip('\r\n'),
            'tag': str(video.tag or ''),
            'desc': str(video.desc or '').rstrip('\r\n'),
        }
        with open(metadata_path, 'w', encoding='utf-8', newline='\n') as f:
            yaml.dump(metadata, f, Dumper=_MetadataDumper, allow_unicode=True, sort_keys=False)
        logger.info(f'已记录投稿资料: {metadata_path} ({video_name}/{account})')

    def _upload_pending_cover(self, video, config):
        """Upload the locally prepared cover; cover failures do not block submission."""
        cover_file = self._cover_file
        if not cover_file:
            return
        try:
            if isinstance(cover_file, str) and cover_file.startswith('http'):
                resp = requests.get(cover_file, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10.0)
                resp.raise_for_status()
                cover_file = BytesIO(resp.content)
            video.cover = self.cover_up(cover_file)
        except Exception as e:
            logger.error(f'视频 {config.get("title", "")} 封面设置失败: {e}, 跳过设置.')
            video.cover = ''

    def upload(
        self,
        files:list[VideoInfo],
        session: dict=None,
        progress_cb=None,
        **kwargs,
    ):
        is_new = False
        videos = self.videos
        self._gen_cover = None
        self._cover_file = None
        n_files = len(files)
        if not n_files:
            return False, '没有需要上传的视频文件'
        if kwargs.get('skip_upload'):
            # 资料模式不读取远端稿件、不上传封面，也不提交视频。
            template_session = {**(session or {}), 'p_n': self._part_numbers(0, n_files)}
            archive_video = self.videoinfo_to_videos(files[0], kwargs, template_session)
            if kwargs.get('record_metadata'):
                self._record_metadata(files[0], archive_video)
            self.videos = None
            return True, '投稿资料已生成，已跳过上传'
        self._gen_cover = None   # 仅 is_new 时由 videoinfo_to_videos 写入；追加上传不生成新封面
        if not videos:
            # 周期追加：未显式指定稿件且开启 auto_append_period 时，查历史决定是否追加到已有稿件
            period = kwargs.get('auto_append_period')
            if period in ('monthly', 'daily') and kwargs.get('output_dir'):
                existing = read_period_bvid(kwargs['output_dir'], (session or {}).get('stime'),
                                            period=period, account=kwargs.get('account'))
                if existing:
                    videos = Data(bvid=existing)
                    logger.info(f"[账号{kwargs.get('account')}] 追加模式({period})：检测到已有稿件 {existing}，将追加")
                else:
                    logger.info(f"[账号{kwargs.get('account')}] 追加模式({period})：暂无稿件，将创建新稿件")
        if not videos:
            is_new = True
            existing_count = 0
        else:
            videos = self.get_remote_data(videos.bvid) # 刷新视频信息
            if not videos:
                raise RuntimeError('无法获取远端稿件信息，已停止本次上传')
            existing_count = len(videos.videos)

        part_numbers = self._part_numbers(existing_count, n_files)
        template_session = {**(session or {}), 'p_n': part_numbers}
        if is_new:
            videos = self.videoinfo_to_videos(files[0], kwargs, template_session)
        logger.info('本次上传任务对应分P：%s', part_numbers)
        if is_new:
            if kwargs.get('record_metadata'):
                # 先记录本地封面；投稿成功后会用平台封面地址更新此条记录。
                self._record_metadata(files[0], videos)
            self._upload_pending_cover(videos, kwargs)
        formatted_append_desc = None
        if (
            not is_new
            and kwargs.get('auto_append_period') in ('monthly', 'daily')
            and kwargs.get('append_desc')
        ):
            append_context = {**files[0], **template_session}
            formatted_append_desc = replace_keywords(str(kwargs['append_desc']), append_context)
        # return False, '手动测试上传失败', {'cover': getattr(self, '_gen_cover', None)}
        publish_options = {
            'watermark': kwargs.get('watermark', 0),
            'space_hidden': kwargs.get('space_hidden', 0),
        }
        for fi, file in enumerate(files, 1):
            if self.stoped:                     # 手动停止：立即中断，不再传后续文件
                raise RuntimeError('上传已被手动停止')
            # UPOS 回调成功上传字节数；这里换算成百分比并带上文件序号交给上层。
            file_cb = (lambda done, total, _i=fi:
                       progress_cb(int(done / total * 100) if total else 0, _i, n_files)) \
                      if progress_cb else None
            status, info = self.upload_file(
                filepath=file.path,
                lines=kwargs.get('line', 'AUTO'),
                videos=videos,
                submit_api='web',
                progress_cb=file_cb,
                publish_options=publish_options,
                # 一次上传任务只追加一段简介；最后一个文件提交时，前面的分P都已成功提交。
                append_desc=formatted_append_desc if fi == n_files else None,
                )
            if status:
                # upload_file 已经提交到远端；后续分P失败时也保留这个有效稿件状态。
                videos.bvid = info
                self.videos = videos
            else:
                break

        # self.submit(submit_api='web', videos=videos)
        # 合集：add_episodes(epid，原作者，仅加入) 与 add_to_list(season_id，加入+排到最前) 二选一，
        # 不要同时开启，否则同一稿件会被重复加入合集。
        if status and is_new and kwargs.get('epid'):
            self.add_episodes(kwargs['epid'], videos.bvid, videos.title)
        if status and is_new and kwargs.get('add_to_list') and kwargs.get('season_id'):
            self.add_to_list(kwargs['season_id'], videos.bvid, videos.title)
        # 周期追加：新建稿件成功后，把本周期 bvid 登记到历史，供下次同周期追加
        if status and is_new and kwargs.get('auto_append_period') in ('monthly', 'daily') and kwargs.get('output_dir'):
            write_period_bvid(kwargs['output_dir'], (session or {}).get('stime'),
                              videos.bvid, period=kwargs['auto_append_period'], account=kwargs.get('account'))

        if status and is_new and kwargs.get('record_metadata'):
            self._record_metadata(files[0], videos, cover_file=videos.cover)

        # 动态生成的封面：上传成功就删本地文件；失败则把路径随返回值带回（留给 WebUI 人工处理）
        gen_cover = getattr(self, '_gen_cover', None)
        if status:
            if gen_cover:
                try:
                    os.remove(gen_cover)
                except Exception:
                    pass
                self._gen_cover = None
            return status, info
        return status, info, {'cover': gen_cover}

    def add_episodes(self, epid, bvid, title):
        """原作者实现：把稿件加入合集（不排序）。与 add_to_list 二选一，勿同时开启。"""
        try:
            uri = f'https://member.bilibili.com/x2/creative/web/season?id={epid}'
            resp = self._session.get(uri, timeout=5).json()
            section_id = resp['data']['sections']['sections'][0]['id']
        except Exception as e:
            logger.error(f'获取合集信息失败: {e}')
            return False

        try:
            uri = f'https://member.bilibili.com/x2/creative/web/season/section/episodes/add?csrf={self.__bili_jct}'
            data = {
                "sectionId": section_id,
                "episodes":[{
                    "title": title,
                    "bvid": bvid,
                }]
            }
            resp = self._session.post(uri, json=data, timeout=5).json()
            if resp['code'] == 0:
                logger.info(f'添加合集成功: {bvid} -> {epid}')
                return True
            else:
                logger.error(f'添加{bvid}到合集失败: {resp}')
        except Exception as e:
            logger.error(f'添加{bvid}到合集失败: {e}')
        return False

    def add_to_list(self, season_id, bvid, title, reorder=True):
        """把刚上传的稿件加入合集(season)的第一个分区，可选地把它排到最前。
        全程用引擎自身登录态(self._session / csrf)，与 cover_up 一致，不另读 cookie 文件。"""
        try:
            uri = f'https://member.bilibili.com/x2/creative/web/season?id={season_id}'
            resp = self._session.get(uri, timeout=5).json()
            section_id = resp['data']['sections']['sections'][0]['id']
        except Exception as e:
            logger.error(f'获取合集信息失败: {e}')
            return False

        try:
            time.sleep(3)   # 给 B站一点时间让新稿件可被加入合集
            uri = f'https://member.bilibili.com/x2/creative/web/season/section/episodes/add?csrf={self.__bili_jct}'
            data = {
                "sectionId": section_id,
                "episodes":[{
                    "title": title,
                    "bvid": bvid,
                }]
            }
            resp = self._session.post(uri, json=data, timeout=5).json()
            if resp.get('code') != 0:
                logger.error(f'添加{bvid}到合集失败: {resp}')
                return False
            logger.info(f'添加合集成功: {bvid} -> {season_id}')
        except Exception as e:
            logger.error(f'添加{bvid}到合集失败: {e}')
            return False

        if reorder:
            self.reorder_section(section_id, mode='last_to_first')
        return True

    def reorder_section(self, section_id, mode='last_to_first'):
        """对合集分区做一次首尾互换排序（默认把最后加入的排到最前）。"""
        url_section = 'https://member.bilibili.com/x2/creative/web/season/section'
        url_sort = 'https://member.bilibili.com/x2/creative/web/season/section/edit'
        try:
            j = self._session.get(url_section, params={'id': section_id}, timeout=5).json()
            if j.get('code') != 0:
                logger.error(f'获取分区信息失败: {j}')
                return j
            data = j.get('data') or {}
            episodes = data.get('episodes') or []
            section = data.get('section') or {}
            if not episodes:
                logger.error('当前分区无可排序的视频（episodes 为空）')
                return {'code': -1, 'message': 'no episodes'}

            if mode == 'last_to_first':
                reordered = [episodes[-1]] + episodes[:-1]
            elif mode == 'first_to_last':
                reordered = episodes[1:] + [episodes[0]]
            else:
                raise ValueError(f'未知排序模式: {mode}')
            sorts = [{'id': e['id'], 'sort': i + 1} for i, e in enumerate(reordered)]

            payload = {
                'section': {
                    'id': section.get('id', section_id),
                    'seasonId': section.get('seasonId'),
                    'title': section.get('title', '正片'),
                    'type': section.get('type', 1),
                },
                'sorts': sorts,
            }
            rj = self._session.post(url_sort, params={'csrf': self.__bili_jct},
                                    json=payload, timeout=5).json()
            if rj.get('code') == 0:
                logger.info(f'成功调整合集分区顺序: {mode}')
            else:
                logger.error(f'调整合集顺序失败: {rj}')
            return rj
        except Exception as e:
            logger.error(f'调整合集顺序失败: {e}')
            return {'code': -1, 'message': str(e)}

    def cover_up(self, img: str):
        """
        :param img: img path or stream
        :return: img URL
        """
        from PIL import Image
        from io import BytesIO

        with Image.open(img) as im:
            # 宽和高,需要16：10
            xsize, ysize = im.size
            if xsize / ysize > 1.6:
                delta = xsize - ysize * 1.6
                region = im.crop((delta / 2, 0, xsize - delta / 2, ysize))
            else:
                delta = ysize - xsize * 10 / 16
                region = im.crop((0, delta / 2, xsize, ysize - delta / 2))
            buffered = BytesIO()
            region.save(buffered, format=im.format)
        r = self._session.post(
            url='https://member.bilibili.com/x/vu/web/cover/up',
            data={
                'cover': b'data:image/jpeg;base64,' + (base64.b64encode(buffered.getvalue())),
                'csrf': self.__bili_jct
            }, timeout=30
        )
        buffered.close()
        res = r.json()
        if res.get('data') is None:
            raise Exception(res)
        return res['data']['url']

    def upload_file(
        self,
        filepath:str,
        lines='AUTO',
        videos: 'Data'=None,
        submit_api: Callable[[str], None] = None,
        progress_cb=None,
        publish_options=None,
        append_desc=None,
    ):
        status, message = self._upload_path(
            filepath=filepath,
            file_name=os.path.basename(filepath),
            total_size=os.path.getsize(filepath),
            lines=lines,
            videos=videos,
            submit_api=submit_api,
            progress_cb=progress_cb,
            publish_options=publish_options,
            append_desc=append_desc,
        )
        return status, message

    def _upload_path(
        self,
        filepath: str,
        file_name,
        total_size,
        lines='AUTO',
        videos: 'Data'=None,
        submit_api: Callable[[str], None] = None,
        progress_cb=None,
        publish_options=None,
        append_desc=None,
    ):

        logger.info(f"{file_name} 开始上传")
        cs_upcdn = ['alia', 'bda', 'bda2', 'bldsa', 'qn', 'tx', 'txa']
        jd_upcdn = ['jd-alia', 'jd-bd', 'jd-bldsa', 'jd-tx', 'jd-txa']
        preferred_upos_cdn = None
        if not self._auto_os:
            if lines in cs_upcdn:
                self._auto_os = {"os": "upos", "query": f"upcdn={lines}&probe_version=20221109",
                                 "probe_url": f"//upos-cs-upcdn{lines}.bilivideo.com/OK"}
                preferred_upos_cdn = lines
            elif lines in jd_upcdn:
                lines = lines.split('-')[1]
                self._auto_os = {"os": "upos", "query": f"upcdn={lines}&probe_version=20221109",
                                 "probe_url": f"//upos-jd-upcdn{lines}.bilivideo.com/OK"}
                preferred_upos_cdn = lines
            else:
                self._auto_os = self.probe()
            logger.debug(f"线路选择 => {self._auto_os['os']}: {self._auto_os['query']}. time: {self._auto_os.get('cost')}")
        if self._auto_os['os'] == 'upos':
            upload = self._upos_upload
        else:
            logger.error(f"NoSearch:{self._auto_os['os']}")
            raise NotImplementedError(self._auto_os['os'])
        logger.debug(f"os: {self._auto_os['os']}")
        query = {
            'r': self._auto_os['os'],
            'profile': 'ugcupos/bup',
            'ssl': 0,
            'version': '2.8.12',
            'build': 2081200,
            'name': file_name,
            'size': total_size,
        }
        resp = self._session.get(
            f"https://member.bilibili.com/preupload?{self._auto_os['query']}", params=query,
            timeout=5)
        ret = resp.json()
        if "chunk_size" not in ret:
            # stop_event.set()
            return False, f"预上传失败: {ret}"
        logger.debug(f"preupload: {ret}")
        if preferred_upos_cdn:
            # 如果返回的endpoint不在probe_url中，则尝试在endpoints中校验probe_url是否可用
            if ret['endpoint'] not in self._auto_os['probe_url']:
                for endpoint in ret['endpoints']:
                    if endpoint in self._auto_os['probe_url']:
                        ret['endpoint'] = endpoint
                        logger.info(f"修改endpoint: {ret['endpoint']}")
                        break
                else:
                    logger.warning(f"选择的线路 {self._auto_os['os']} 没有返回对应 endpoint，不做修改")
        video_part = asyncio.run(upload(filepath, file_name, total_size, ret, progress_cb=progress_cb))
        if video_part is None:
            # stop_event.set()
            return False, '分P上传失败'
        video_part['title'] = video_part['title'][:80]

        if new_videos := self.get_remote_data(videos.bvid):
            videos = new_videos
        self._apply_publish_options(videos, publish_options)
        self._append_remote_desc(videos, append_desc)
        videos.append(video_part)  # 添加已经上传的视频

        ret = self.submit(submit_api=submit_api, videos=videos)
        # logger.info(f"上传成功: {ret}")
        bvid = ret['data']['bvid']
        videos.bvid = bvid
        return True, bvid

    async def _upos_upload(self, filepath, file_name, total_size, ret, progress_cb=None):
        # print("--------------, ", file_name)
        file_size = total_size
        chunk_size = ret['chunk_size']
        auth = ret["auth"]
        endpoint = ret["endpoint"]
        biz_id = ret["biz_id"]
        upos_uri = ret["upos_uri"]
        endpoint_pool = _UploadEndpointPool(
            [endpoint, *(ret.get('endpoints') or [])],
            upos_uri,
        )
        url = endpoint_pool.current()
        headers = {
            "X-Upos-Auth": auth
        }
        # 向上传地址申请上传，得到上传id等信息
        upload_id = self._session.post(f'{url}?uploads&output=json', timeout=15,
                                        headers=headers).json()["upload_id"]
        if progress_cb:
            progress_cb(0, file_size)
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量
        total_size = chunks * chunk_size  # 补齐总大小

        start = time.perf_counter()

        # print("-----------")
        # print(upload_id, chunks, chunk_size, total_size)

        # logger.info(
        #     f"{file_name} - upload_id: {upload_id}, chunks: {chunks}, chunk_size: {chunk_size}, total_size: {total_size}")
        
        
        chunk_generator = self.file_reader_generator(filepath, chunk_size)
        
        n = 0
        max_workers = self.limit
        semaphore = threading.Semaphore(max_workers)
        # 只累计确认上传成功的字节；失败、重试和 endpoint 切换都不会虚增进度。
        uploaded_bytes = [0]
        _plock = threading.Lock()
        def _chunk_done(fut):
            semaphore.release()
            if not progress_cb:
                return
            try:
                result = fut.result()
                if result is None:
                    return
                with _plock:
                    uploaded_bytes[0] += result['size']
                    done = min(uploaded_bytes[0], file_size * 99 // 100)
                progress_cb(done, file_size)
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            sessions = [copy.deepcopy(self._session) for _ in range(max_workers)]
            for index, chunk in enumerate(chunk_generator):
                if self.stoped:                     # 手动停止：不再提交新分块，立即中断
                    raise RuntimeError('上传已被手动停止')
                if not chunk:
                    break
                n += len(chunk)
                params = {
                    'uploadId': upload_id,
                    'chunks': chunks,
                    'total': total_size,
                    'chunk': index,
                    'size': chunk_size,
                    'partNumber': index + 1,
                    'start': index * chunk_size,
                    'end': index * chunk_size + chunk_size
                }
                params_clone = params.copy()
                semaphore.acquire()
                future = executor.submit(self.upload_chunk_thread, sessions[index % max_workers],
                                         endpoint_pool, chunk, params_clone, headers, file_name)
                future.add_done_callback(_chunk_done)
                futures[future] = index + 1

            # 必须检查每个分片的真实结果，缺少任意分片都不能请求合并。
            failed_parts = []
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result is None:
                    failed_parts.append(futures[future])
                else:
                    parts.append({
                        'partNumber': result['partNumber'],
                        'eTag': result['eTag'],
                    })

            if failed_parts:
                logger.error(
                    "%s 有 %d 个分块上传失败: %s",
                    file_name, len(failed_parts), sorted(failed_parts),
                )
                return None
            parts.sort(key=lambda part: part['partNumber'])

        if n == 0:
            return None
        # logger.info(f"{file_name} - total_size: {total_size}, n: {n}")
        cost = time.perf_counter() - start
        p = {
            'name': file_name,
            'uploadId': upload_id,
            'biz_id': biz_id,
            'output': 'json',
            'profile': 'ugcupos/bup'
        }
        attempt = 1
        while attempt <= 3:  # 一旦放弃就会丢失前面所有的进度，多试几次吧
            try:
                merge_url = endpoint_pool.current()
                if merge_url is None:
                    return None
                r = self._session.post(merge_url, params=p, json={"parts": parts}, headers=headers, timeout=15).json()
                if r.get('OK') == 1:
                    if progress_cb:
                        progress_cb(file_size, file_size)
                    # logger.info(f'{file_name} uploaded >> {n / 1000 / 1000:.2f}MB, {n / 1000 / 1000 / cost:.2f}MB/s. {r}')
                    return {"title": splitext(file_name)[0], "filename": splitext(basename(upos_uri))[0], "desc": ""}
                raise IOError(r)
            except IOError:
                logger.info(f"请求合并分片 {file_name} 时出现问题，尝试重连，次数：" + str(attempt))
                attempt += 1
                time.sleep(10)
        pass

    def upload_chunk_thread(self, session, endpoint_pool, chunk, params_clone, headers,
                            file_name, max_retries=5, backoff_factor=2):
        chunk_index = params_clone['chunk']

        while url := endpoint_pool.current():
            retries = 0
            while retries < max_retries:
                if self.stoped:                     # 手动停止：在途分块不再重试，直接退出
                    return None
                try:
                    r = session.put(
                        url=url,
                        params=params_clone,
                        data=chunk,
                        headers=headers,
                        timeout=20,
                    )
                    if r.status_code == 200:
                        return {
                            "partNumber": chunk_index + 1,
                            "eTag": "etag",
                            "size": len(chunk),
                        }

                    retries += 1
                    if retries < max_retries:
                        logger.warning(
                            "%s - chunks-%d - up failed: %s. Retrying %d/%d",
                            file_name, chunk_index, r.status_code, retries, max_retries,
                        )
                    else:
                        logger.error(
                            "%s - chunks-%d - endpoint %s 连续失败 %d 次",
                            file_name, chunk_index, url, max_retries,
                        )
                except requests.exceptions.SSLError as e:
                    # 证书错误属于 endpoint 级故障，在原地址继续重试没有意义。
                    logger.error("上传节点证书错误 %s: %s", url, e)
                    break
                except Exception as e:
                    retries += 1
                    if retries < max_retries:
                        logger.error(
                            "upload_chunk_thread err %s. Retrying %d/%d",
                            e, retries, max_retries,
                        )
                    else:
                        logger.error(
                            "%s - chunks-%d - endpoint %s 连续失败 %d 次: %s",
                            file_name, chunk_index, url, max_retries, e,
                        )

                if retries < max_retries:
                    time.sleep(backoff_factor ** retries)

            next_url = endpoint_pool.disable(url)
            if next_url:
                logger.warning(
                    "%s - chunks-%d - endpoint 失败，切换到 %s",
                    file_name, chunk_index, next_url,
                )
            else:
                logger.error(
                    "%s - chunks-%d - 所有候选 endpoint 均上传失败",
                    file_name, chunk_index,
                )
                return None

        return None

    def file_reader_generator(self, filepath: str, chunk_size: int):
        """
        从文件中读取数据并按 chunk_size 大小分块产出 (yield)
        :param filepath: 文件路径
        :param chunk_size: 消费者每次想要获取的数据块大小
        :return: 生成器，按 chunk_size 大小分批次产出数据
        """
        with open(filepath, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data
        yield None

    def get_remote_data(self, bvid):
        if not bvid:
            return None

        url = f'https://member.bilibili.com/x/vupre/web/archive/view?bvid={bvid}'
        try:
            r = self._session.get(url, timeout=5).json()
            if r['code'] == 0:
                # logger.info(f'get_remote_data {bvid} data: {r["data"]}')
                data = r['data']
                info = data['archive']
                video_data = Data(
                    bvid=info['bvid'],
                    copyright=info['copyright'],
                    source=info['source'],
                    tid=info['tid'],
                    cover=info['cover'],
                    title=info['title'],
                    desc=info['desc'],
                    # desc_v2=info['desc_v2'], # 这里会导致简介过长
                    dynamic=info['dynamic'],
                    tag=info['tag'],
                    dtime=info['dtime'],
                    open_subtitle=data['subtitle']['allow'],
                    dolby=info['attrs']['is_dolby'],
                    no_reprint=info['no_reprint'],
                    is_only_self=info['is_only_self'],
                    charging_pay=info['charging_pay'],
                    watermark=info.get('watermark') or {'state': 0},
                    space_hidden=info.get('space_hidden', 0),
                )
                for video in data['videos']:
                    video_data.append({
                        'title': video['title'],
                        'filename': video['filename'],
                        'cid': video['cid'],
                        'desc': '',
                    })
                return video_data
        except Exception as e:
            logger.error(f'获取远程稿件{bvid}数据失败: {e}')
        return None

    def submit(self, submit_api=None, videos:'Data'=None):
        edit = videos.bvid is not None
        if self.sort_videos:
            videos.videos.sort(key=lambda x: x['title'])
        # 不能提交 extra_kwargs 字段，提前处理
        post_data = asdict(videos)
        for key, value in (post_data.pop('extra_kwargs', None) or {}).items():
            post_data.setdefault(key, value)

        # self.__session.get('https://member.bilibili.com/x/geetest/pre/add', timeout=5)
        ret = self.submit_web(post_data, edit=edit)
        if not ret:
            raise Exception(f'不存在的选项：{submit_api}')
        if ret["code"] == 0:
            return ret
        elif ret["code"] == 10010:
            self.videos = None
            logger.warning('稿件被锁定或已经删除, 即将提交新稿件')
            raise
        else:
            logger.warning(f'submit failed {ret}: post_data={post_data}')
            raise Exception(ret)

    def submit_web(self, post_data, edit=False):
        # logger.info('使用网页端api提交')
        if not self.__bili_jct:
            raise RuntimeError("bili_jct is required!")
        api = 'https://member.bilibili.com/x/vu/web/add?csrf=' + self.__bili_jct
        if edit:
            api = 'https://member.bilibili.com/x/vu/web/edit?csrf=' + self.__bili_jct
        return self._session.post(api, timeout=5,
                                   json=post_data).json()

    def stop(self):
        self.stoped = True


@dataclass
class Data:
    """
    cover: 封面图片，可由recovers方法得到视频的帧截图
    """
    copyright: int = 1
    source: str = ''
    tid: int = 21
    cover: str = ''
    title: str = ''
    desc_format_id: int = 0
    desc: str = ''
    desc_v2: list = field(default_factory=list)
    dynamic: str = ''
    subtitle: dict = field(init=False)
    tag: Union[list, str] = ''
    videos: list = field(default_factory=list)
    dtime: Any = None
    open_subtitle: InitVar[bool] = False
    dolby: int = 0
    hires: int = 0
    no_reprint: int = 0
    is_only_self: int = 0
    charging_pay: int = 0
    no_disturbance: int = 0
    watermark: dict = field(default_factory=lambda: {'state': 0})
    space_hidden: int = 0
    extra_kwargs: dict = field(default_factory=dict)

    bvid: int = None
    # interactive: int = 0
    # no_reprint: int 1
    # charging_pay: int 1

    def __post_init__(self, open_subtitle):
        self.subtitle = {"open": int(open_subtitle), "lan": ""}
        if self.dtime and self.dtime - int(time.time()) <= 14400:
            self.dtime = None
        if isinstance(self.tag, list):
            self.tag = ','.join(self.tag)

    def delay_time(self, dtime: int):
        """设置延时发布时间，距离提交大于2小时，格式为10位时间戳"""
        if dtime - int(time.time()) > 7200:
            self.dtime = dtime

    def set_tag(self, tag: list):
        """设置标签，tag为数组"""
        self.tag = ','.join(tag)

    def append(self, video):
        self.videos.append(video)
