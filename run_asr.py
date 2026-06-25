import os
import re
import sys
import subprocess
import tempfile

os.environ["MODELSCOPE_CACHE"] = r"D:\models"
os.environ["HF_HOME"] = r"D:\models"

from funasr import AutoModel

# ── 配置 ─────────────────────────────────────────────────────────────────
SUBTITLE_OFFSET_MS = 150   # 字幕整体延后，补 FunASR 把起音/换气算进句首导致的偏快
MIN_DURATION_MS    = 300   # 每条字幕最小持续时间，过短/零时长会被补到这个值
CLEAN_PUNCTUATION  = True  # 去标点：句末标点删掉、句中标点换空格。设 False 则保留原始标点
ASR_SAMPLE_RATE    = 16000 # paraformer-zh 要求 16k 单声道
GAP_REPORT_SEC     = 0.3   # 音频断流检测阈值（秒），仅用于日志提示
# ─────────────────────────────────────────────────────────────────────────


def detect_audio_gaps(path, threshold=GAP_REPORT_SEC):
    """扫描音频包时间戳，返回 [(位置秒, 空洞秒), ...]。失败返回 []。
    直播卡顿会在音频流留下时间戳空洞——前一个包到下一个包之间跳变超过阈值即为断流。"""
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a',
             '-show_entries', 'packet=dts_time', '-of', 'csv=p=0', path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=300,
        ).stdout.decode('utf-8', 'ignore')
    except Exception:
        return []
    gaps, prev = [], None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = float(line)
        except ValueError:
            continue
        if prev is not None and t - prev > threshold:
            gaps.append((prev, t - prev))
        prev = t
    return gaps


def extract_aligned_audio(src_path):
    """抽取 16k 单声道 wav，用 aresample=async=1 在时间戳空洞处补静音，
    使音频时间轴与视频对齐——否则直播卡顿断流处的空洞会被解码"吞掉"，
    导致空洞之后的字幕整体提前、与画面对不上。
    返回 (音频路径, 是否为临时文件)；失败时回退原文件直接识别。"""
    fd, wav_path = tempfile.mkstemp(suffix='.wav', prefix='asr_aligned_')
    os.close(fd)
    cmd = [
        'ffmpeg', '-y', '-v', 'error',
        '-i', src_path,
        '-vn',
        '-af', 'aresample=async=1:first_pts=0',   # 关键：按时间戳补静音/去重叠
        '-ac', '1', '-ar', str(ASR_SAMPLE_RATE),
        '-c:a', 'pcm_s16le',
        wav_path,
    ]
    try:
        p = subprocess.run(cmd, stderr=subprocess.PIPE)
        ok = (p.returncode == 0 and os.path.exists(wav_path)
              and os.path.getsize(wav_path) > 0)
        if not ok:
            tail = (p.stderr or b'').decode('utf-8', 'ignore')[-500:]
            print(f"音频预处理失败，回退为直接识别原文件。ffmpeg: {tail}")
    except Exception as e:
        print(f"音频预处理异常，回退为直接识别原文件：{e}")
        ok = False
    if not ok:
        try:
            os.remove(wav_path)
        except OSError:
            pass
        return src_path, False
    return wav_path, True


def resolve_input():
    """两种启动方式：命令行传入路径，或交互询问。必须提供一个视频。"""
    if len(sys.argv) > 1:
        path = sys.argv[1].strip().strip('"')
        print(f"使用命令行传入的视频：{path}")
        return path
    ans = input("请输入视频/音频路径：").strip().strip('"')
    if not ans:
        print("未提供视频路径，已退出。")
        sys.exit(1)
    return ans


_PUNCT = "，。！？、；：,.!?;:…—～~·“”\"'‘’《》（）()【】[]"

def clean_text(text):
    """去掉句末标点；句中标点用空格代替。CLEAN_PUNCTUATION 为 False 时原样返回。"""
    if not CLEAN_PUNCTUATION:
        return text.strip()
    text = text.strip()
    text = re.sub(rf"[{re.escape(_PUNCT)}\s]+$", "", text)   # 末尾标点/空白
    text = re.sub(rf"[{re.escape(_PUNCT)}]+", " ", text)     # 中间标点 -> 空格
    return re.sub(r"\s+", " ", text).strip()                 # 合并多余空格


def ms_to_ts(ms):
    ms = int(ms)
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, ms = divmod(r, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def fix_durations(items, min_dur=MIN_DURATION_MS):
    """保证每条至少 min_dur 毫秒；若因此与下一条重叠，则顺移下一条的起点。"""
    items = sorted(items, key=lambda x: x["start"])
    for i, it in enumerate(items):
        if it["end"] - it["start"] < min_dur:
            it["end"] = it["start"] + min_dur
        if i + 1 < len(items) and items[i + 1]["start"] < it["end"]:
            items[i + 1]["start"] = it["end"]
    return items


def main():
    input_audio_path = resolve_input()
    if not os.path.exists(input_audio_path):
        print(f"错误：找不到文件 {input_audio_path}")
        sys.exit(1)

    # 直播卡顿会在音频流留下时间戳空洞。FunASR 直接读取媒体时会"吞掉"这些空洞，
    # 使空洞之后的字幕整体提前。这里先检测并补静音对齐，再喂给识别模型。
    gaps = detect_audio_gaps(input_audio_path)
    if gaps:
        total = sum(g for _, g in gaps)
        print(f"检测到 {len(gaps)} 处音频断流（共约 {total:.2f}s），将补静音对齐后再识别：")
        for pos, g in gaps[:10]:
            print(f"  - 约 {pos:.1f}s 处断流 {g:.2f}s")
        if len(gaps) > 10:
            print(f"  - ……其余 {len(gaps) - 10} 处略")

    asr_input, is_temp = extract_aligned_audio(input_audio_path)

    model = AutoModel(
        model="paraformer-zh",
        model_revision="v2.0.4",
        vad_model="fsmn-vad",
        vad_model_revision="v2.0.4",
        punc_model="ct-punc-c",
        punc_model_revision="v2.0.4",
        spk_model="cam++",
        spk_model_revision="v2.0.2",
        disable_update=True,   # 跳过每次启动时 funasr 联网检查更新
    )

    try:
        results = model.generate(
            input=asr_input,
            batch_size_s=300,
            vad_kwargs={"max_single_segment_time": 30000},
            # hotword="",
        )
    finally:
        if is_temp:
            try:
                os.remove(asr_input)
            except OSError:
                pass

    # 汇总所有句子（保留毫秒精度），整体延后修正偏快
    items = []
    for item in results:
        for s in item.get("sentence_info", []):
            items.append({
                "start": max(0, s.get("start", 0) + SUBTITLE_OFFSET_MS),
                "end":   max(0, s.get("end", 0) + SUBTITLE_OFFSET_MS),
                "spk":   s.get("spk", "?"),
                "text":  clean_text(s.get("text", "")),
            })

    items = fix_durations(items)

    # 可选第二个参数指定输出 txt 路径；否则默认视频同名 .txt
    if len(sys.argv) > 2 and sys.argv[2].strip():
        output_text_path = sys.argv[2].strip().strip('"')
    else:
        output_text_path = os.path.splitext(input_audio_path)[0] + ".txt"
    with open(output_text_path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(f"[{ms_to_ts(it['start'])} --> {ms_to_ts(it['end'])}] spk_{it['spk']}: {it['text']}\n")

    print(f"完成，共 {len(items)} 条，已保存到：{output_text_path}")


if __name__ == "__main__":
    main()
