import subprocess
import json
import warnings
import re

from .toolsmgr import ToolsList

class FFprobe():
    header = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) AppleWebKit/537.36 '
                                '(KHTML, like Gecko) Chrome/75.0.3770.100 Mobile Safari/537.36 '
            }

    @classmethod
    def ffprobe(cls) -> str:
        return ToolsList.get('ffprobe')
    
    @classmethod
    def run_ffprobe(cls,fpath):
        out = subprocess.check_output([
            cls.ffprobe(),
            '-i', fpath,
            '-print_format','json',
            '-select_streams', 'v:0',
            '-show_format','-show_streams',
            '-v','quiet'
            ])
        out = out.decode('utf8')
        res = json.loads(out)
        return res
        
    @classmethod
    def get_duration(cls,fpath) -> float:
        try:
            res = cls.run_ffprobe(fpath)
            try:
                st = float(res['format']['start_time'])
            except Exception:
                st = 0
            duration = float(res['format']['duration'])-st
            return max(duration, 0)
        except Exception:
            return -1

    @classmethod
    def run_ffprobe_livestream(cls, url, header=None):
        if header is None:
            header = cls.header
        out = subprocess.check_output([
            cls.ffprobe(),
            '-headers', ''.join('%s: %s\r\n' % x for x in header.items()),
            '-i', url,
            '-select_streams', 'v:0', 
            '-print_format','json',
            '-show_format','-show_streams',
            '-v','quiet'
            ],
            timeout=15,
        )
        out = out.decode('utf8')
        res = json.loads(out)
        return res

    @classmethod
    def get_livestream_info(cls,url,header=None) -> dict:
        res = cls.run_ffprobe_livestream(url,header)
        return res['streams'][0]
        
    @classmethod
    def get_resolution(cls, url:str, header=None) -> tuple:
        try:
            if url.startswith('http'):
                res = cls.run_ffprobe_livestream(url, header)
            else:
                res = cls.run_ffprobe(url)
            resolution = res['streams'][0]['width'],res['streams'][0]['height']
            return resolution
        except Exception:
            return 0,0

    @classmethod
    def get_fps(cls, url:str, header=None) -> float:
        """取标称帧率(r_frame_rate，如 '60/1' → 60.0)。用 r_frame_rate 而非 avg_frame_rate：
        直播源 avg 常算成 62 这种怪值，r 才是干净的标称值。失败返回 0。"""
        try:
            if url.startswith('http'):
                res = cls.run_ffprobe_livestream(url, header)
            else:
                res = cls.run_ffprobe(url)
            st = res['streams'][0]
            rate = st.get('r_frame_rate') or st.get('avg_frame_rate') or '0'
            num, _, den = str(rate).partition('/')
            den = float(den) if den else 1.0
            return float(num) / den if den else 0.0
        except Exception:
            return 0.0