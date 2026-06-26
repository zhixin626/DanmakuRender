"""字幕（ASR 语音识别）共用逻辑：生成字幕 ass + 滤镜路径转义 + 清理中间文件。

DmRender 与 EmojiDmRender 共用——"生成 ass / 路径转义 / 清理中间文件"三件事两边一样。
"把字幕烧进画面"各引擎不同（dmrender 一条 ffmpeg 滤镜烧；emoji 交给 emoji_render_mt 按段烧），
故不在此处，由各引擎自己做。
"""
import os
import platform
import logging
from os.path import exists

logger = logging.getLogger(__name__)

# ASR 生成字幕时从 subtitle 配置里取的样式键
_STYLE_KEYS = ('font_name', 'font_size', 'margin_bottom', 'font_color',
               'outline', 'outline_color', 'wrap_chars')


def escape_sub(path):
    """把路径转义成可放进 ffmpeg `subtitles=` 滤镜的形式（Windows 下处理 \\ 与盘符冒号）。"""
    if platform.system().lower() == 'windows':
        return path.replace("\\", "/").replace(":/", "\\:/")
    return path


def generate_subtitle_ass(video_path, subtitle_cfg, log=None):
    """按 subtitle 配置对视频做 ASR 生成字幕 ass，返回 ass 路径。
    未开启（enable 缺省/为假）或失败时返回 None（失败只记日志、不抛，渲染照常进行）。"""
    log = log or logger
    cfg = subtitle_cfg if isinstance(subtitle_cfg, dict) else {}
    if not cfg.get('enable'):
        return None
    try:
        import subtitle_core as sc
        style = {k: cfg[k] for k in _STYLE_KEYS if k in cfg}
        log.info(f'开始对 {video_path} 做语音识别并生成字幕...')
        sub_ass = sc.generate_subtitle_ass(video_path, style=style, do_asr=True)
        log.info(f'字幕已生成: {sub_ass}')
        return sub_ass
    except Exception as e:
        log.error(f'生成语音识别字幕失败，本次跳过字幕: {e}')
        return None


def clean_subtitle_intermediates(video_path, sub_ass, clean=True, log=None):
    """清理 ASR 字幕中间文件（.txt 与 (字幕).ass），渲染完即用完即删。
    clean=False 则保留。只删真实存在的文件，不存在安全跳过。"""
    log = log or logger
    if not clean:
        return
    from send2trash import send2trash
    stem = os.path.splitext(video_path)[0]
    for p in (stem + '.txt', sub_ass or (stem + '(字幕).ass')):
        try:
            if p and exists(p):
                send2trash(os.path.abspath(p))
                log.info(f'已清理 ASR 字幕中间文件: {p}')
        except Exception as e:
            log.debug(f'清理 ASR 字幕中间文件失败(忽略): {p}: {e}')
