import logging
import random
import re
import os
from typing import Optional
import requests
import urllib
import json
try:
    from .BaseAPI import BaseAPI
except ImportError:
    from BaseAPI import BaseAPI
try:
    from .abogus import ABogus
except ImportError:
    from abogus import ABogus
from DMR.utils import random_user_agent
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


logger = logging.getLogger(__name__)


class douyin_utils():
    base_headers = {
        'authority': 'live.douyin.com',
        'accept-encoding': 'gzip, deflate',         # brotli 会导致返回内容无法解码
        'Referer': "https://live.douyin.com/",
        'user-agent': random_user_agent(),
    }
    cookies = {}

    _douyin_ttwid: Optional[str] = None
    # DOUYIN_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36'
    CHARSET = "abcdef0123456789"
    LONG_CHATSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"

    @staticmethod
    def get_ttwid() -> Optional[str]:
        if not douyin_utils._douyin_ttwid:
            try:
                page = requests.get("https://live.douyin.com/1-2-3-4-5-6-7-8-9-0", timeout=15, 
                                    headers=douyin_utils.base_headers)
                douyin_utils._douyin_ttwid = page.cookies.get("ttwid")
            except Exception as e:
                logger.exception(f'获取抖音ttwid失败: {e}')
                raise e
        return douyin_utils._douyin_ttwid

    @staticmethod
    def generate_ms_token() -> str:
        '''生成随机 msToken'''
        return ''.join(random.choice(douyin_utils.LONG_CHATSET) for _ in range(184))


    @staticmethod
    def generate_nonce() -> str:
        """生成 21 位随机十六进制小写 nonce"""
        return ''.join(random.choice(douyin_utils.CHARSET) for _ in range(21))


    @staticmethod
    def generate_odin_ttid() -> str:
        """生成 160 位随机十六进制小写 odin_ttid"""
        return ''.join(random.choice(douyin_utils.CHARSET) for _ in range(160))
    
    @classmethod
    def get_headers(cls, extra_cookies:dict=None) -> dict:
        headers = cls.base_headers.copy()
        cookies = {
            'ttwid': cls.get_ttwid(),
            '__ac_nonce': cls.generate_nonce(),
            'odin_ttid': cls.generate_odin_ttid(),
        }
        if extra_cookies:
            cookies.update(extra_cookies)
        headers['cookie'] = '; '.join(f'{k}={v}' for k,v in cookies.items())
        return headers
    
    @classmethod
    def build_request_url(cls, url:str, query:dict=None) -> str:
        headers = cls.get_headers()
        user_agent = headers.get('user-agent')
        
        parsed_url = urlparse(url)
        params_to_encode = (query or parse_qs(parsed_url.query)).copy()
        
        try:
            browser_info = user_agent.split(' ')[-1]
            browser_name = browser_info.split('/')[0]
            browser_version = browser_info.split('/')[1]
        except IndexError:
            browser_name = "Edge"
            browser_version = "124.0.0.0"
        params_to_encode.update({
            'aid': '6383',
            'enter_from': random.choice(['link_share', 'web_live']),
            'device_platform': 'web',
            'browser_language': 'zh-CN',
            'browser_platform': 'Win32',
            'browser_name': browser_name,
            'browser_version': browser_version,
        })
        
        encoded_params_str = urlencode(params_to_encode, doseq=True)
        abogus_generator = ABogus(user_agent=user_agent)
        abogus_value = abogus_generator.generate_abogus(params=encoded_params_str, body="")[1]
        final_params = params_to_encode
        final_params['a_bogus'] = abogus_value
        new_query_string = urlencode(final_params, doseq=True)
        new_url = urlunparse((
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query_string,
            parsed_url.fragment
        ))
        return new_url


