import json
import logging
import os
import re
import requests

from DMR.utils import random_user_agent
try:
    from .BaseAPI import BaseAPI
except ImportError:
    from BaseAPI import BaseAPI

logger = logging.getLogger(__name__)


# 与 biliLive-tools 的默认优先级一致：先 AVC，再 HEVC、AV1；
# 同一编码内依次选择 FLV、fMP4、HLS-TS。
BILIBILI_STREAM_PRIORITY = (
    'flv-avc',
    'fmp4-avc',
    'ts-avc',
    'flv-hevc',
    'fmp4-hevc',
    'ts-hevc',
    'flv-av1',
    'fmp4-av1',
    'ts-av1',
)

# DMR 的 FLV 会交给 StreamGears。它不能稳定处理 B 站的增强
# FLV-HEVC/AV1，因此自动模式不把这两种流作为候选；HEVC/AV1
# 原画改走 fMP4/HLS，由 FFmpeg 录制。
DMR_AUTO_STREAM_PRIORITY = (
    'flv-avc',
    'fmp4-avc',
    'ts-avc',
    'fmp4-hevc',
    'ts-hevc',
    'fmp4-av1',
    'ts-av1',
)


def _ordered_stream_types(preference=None):
    """把兼容的 stream_type 配置转换成候选类型顺序。

    flv/hls/fmp4 与 avc/hevc/av1 表示优先但允许回退；
    *_only 表示严格限制；type-codec（如 flv-avc）表示该组合优先。
    """
    priority = list(BILIBILI_STREAM_PRIORITY)
    value = str(preference or '').strip().lower().replace('_', '-')
    if value in ('', 'auto', 'none'):
        return list(DMR_AUTO_STREAM_PRIORITY)

    only = value.endswith('-only')
    if only:
        value = value[:-5]

    # biliLive-tools 中 hls 指 MPEG-TS，fmp4 是单独的格式选项。
    value = {'hls': 'ts'}.get(value, value)
    if '-' in value:
        fmt, codec = value.split('-', 1)
        fmt = {'hls': 'ts'}.get(fmt, fmt)
        exact = f'{fmt}-{codec}'
        if exact in priority:
            if only:
                return [exact]
            return [exact] + [item for item in priority if item != exact]

    if value in ('flv', 'fmp4', 'ts'):
        matched = [item for item in priority if item.startswith(f'{value}-')]
    elif value in ('avc', 'hevc', 'av1'):
        matched = [item for item in priority if item.endswith(f'-{value}')]
    else:
        logger.warning('未知的B站 stream_type=%s，将使用自动选流顺序', preference)
        return priority

    if only:
        return matched
    return matched + [item for item in priority if item not in matched]


def _cdn_matches(pattern, host):
    if not pattern:
        return True
    if host == pattern:
        return True
    try:
        return re.search(str(pattern), host) is not None
    except re.error:
        logger.warning('B站 stream_cdn 不是有效正则：%s，将按完整域名匹配', pattern)
        return False


