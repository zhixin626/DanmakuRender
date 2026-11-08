import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from DMR.utils.cover import prepare_cover

# ── 在这里修改配置 ────────────────────────────────────────────
# extract_frame 必须填写视频路径。
# render_on_frame / render_text_on_frame 可直接使用 frame_path；留空则从 VIDEO_PATH 抽帧。
# render_template / render_text 不需要视频；mode: None 时使用 path。
# VIDEO_PATH = R"D:\DanmakuRender\Tasks文件\峰哥（弹幕版）\08月20日(1).mp4"
VIDEO_PATH = R""
OUTPUT_FILENAME = "cover2.png"

COVER = {
    # 通用参数：选择模式；path 是不生成或生成失败时使用的静态封面
    "mode": "render_template",
    "path": "",
    # 所有文字渲染模式：-1 左，0 居中，1 右
    "edge": 0,

    # 抽帧参数（两个 on_frame 模式未提供 frame_path 时也使用）
    "ratio": "16/9",
    "sample_count": 20,

    # render_template 参数（render_on_frame 也使用这个文字模板）
    "name": "邵艾伦",
    "name_color": "#9afffe",
    "time_template": "10月9日",
    "year": "2026",

    # render_text / render_text_on_frame 参数
    # colors 可传一个，或与 texts 数量一致
    "texts": ["邵艾伦"],
    "colors": ["#80ffff"],
    # 仅 render_text 使用；True 时向 ManimGL 添加 -t
    "transparent": False,

    # render_on_frame / render_text_on_frame 参数
    # frame_path 为空时从 VIDEO_PATH 自动抽帧
    "frame_path": R"",
    "frame_opacity": 0.9,
}
# ─────────────────────────────────────────────────────────────


def main():
    output_dir = Path(__file__).parent
    mode = COVER.get("mode")
    cover = dict(COVER)
    frame_path_value = cover.get("frame_path")

    on_frame_modes = {"render_on_frame", "render_text_on_frame"}
    if mode == "extract_frame" or (mode in on_frame_modes and not frame_path_value):
        if not VIDEO_PATH:
            raise ValueError(f"cover.mode={mode} 且未提供 frame_path 时必须填写 VIDEO_PATH")
        source_path = Path(VIDEO_PATH).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(f"视频不存在: {source_path}")
    elif mode in on_frame_modes:
        frame_path = Path(frame_path_value).expanduser()
        if not frame_path.is_absolute():
            frame_path = output_dir / frame_path
        if not frame_path.is_file():
            raise FileNotFoundError(f"背景帧不存在: {frame_path}")
        cover["frame_path"] = str(frame_path.resolve())
        # prepare_cover 仍以 video_path 的父目录作为输出目录；手动传图时用
        # 脚本目录下的占位路径即可，不会读取这个文件。
        source_path = output_dir / "_cover_source.mp4"
    else:
        # 两个纯文字模式不读取视频；占位路径仅用于确定生成目录。
        source_path = output_dir / "_cover_source.mp4"

    result, generated = prepare_cover(
        str(source_path),
        cover,
        account="manual",
    )
    if not result:
        raise RuntimeError("没有生成或配置可用的封面")

    if generated:
        result_path = Path(result)
        target_path = output_dir / OUTPUT_FILENAME
        # 仅抽帧时保留真实图片格式，避免把 JPEG 直接改成 .png 后缀。
        if mode == "extract_frame" and result_path.suffix.lower() != target_path.suffix.lower():
            target_path = target_path.with_suffix(result_path.suffix)
        if result_path.resolve() != target_path.resolve():
            os.replace(result_path, target_path)
        result = str(target_path)

    print(f"封面已保存到 {result}")


if __name__ == "__main__":
    main()