class douyin(BaseAPI):
    def __init__(self, rid:str) -> None:
        self.web_rid = rid
        self.sess = requests.Session()
        self.headers = douyin_utils.get_headers()
        if len(rid) == 19:
            self.real_rid = rid
        else:
            try:
                resp = self._get_response_douyin()
                self.real_rid = resp['data'][0]['id_str']
            except Exception:
                raise Exception(f'解析抖音房间号{rid}错误.')
    
    def __del__(self):
        self.sess.close()

    def is_available(self) -> bool:
        return len(self.real_rid) == 19
    
    def _get_response_douyin(self):
        url = 'https://live.douyin.com/webcast/room/web/enter/'
        params = {
            'web_rid': self.web_rid,
        }
        self.headers = douyin_utils.get_headers()
        url = douyin_utils.build_request_url(url, query=params)
        response = self.sess.get(url, headers=self.headers, timeout=5)
        response.raise_for_status()
        data = response.json()['data']
        return data

    def onair(self) -> bool:
        resp = self._get_response_douyin()
        code = resp['data'][0]['status']
        return code == 2

    @staticmethod
    def _quality_score(quality: dict) -> tuple:
        """抖音画质排序：优先官方 level，再比较像素、码率和常见画质名称。"""
        sdk_key = str(quality.get('sdk_key') or quality.get('quality') or '').lower()
        name_score = 0
        for keyword, score in (
            ('origin', 5), ('full_hd', 5), ('uhd', 5),
            ('hd', 4), ('sd', 3), ('ld', 2),
        ):
            if keyword in sdk_key:
                name_score = score
                break

        resolution = str(quality.get('resolution') or '')
        try:
            width, height = (int(value) for value in resolution.lower().split('x', 1))
            pixels = width * height
        except (TypeError, ValueError):
            pixels = 0

        return (
            int(quality.get('level') or 0),
            pixels,
            int(quality.get('v_bit_rate') or 0),
            name_score,
        )

    def get_stream_urls(self, **kwargs) -> list:
        resp = self._get_response_douyin()
        stream_info = resp['data'][0]['stream_url']
        real_urls = []
        qualities = []
        try:
            extra_data = stream_info['live_core_sdk_data']['pull_data']['stream_data']
            extra_data = json.loads(urllib.parse.unquote(extra_data))
            qualities = stream_info['live_core_sdk_data']['pull_data']['options']['qualities']
            avail_qualities = [q for q in qualities if not q.get('disable')]
            best_quality = max(avail_qualities or qualities, key=self._quality_score)
            this_quality = best_quality['sdk_key']
            url_dict = extra_data['data']
            for stype, url in url_dict[this_quality]['main'].items():
                if not str(url).startswith('http'):
                    continue
                real_urls.append({
                    'quality': this_quality,
                    'quality_name': best_quality.get('name'),
                    'level': best_quality.get('level'),
                    'codec': best_quality.get('v_codec'),
                    'resolution': best_quality.get('resolution'),
                    'bitrate': best_quality.get('v_bit_rate'),
                    'stream_type': stype,
                    'stream_url': url,
                })
        except Exception as e:
            logger.warning(f'抖音{self.web_rid}主直播流解析失败，将尝试FLV备用地址: {e}')
            flv_urls = stream_info.get('flv_pull_url') or {}
            quality_meta = {
                str(q.get('sdk_key')): q for q in qualities if q.get('sdk_key')
            }
            available_flv_urls = [
                (quality, url) for quality, url in flv_urls.items()
                if str(url).startswith('http')
            ]
            if not available_flv_urls:
                raise RuntimeError(f'抖音{self.web_rid}没有可用的直播流地址') from e

            quality, url = max(
                available_flv_urls,
                key=lambda item: self._quality_score(
                    quality_meta.get(str(item[0]), {'quality': item[0]})
                ),
            )
            meta = quality_meta.get(str(quality), {})
            real_urls = [{
                    'quality': quality,
                    'quality_name': meta.get('name'),
                    'level': meta.get('level'),
                    'codec': meta.get('v_codec'),
                    'resolution': meta.get('resolution'),
                    'bitrate': meta.get('v_bit_rate'),
                    'stream_type': 'flv',
                    'stream_url': url,
                }]

        if not real_urls:
            raise RuntimeError(f'抖音{self.web_rid}没有可用的直播流地址')
        return real_urls
    
    def get_stream_url(self, stream_type=None, **kwargs) -> str:
        stream_type = str(stream_type or 'flv').lower()
        if stream_type in ('m3u8', 'fmp4'):
            stream_type = 'hls'

        avail_urls = self.get_stream_urls(**kwargs)
        selected_urls = [
            url_info for url_info in avail_urls
            if str(url_info['stream_type']).lower() == stream_type
        ]

        if not selected_urls:
            logger.warning(f'抖音{self.web_rid}没有{stream_type}流，将使用默认选项.')
            selected_urls = avail_urls

        chosen_info = random.choice(selected_urls)
        selected_url = chosen_info['stream_url']
        cdn = urlparse(selected_url).netloc or '未知'
        proto = 'FLV' if chosen_info['stream_type'].lower() == 'flv' else 'HLS'
        logger.info(
            f'[选流] 画质={chosen_info.get("quality_name") or chosen_info["quality"]} '
            f'level={chosen_info.get("level", "未知")} '
            f'分辨率={chosen_info.get("resolution") or "未知"} '
            f'码率={chosen_info.get("bitrate") or "未知"} '
            f'编码={chosen_info.get("codec") or "未知"} '
            f'CDN={cdn} 协议={proto}'
        )
        return selected_url

    def get_info(self) -> tuple:
        resp = self._get_response_douyin()
        title = resp['data'][0]['title']
        uname = resp['user']['nickname']
        face_url = resp['user']['avatar_thumb']['url_list'][0]
        keyframe_url = None
        return title, uname, face_url, keyframe_url


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    rid = input('请输入抖音直播间号(网页直播间URL中的数字, 如 https://live.douyin.com/123456 中的123456): ').strip()

    dy = douyin(rid)
    print('real_rid:', dy.real_rid)
    print('is_available:', dy.is_available())
    print('onair:', dy.onair())
    print('get_info:', dy.get_info())

    urls = dy.get_stream_urls()
    print('get_stream_urls:')
    for u in urls:
        print(' ', u)

    print('get_stream_url:', dy.get_stream_url())
