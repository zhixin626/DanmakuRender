"""封面生成（平台无关）：抽帧 + manimgl 渲染主播名/日期 → 一张封面图。
由各 uploader 引擎（biliwebapi / acfun / …）按需调用；生成只此一份，避免重复。
封面的"上传"是平台特有的，仍由各引擎自己做（biliwebapi.cover_up / acfun.upload_cover）。
"""
import logging
import re
from datetime import datetime
from pathlib import Path

from .extract_frame import extract_best_frame
from .render_with_manimgl import rendercover_with_manimgl
from .utils import replace_keywords

logger = logging.getLogger(__name__)


def generate_cover(video_path, cover_args, stime=None, account=''):
    """根据 cover_args 生成封面图，返回封面文件路径；不需要生成或失败返回 None。

    cover_args 关键字段：
      is_extract_frame : 从视频抽一帧做底图
      is_render_cover  : 在底图上用 manimgl 渲染主播名/日期
      name / name_color / time_template / ratio / sample_count
    两者都不开 → 返回 None（调用方自行决定是否用静态 config['cover']）。
    """
    ca = cover_args or {}
    if not (ca.get('is_render_cover') or ca.get('is_extract_frame')):
        return None

    out_dir = str(Path(video_path).parent)

    def _extract():
        try:
            return extract_best_frame(
                video_path,
                output_dir=out_dir,
                sample_count=ca.get('sample_count', 10),
                ratio=ca.get('ratio', '16/9'),
            )
        except Exception as e:
            logger.warning(f'封面帧提取失败: {e}')
            return None

    # 渲染封面（可选地以抽帧为底图）
    if ca.get('is_render_cover'):
        image_path = _extract() if ca.get('is_extract_frame') else None
        now = datetime.now()
        safe_account = re.sub(r'[\\/:*?"<>|]', '_', str(account))
        # 用时间戳命名，保证唯一不互相覆盖（上传成功后由 biliwebapi 删除，失败则留给人工）
        output_filename = f'cover_{safe_account}_{now.strftime("%Y%m%d_%H%M%S_%f")}.png'
        try:
            return rendercover_with_manimgl(
                ca.get('name', '未知主播'),
                replace_keywords(str(ca.get('time_template', '{NOW.MONTH}月{NOW.DAY}日')),
                                 {'now': now, 'stime': stime}),
                ca.get('name_color', '#111111'),
                str(now.year),
                output_dir=out_dir,
                image_path=image_path,
                output_filename=output_filename,
            )
        except Exception as e:
            logger.warning(f'封面渲染失败: {e}')
            return None

    # 仅抽帧
    return _extract()
