"""平台无关的封面准备逻辑。

``cover.mode`` 支持以下动态生成方式：

* ``extract_frame``：从视频抽取一帧；
* ``render_template``：渲染纯模板封面；
* ``render_text``：将一组文字纵向排列并渲染到安全区；
* ``render_on_frame``：优先使用 ``frame_path``，未配置时从视频抽帧，
  再将图片作为模板背景；
* ``render_text_on_frame``：使用相同的背景逻辑渲染 ``texts``。

``mode`` 为空时直接使用 ``cover.path``；动态生成失败时也会回退到
``cover.path``。上传及生成文件的清理由各 uploader 负责。
"""
import logging
import re
from datetime import datetime
from pathlib import Path

from .extract_frame import extract_best_frame
from .render_with_manimgl import (
    render_text_cover_with_manimgl,
    rendercover_with_manimgl,
)
from .utils import replace_keywords

logger = logging.getLogger(__name__)

_DYNAMIC_MODES = {
    'extract_frame',
    'render_template',
    'render_text',
    'render_on_frame',
    'render_text_on_frame',
}
_ON_FRAME_MODES = {'render_on_frame', 'render_text_on_frame'}


def _static_path(cover, context):
    path = cover.get('path')
    if not path:
        return None
    return replace_keywords(str(path), context or {})


def _configured_frame_path(cover, context):
    path = cover.get('frame_path')
    if not path:
        return None
    return replace_keywords(str(path), context or {})


def prepare_cover(video_path, cover, stime=None, account='', context=None):
    """准备上传封面，返回 ``(路径, 是否为本次动态生成)``。

    ``cover`` 必须是包含 ``mode`` / ``path`` 等字段的字典。
    """
    if cover is None:
        cover = {}
    if not isinstance(cover, dict):
        raise TypeError('cover 必须是字典，请使用 cover.mode / cover.path 配置')

    mode = cover.get('mode')
    fallback_path = _static_path(cover, context)
    if mode in (None, ''):
        return fallback_path, False
    if mode not in _DYNAMIC_MODES:
        choices = ', '.join(sorted(_DYNAMIC_MODES))
        raise ValueError(f'不支持的 cover.mode: {mode!r}，可选值为 {choices} 或空')

    out_dir = str(Path(video_path).parent)

    def _extract():
        try:
            return extract_best_frame(
                video_path,
                output_dir=out_dir,
                sample_count=cover.get('sample_count', 10),
                ratio=cover.get('ratio', '16/9'),
            )
        except Exception as e:
            logger.warning(f'封面帧提取失败: {e}')
            return None

    if mode == 'extract_frame':
        generated_path = _extract()
        return (generated_path, True) if generated_path else (fallback_path, False)

    # render_on_frame 可使用用户提供的 frame_path；只有未提供或路径无效时
    # 才从视频自动抽帧。所有权必须单独记录，不能误删用户原图。
    image_path = None
    generated_frame = False
    if mode in _ON_FRAME_MODES:
        configured_frame = _configured_frame_path(cover, context)
        if configured_frame and Path(configured_frame).is_file():
            image_path = configured_frame
        else:
            if configured_frame:
                logger.warning(f'指定的 cover.frame_path 不存在，改为从视频抽帧: {configured_frame}')
            image_path = _extract()
            generated_frame = bool(image_path)
    frame_opacity = 0.2
    if mode in _ON_FRAME_MODES:
        try:
            frame_opacity = float(cover.get('frame_opacity', frame_opacity))
        except (TypeError, ValueError):
            raise ValueError('cover.frame_opacity 必须是 0 到 1 之间的数字')
        if not 0 <= frame_opacity <= 1:
            raise ValueError('cover.frame_opacity 必须在 0 到 1 之间')

    now = datetime.now()
    template_context = {'now': now, 'stime': stime, **(context or {})}
    safe_account = re.sub(r'[\\/:*?"<>|]', '_', str(account))
    output_filename = f'cover_{safe_account}_{now.strftime("%Y%m%d_%H%M%S_%f")}.png'
    try:
        common_kwargs = {
            'output_dir': out_dir,
            'image_path': image_path,
            'frame_opacity': frame_opacity,
            'output_filename': output_filename,
            'edge': cover.get('edge', 0),
        }
        if mode in ('render_text', 'render_text_on_frame'):
            texts = [
                replace_keywords(str(text), template_context)
                for text in cover.get('texts', [])
            ]
            generated_path = render_text_cover_with_manimgl(
                texts,
                cover.get('colors'),
                transparent=(
                    mode == 'render_text'
                    and bool(cover.get('transparent', False))
                ),
                **common_kwargs,
            )
        else:
            generated_path = rendercover_with_manimgl(
                cover.get('name', '未知主播'),
                replace_keywords(
                    str(cover.get('time_template', '{NOW.MONTH}月{NOW.DAY}日')),
                    template_context,
                ),
                cover.get('name_color', '#111111'),
                replace_keywords(str(cover.get('year', now.year)), template_context),
                **common_kwargs,
            )
        return generated_path, True
    except Exception as e:
        logger.warning(f'封面渲染失败: {e}')
        return fallback_path, False
    finally:
        # 自动抽出的帧只作为 Manim 输入；用户传入的 frame_path 不归程序所有。
        if generated_frame and image_path:
            try:
                Path(image_path).unlink()
            except OSError:
                pass