class bilibili(BaseAPI):
    def __init__(self,rid) -> None:
        self.rid = rid
        self.sess = requests.Session()
        self.header = {
            'Referer': 'https://live.bilibili.com',
            'User-Agent': random_user_agent(),
        }

        # 获取标准房间号
        room_json = self._get_response()
        self.rid = room_json["data"]["room_id"]

    def _get_response(self):
        r_url = 'https://api.live.bilibili.com/room/v1/Room/room_init?id={}'.format(self.rid)
        res = self.sess.get(r_url, timeout=5, headers=self.header).json()
        return res

    def is_available(self) -> bool:
        code = self._get_response()['code']
        if code == 0:
            return True
        else:
            return False
        
    def onair(self) -> bool:
        resp = self._get_response()
        code = resp['code']
        if code == 0:
            live_status = resp['data']['live_status']
            if live_status == 1:
                return True
            else:
                return False

    def get_stream_urls(self,
                        bili_watch_cookies=None,
                        **kwargs) -> dict:
        bili_watch_cookies = bili_watch_cookies or '.login_info/.bili_watch_cookies.json'
        watch_cookies = {}
        if bili_watch_cookies.lower() != 'none':
            try:
                if not os.path.exists(bili_watch_cookies) and os.path.exists('.login_info/bilibili.json'):
                    bili_watch_cookies = '.login_info/bilibili.json'
                elif not os.path.exists(bili_watch_cookies):
                    from DMR.Uploader.biliuprs import biliuprs
                    logger.info(f'即将登录用于B站获取直播流的账号，如果不想登录请设置bili_watch_cookies为空.')
                    biliuprs(cookies=bili_watch_cookies)
                with open(bili_watch_cookies, 'r') as f:
                    cookies = json.load(f)
                watch_cookies = {c['name']: c['value'] for c in cookies['cookie_info']['cookies']}
                logger.info(f'正在使用 {bili_watch_cookies} 的cookies登录B站.')
            except Exception as e:
                logger.warning(f'B站观看cookies设置错误:{e}，即将使用无登录模式.')

        res = self._get_response()
        room_id = res['data']['room_id']
        f_url = 'https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo'
        params = {
            'room_id': room_id,
            'platform': 'html5',
            'protocol': '0,1',
            'format': '0,1,2',
            'codec': '0,1,2',   # 0:avc, 1:hevc, 2:av1
            'qn': 25000,
            'ptype': 8,
            'dolby': 5,
            'panorama': 1
        }
        resp = self.sess.get(f_url, params=params, headers=self.header, cookies=watch_cookies, timeout=5).json()
        real_urls = []
        try:
            stream = resp['data']['playurl_info']['playurl']['stream']
            for protocol_info in stream:
                for format_info in protocol_info['format']:
                    format_name = format_info['format_name']
                    for http_info in format_info['codec']:
                        base_url = http_info['base_url']
                        codec_name = http_info['codec_name']
                        for info in http_info['url_info']:
                            host = info['host']
                            extra = info['extra']
                            url = host + base_url + extra
                            real_urls.append({
                                'quality': http_info['current_qn'],
                                'stream_cdn': host.split('//')[1],
                                'stream_type': f'{format_name}-{codec_name}',
                                'stream_url': url,
                            })
        except Exception as e:
            raise RuntimeError(f'bilibili {self.rid} 直播流获取错误: {e}')

        if not real_urls:
            raise RuntimeError(f'bilibili {self.rid} 没有返回可用直播流')
        if max(real_urls, key=lambda x: x['quality'])['quality'] < 10000:
            logger.warning('未登录B站账号，无法录制原画，将录制最低画质直播.')

        return real_urls
    
    def get_stream_url(self,
                       stream_cdn=None, 
                       stream_type=None,
                       quality=None,
                       **kwargs) -> str:
        avail_urls = self.get_stream_urls(**kwargs)

        type_priority = _ordered_stream_types(stream_type)

        # 自动模式优先保留原画，但先排除 StreamGears 不兼容的增强
        # FLV-HEVC/AV1。这样有 1440p fMP4-HEVC 时会交给 FFmpeg，
        # 而不会降到 1080p AVC，也不会误交给 StreamGears。
        auto_mode = str(stream_type or '').strip().lower() in ('', 'auto', 'none')
        if auto_mode:
            safe_urls = [
                item for item in avail_urls
                if item['stream_type'].lower() in type_priority
            ]
            if not safe_urls:
                raise RuntimeError(f'Bilibili{self.rid}没有返回 DMR 可安全录制的流')
            max_quality = max(item['quality'] for item in safe_urls)
            avail_urls = [
                item for item in safe_urls
                if item['quality'] == max_quality
            ]

        selected_group = []
        selected_type = None
        for candidate_type in type_priority:
            selected_group = [
                item for item in avail_urls
                if item['stream_type'].lower() == candidate_type
            ]
            if selected_group:
                selected_type = candidate_type
                break

        if not selected_group:
            raise RuntimeError(
                f'Bilibili{self.rid}没有满足 stream_type={stream_type} 的流'
            )

        # 同一种格式/编码可能返回多个 qn；保留该类型的最高画质。
        # 列表原有顺序就是 B 站 url_info 的顺序，因此 [0] 仍是首个 CDN。
        selected_quality = max(item['quality'] for item in selected_group)
        selected_group = [
            item for item in selected_group
            if item['quality'] == selected_quality
        ]

        # biliLive-tools 默认使用 url_info[0]（主线）。显式填写 stream_cdn
        # 时保留 DMR 原有能力：在同类型候选中取第一个匹配项；匹配不到仍回退主线。
        chosen_info = selected_group[0]
        if stream_cdn:
            cdn_group = [
                item for item in selected_group
                if _cdn_matches(stream_cdn, item['stream_cdn'])
            ]
            if cdn_group:
                chosen_info = cdn_group[0]
            else:
                logger.warning(
                    'Bilibili%s的%s流没有匹配CDN %s，将使用B站主线',
                    self.rid,
                    selected_type,
                    stream_cdn,
                )

        logger.info(
            '[选流] qn=%s 编码=%s CDN=%s 来源=%s',
            chosen_info['quality'],
            chosen_info['stream_type'],
            chosen_info['stream_cdn'],
            '指定CDN' if stream_cdn and chosen_info is not selected_group[0] else 'B站主线',
        )
        return chosen_info['stream_url']

    def get_info(self) -> tuple:
        try:
            resp = self.sess.get(f'https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={self.rid}', headers=self.header, timeout=5).json()
            data = resp['data']
            
            title = data['room_info']['title']
            uname = data['anchor_info']['base_info']['uname']
            face_url = data['anchor_info']['base_info']['face']
            cover_url = data['room_info']['cover']
        
        # 海外服务器可能报错就用下面的方法
        except Exception as e:
            resp = self.sess.get(f'https://api.live.bilibili.com/room/v1/Room/get_info?room_id={self.rid}', headers=self.header, timeout=5).json()
            title = resp['data']['title']
            cover_url = resp['data']['user_cover']

            resp = self.sess.get(f'https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={self.rid}', headers=self.header, timeout=5).json()
            info = resp['data']['info']
            uname = info['uname']
            face_url = info['face']

        return title, uname, face_url, cover_url
    
    def get_stream_header(self) -> dict:
        return self.header
