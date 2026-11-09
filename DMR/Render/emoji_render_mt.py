# -*- coding: utf-8 -*-
"""
弹幕彩色 emoji 渲染（整段单路版）

用法:
    python emoji_render_mt.py <输入.flv> <输入.ass> <输出.mp4>
        [--vb 8M] [--ab 160K] [--no-hwaccel]
        [--emoji-font 路径] [--emoji-pack 表情目录] [--emoticon-dir 纯表情目录]
        [--render-plan LayoutCompiler生成的JSON]
        [--rescale-to-1080p] [--min-fps 30|60]
        [--canvas-resolution 1920x1080]
        [--resize 1280x720|0.5]

特性:
    - 整段原视频连续解码 → libass烧普通弹幕 + nv12上贴彩色emoji → 混修复后的音频。
    - 文字字体从 ASS 的 R2L 样式读取(Fontname+Bold,经 matplotlib 解析为文件),与普通弹幕一致。
    - emoji 字体可换: --emoji-font 指定 ttf/名称(默认系统 Segoe UI Emoji,矢量、最快最清晰;
      位图字体如 Noto 也支持,会按 strike 缩放,稍慢略糊)。
    - [xxx] 方括号表情替换: --emoji-pack 指向目录,把弹幕里的 [笑] 换成 目录/笑.png。
    - --rescale-to-1080p: 所有横/竖屏源等比缩放并补边到对应的1080p画布。
    - --resize: 输出缩放,"宽x高" 或缩放系数(如 0.5);合成在渲染画布完成,仅最终编码缩放。
"""
import os, re, sys, json, time, shutil, logging, subprocess, threading, queue, atexit, collections
import hashlib
import io
import urllib.parse
import urllib.request
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from .plan_ass import render_plan_to_ass
    from .resolution import resolve_render_canvas
    from .media import resolve_output_fps, source_frame_rate
    from .sprites import (
        DEFAULT_EMOJI_FONT,
        EMOTICON_PREFIX,
        OUT,
        SCALE,
        _STRIP,
        SpriteFactory,
        has_special,
        is_emoji,
        load_pack,
        parse_pill,
        resolve_emoji_font,
        resolve_font,
    )
except ImportError:
    from plan_ass import render_plan_to_ass
    from resolution import resolve_render_canvas
    from media import resolve_output_fps, source_frame_rate
    from sprites import (
        DEFAULT_EMOJI_FONT,
        EMOTICON_PREFIX,
        OUT,
        SCALE,
        _STRIP,
        SpriteFactory,
        has_special,
        is_emoji,
        load_pack,
        parse_pill,
        resolve_emoji_font,
        resolve_font,
    )

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# 本脚本零 DMR 依赖(独立子进程运行),用标准库 logging。
# 每行带 [级别] 前缀,被 DMR 以管道捕获时由 emoji_dmrender 解析出级别转发到主日志;
# 单独跑时就是普通的控制台输出。级别开到 DEBUG,由父进程决定哪些进控制台。
log = logging.getLogger("emoji_mt")


def setup_log():
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.DEBUG)
    log.propagate = False


def kill_tree(pid):
    """强杀进程及其子孙。taskkill 返回不代表进程已消失,调用方需自行确认。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def wait_dead(procs, timeout=10.0):
    """等进程真正退出。taskkill 是异步的,不等就去删文件必然撞上句柄未释放。"""
    deadline = time.time() + timeout
    for pr in procs:
        left = deadline - time.time()
        try:
            pr.wait(timeout=max(0.1, left))
        except Exception:
            pass


def rmtree_retry(path, tries=5, wait=1.0):
    """删临时目录。杀毒扫描/句柄未释放会让 Windows 短暂锁住文件,重试几次;
    最终失败必须吭声,否则残留目录会一直攒着没人知道。"""
    for i in range(tries):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as e:
            if i == tries - 1:
                log.warning("临时目录清理失败,请手动删除: %s (%s)", path, e)
                return False
            time.sleep(wait)

# ============================ 单段渲染（worker） ============================
def ass_time(value):
    """Convert an ASS timestamp to seconds."""
    hours, minutes, rest = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def sec_to_ass(seconds):
    """Convert seconds to an ASS timestamp."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remainder = seconds % 60
    return f"{hours}:{minutes:02d}:{remainder:05.2f}"


def fmt(seconds):
    """Format seconds for renderer logs."""
    whole = int(seconds)
    return f"{whole // 60}分{whole % 60:02d}秒"


QDEPTH = 8


def render_segment(cfg):
    seg_file = cfg["seg"]; textonly_ass = cfg["textonly"]; out_ts = cfg["out"]; prog_file = cfg["prog"]
    W, H, fps, vb = cfg["W"], cfg["H"], cfg["fps"], cfg["vb"]
    use_hwaccel = cfg["hwaccel"]; emoji_events = cfg["events"]
    pack = cfg.get("pack", {})
    segdir = os.path.dirname(os.path.abspath(out_ts)) or "."

    fs_ass = cfg["fs"]
    sprite_factory = SpriteFactory(cfg["textfont"], cfg["emojifont"], pack, fs_ass)
    for event in emoji_events:
        event["_spr"] = sprite_factory.get_sprite(
            event["text"],
            event["rgb"],
            event.get("pill"),
            event.get("emoticon_pack"),
            event.get("outline", OUT),
            event.get("outline_rgb", (0, 0, 0)),
        )

    dec_cmd = [FFMPEG, "-v", "error", "-fflags", "+genpts+discardcorrupt"]
    if use_hwaccel: dec_cmd += ["-hwaccel", "auto"]
    # 字幕（可选）：和弹幕在同一遍 libass 里一起烧，几乎不增加编码成本
    sub_seg = cfg.get("subtitle")
    sub_filter = f",subtitles={os.path.basename(sub_seg)}" if sub_seg else ""
    # scale+pad 到固定 W×H：兜住「直播中途分辨率变化(如连线)」——否则后半段帧尺寸变了、
    # 而下游编码器按固定 -s {W}x{H} 切 nv12 字节流 → 字节错位 → 彩条乱码；且弹幕/礼物框(ass 按 W×H 定位)会被拉伸。
    # 源本就是 W×H 时此步为 no-op；变化的片段会被等比缩放居中补边到 W×H。放在 subtitles 之前，保证弹幕在 W×H 上不变形。
    uniform = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,"
        f"pad={W}:{H}:-1:-1:color=black,setsar=1,"
    )
    # 整段单路模式直接读取原视频绝对路径；工作目录仍只放 ASS 和输出临时文件。
    dec_cmd += ["-i", os.path.abspath(seg_file),
                # fps 在 subtitles 之前：先把(可变帧率的)源铺成均匀 CFR，再让 libass 逐帧画弹幕 → 顺滑
                "-vf", f"fps={fps},{uniform}subtitles={os.path.basename(textonly_ass)}{sub_filter}",
                "-fps_mode", "cfr", "-f", "rawvideo", "-pix_fmt", "nv12", "-"]
    dec = subprocess.Popen(
        dec_cmd,
        cwd=segdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    venc = cfg.get("venc", "h264_nvenc")
    venc_args = cfg.get("venc_args") or ["-b:v", vb]   # 完整编码参数，缺省回退固定码率
    # 缩放放在编码段(合成在原分辨率完成,emoji \move 坐标才不会错位)
    scale_args = []
    rs = cfg.get("resize")
    if rs and rs != f"{W}x{H}":
        rw, rh = rs.split("x"); scale_args = ["-vf", f"scale={rw}:{rh}"]
    try:
        enc = subprocess.Popen(
            [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "nv12",
             "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-", "-an", *scale_args,
             "-c:v", venc, *venc_args, "-f", "mpegts", os.path.basename(out_ts)],
            cwd=segdir,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        dec.kill()
        dec.wait()
        raise

    fb = W * H * 3 // 2
    read_q = queue.Queue(maxsize=QDEPTH); write_q = queue.Queue(maxsize=QDEPTH)
    pipeline_stop = threading.Event()
    pipeline_errors = []
    pipeline_error_lock = threading.Lock()
    stderr_tail = collections.deque(maxlen=80)
    last_read_progress = [time.monotonic()]
    last_write_progress = [time.monotonic()]
    pipeline_stall_seconds = 30.0

    def fail_pipeline(message):
        with pipeline_error_lock:
            if not pipeline_errors:
                pipeline_errors.append(str(message))
        pipeline_stop.set()

    def drain_stderr(proc, label):
        stream = proc.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    stderr_tail.append(f"{label}: {line}")
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def put_queue(target, item, *, encoder_side=False):
        while not pipeline_stop.is_set():
            try:
                target.put(item, timeout=0.5)
                return True
            except queue.Full:
                if encoder_side:
                    enc_rc = enc.poll()
                    if enc_rc is not None:
                        fail_pipeline(f"编码器提前退出，返回码 {enc_rc}")
                    elif time.monotonic() - last_write_progress[0] > pipeline_stall_seconds:
                        fail_pipeline(
                            f"编码器超过 {pipeline_stall_seconds:.0f} 秒未消费视频帧"
                        )
        return False

    def readexactly(rd, n):
        chunks = []; got = 0
        while got < n:
            ch = rd.read(n - got)
            if not ch: return None
            chunks.append(ch); got += len(ch)
        return chunks[0] if len(chunks) == 1 else b"".join(chunks)

    def reader():
        i = 0
        try:
            while not pipeline_stop.is_set():
                buf = readexactly(dec.stdout, fb)
                if buf is None:
                    break
                last_read_progress[0] = time.monotonic()
                if not put_queue(read_q, (i, buf)):
                    break
                i += 1
        except Exception as exc:
            fail_pipeline(f"读取解码帧失败: {exc}")
        finally:
            put_queue(read_q, None)

    def writer():
        try:
            while not pipeline_stop.is_set():
                try:
                    item = write_q.get(timeout=0.5)
                except queue.Empty:
                    enc_rc = enc.poll()
                    if enc_rc is not None:
                        fail_pipeline(f"编码器提前退出，返回码 {enc_rc}")
                    continue
                if item is None:
                    break
                try:
                    enc.stdin.write(item)
                    last_write_progress[0] = time.monotonic()
                except (BrokenPipeError, OSError) as exc:
                    fail_pipeline(f"写入编码器失败: {exc}")
                    break
        finally:
            try:
                enc.stdin.close()
            except Exception:
                pass

    rt = threading.Thread(target=reader, daemon=True, name="emoji-frame-reader")
    wt = threading.Thread(target=writer, daemon=True, name="emoji-frame-writer")
    dec_err_thread = threading.Thread(
        target=drain_stderr,
        args=(dec, "decoder"),
        daemon=True,
        name="emoji-decoder-stderr",
    )
    enc_err_thread = threading.Thread(
        target=drain_stderr,
        args=(enc, "encoder"),
        daemon=True,
        name="emoji-encoder-stderr",
    )
    rt.start(); wt.start(); dec_err_thread.start(); enc_err_thread.start()

    n = 0; last = 0.0
    bypass_emoji = os.environ.get("DMR_EMOJI_BYPASS") == "1"
    stats_path = os.environ.get("DMR_EMOJI_STATS")
    stats = {
        "frames": 0,
        "raster_events": len(emoji_events),
        "active_event_samples": 0,
        "max_active_events": 0,
        "bypass": bypass_emoji,
    }
    dec_rc = enc_rc = None
    try:
        while not pipeline_stop.is_set():
            try:
                item = read_q.get(timeout=0.5)
            except queue.Empty:
                if dec.poll() is not None and not rt.is_alive():
                    fail_pipeline(f"解码器提前退出，返回码 {dec.returncode}")
                elif time.monotonic() - last_read_progress[0] > pipeline_stall_seconds:
                    fail_pipeline(
                        f"解码器超过 {pipeline_stall_seconds:.0f} 秒未产生视频帧"
                    )
                continue
            if item is None:
                break
            i, buf = item
            t = i / fps
            active = [] if bypass_emoji else [
                ev for ev in emoji_events if ev["s"] <= t <= ev["e"]
            ]
            stats["active_event_samples"] += len(active)
            stats["max_active_events"] = max(stats["max_active_events"], len(active))
            if active:
                arr = np.frombuffer(buf, np.uint8).copy()
                Y = arr[:W * H].reshape(H, W)
                UV = arr[W * H:].reshape(H // 2, W // 2, 2)
                for ev in active:
                    sY, sU, sV, A, w, h, tbo, lbo, anchor_x_offset, anchor_y_offset = ev["_spr"]
                    p = (t - ev["s"]) / (ev["e"] - ev["s"])
                    if ev.get("anchor") == "top_left":
                        xa = int(round(ev["x0"] + (ev["x1"] - ev["x0"]) * p - anchor_x_offset))
                        ya = int(round(ev["y0"] - anchor_y_offset))
                    else:
                        xa = int(round(ev["x0"] + (ev["x1"] - ev["x0"]) * p)) - lbo
                        ya = int(round(ev["y0"]) - tbo)
                    sx0 = max(0, -xa); sy0 = max(0, -ya); dx0 = max(0, xa); dy0 = max(0, ya)
                    cw = min(w - sx0, W - dx0); ch = min(h - sy0, H - dy0)
                    if cw <= 0 or ch <= 0: continue
                    aR = A[sy0:sy0 + ch, sx0:sx0 + cw] * ev["op"]
                    Yreg = Y[dy0:dy0 + ch, dx0:dx0 + cw].astype(np.float32)
                    Y[dy0:dy0 + ch, dx0:dx0 + cw] = (sY[sy0:sy0 + ch, sx0:sx0 + cw] * aR + Yreg * (1 - aR)).astype(np.uint8)
                    uy, ux = dy0 // 2, dx0 // 2
                    aSub = aR[::2, ::2]; sUs = sU[sy0:sy0 + ch:2, sx0:sx0 + cw:2]; sVs = sV[sy0:sy0 + ch:2, sx0:sx0 + cw:2]
                    Ud = UV[uy:uy + aSub.shape[0], ux:ux + aSub.shape[1], 0]
                    Vd = UV[uy:uy + aSub.shape[0], ux:ux + aSub.shape[1], 1]
                    hh = min(aSub.shape[0], sUs.shape[0], Ud.shape[0]); ww = min(aSub.shape[1], sUs.shape[1], Ud.shape[1])
                    if hh <= 0 or ww <= 0: continue
                    a2 = aSub[:hh, :ww]
                    Ud[:hh, :ww] = (sUs[:hh, :ww] * a2 + Ud[:hh, :ww].astype(np.float32) * (1 - a2)).astype(np.uint8)
                    Vd[:hh, :ww] = (sVs[:hh, :ww] * a2 + Vd[:hh, :ww].astype(np.float32) * (1 - a2)).astype(np.uint8)
                output_frame = arr.tobytes()
            else:
                output_frame = buf
            if not put_queue(write_q, output_frame, encoder_side=True):
                break
            n += 1
            stats["frames"] = n
            now = time.time()
            if now - last >= 0.4:
                last = now
                with open(prog_file, "w") as progress:
                    progress.write(str(n))

        if not pipeline_stop.is_set():
            sentinel_sent = put_queue(write_q, None, encoder_side=True)
            if sentinel_sent:
                wt.join(timeout=pipeline_stall_seconds)
                if wt.is_alive():
                    fail_pipeline(
                        f"编码线程超过 {pipeline_stall_seconds:.0f} 秒未结束"
                    )
    finally:
        if stats_path:
            try:
                with open(stats_path, "w", encoding="utf-8") as fp:
                    json.dump(stats, fp, ensure_ascii=False)
            except OSError:
                pass
        if pipeline_stop.is_set():
            for proc in (dec, enc):
                if proc.poll() is None:
                    proc.kill()
        try:
            dec.stdout.close()
        except Exception:
            pass
        rt.join(timeout=5)
        wt.join(timeout=5)
        for proc, label in ((dec, "解码器"), (enc, "编码器")):
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.wait()
                fail_pipeline(f"{label}退出超时，已强制结束")
            if proc is dec:
                dec_rc = rc
            else:
                enc_rc = rc
        dec_err_thread.join(timeout=2)
        enc_err_thread.join(timeout=2)

    if dec_rc != 0:
        fail_pipeline(f"解码器退出，返回码 {dec_rc}")
    if enc_rc != 0:
        fail_pipeline(f"编码器退出，返回码 {enc_rc}")
    if pipeline_errors:
        detail = pipeline_errors[0]
        if stderr_tail:
            detail += "\n" + "\n".join(stderr_tail)
        raise RuntimeError(detail)
    if n == 0:   # 一帧都没解出来(段文件不可读/解码失败)→ 判失败
        open(prog_file, "w").write("ERR")
        raise RuntimeError(f"该段解码 0 帧(seg 不可读?): {os.path.basename(seg_file)}")
    open(prog_file, "w").write(f"{n} done")
    return n


# ============================ 主流程 ============================
def check_tool(name):
    """Return whether an executable is available on PATH."""
    try:
        subprocess.run([name, "-version"], capture_output=True)
        return True
    except (FileNotFoundError, OSError):
        return False


def audio_codec(path):
    """Return the first audio stream codec, or an empty string on failure."""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


class EmoticonStore:
    """纯 emoticon 的本地表情库：按 unique 记账，图片按 URL 哈希落盘。"""
    MAX_BYTES = 10 * 1024 * 1024
    FORMAT_EXT = {"PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "JPEG": ".jpg"}

    def __init__(self, directory):
        self.directory = os.path.abspath(directory) if directory else None
        self.registry_path = os.path.join(self.directory, "emoticon.json") if self.directory else None
        self.registry = {}
        self.dirty = False
        self._resolved = {}
        if not self.directory:
            return
        os.makedirs(self.directory, exist_ok=True)
        try:
            with open(self.registry_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.registry = data
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("读取 emoticon.json 失败，将按空表情库处理: %s", e)

    @staticmethod
    def _normalized_url(url):
        url = str(url or "").strip()
        if not url:
            return ""
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https"):
            return ""
        # B站同一资源会混用 http/https，统一成 https 避免重复缓存。
        scheme = "https" if (p.hostname or "").lower().endswith("hdslb.com") else p.scheme
        return urllib.parse.urlunsplit((scheme, p.netloc, p.path, p.query, ""))

    @staticmethod
    def _key(meta, url):
        unique = str(meta.get("emoticon_unique") or "").strip()
        return unique or ("url_" + hashlib.sha256(url.encode("utf-8")).hexdigest())

    def _entry_path(self, entry):
        filename = os.path.basename(str(entry.get("file") or ""))
        return os.path.join(self.directory, filename) if filename else None

    def _download(self, url):
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host != "hdslb.com" and not host.endswith(".hdslb.com"):
            raise ValueError(f"拒绝非 B站图片域名: {host or url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > self.MAX_BYTES:
                raise ValueError(f"图片过大: {declared} bytes")
            data = resp.read(self.MAX_BYTES + 1)
        if len(data) > self.MAX_BYTES:
            raise ValueError(f"图片超过 {self.MAX_BYTES} bytes")

        with Image.open(io.BytesIO(data)) as im:
            image_format = (im.format or "").upper()
            im.verify()
        ext = self.FORMAT_EXT.get(image_format)
        if not ext:
            raise ValueError(f"不支持的图片格式: {image_format or 'unknown'}")

        filename = hashlib.sha256(url.encode("utf-8")).hexdigest() + ext
        target = os.path.join(self.directory, filename)
        temp = target + f".{os.getpid()}.part"
        try:
            with open(temp, "wb") as f:
                f.write(data)
            os.replace(temp, target)
        finally:
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except OSError:
                pass
        return filename, target

    def resolve(self, meta):
        """返回该条纯 emoticon 的精确本地图片；无法补齐时返回 None。"""
        if not self.directory or not isinstance(meta, dict):
            return None
        url = self._normalized_url(meta.get("url"))
        key = self._key(meta, url)
        memo_key = (key, url)
        if memo_key in self._resolved:
            return self._resolved[memo_key]

        entry = self.registry.get(key)
        if isinstance(entry, dict):
            path = self._entry_path(entry)
            entry_url = self._normalized_url(entry.get("url"))
            if path and os.path.isfile(path) and (not url or entry_url == url):
                self._resolved[memo_key] = path
                return path

        if not url:
            log.warning("emoticon 缺少可用 URL 且本地未命中: %s", meta.get("name") or key)
            self._resolved[memo_key] = None
            return None

        try:
            filename, path = self._download(url)
        except Exception as e:
            log.warning("emoticon 下载失败 %s: %s", meta.get("name") or key, e)
            self._resolved[memo_key] = None
            return None

        self.registry[key] = {
            "name": meta.get("name"),
            "url": url,
            "width": meta.get("width"),
            "height": meta.get("height"),
            "is_dynamic": meta.get("is_dynamic", 0),
            "file": filename,
        }
        self.dirty = True
        self._resolved[memo_key] = path
        log.info("已补齐 emoticon: %s", meta.get("name") or key)
        return path

    def save(self):
        if not self.directory or not self.dirty:
            return
        # 写入前重新合并磁盘内容，降低多个渲染任务同时补表情时互相覆盖的概率。
        merged = {}
        try:
            with open(self.registry_path, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                merged.update(old)
        except Exception:
            pass
        merged.update(self.registry)
        temp = self.registry_path + f".{os.getpid()}.tmp"
        try:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            os.replace(temp, self.registry_path)
        finally:
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except OSError:
                pass


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--worker" in sys.argv:
        cfgpath = sys.argv[sys.argv.index("--worker") + 1]
        try:
            with open(cfgpath, encoding="utf-8") as f:
                cfg = json.load(f)
            render_segment(cfg)
        except Exception:
            import traceback; traceback.print_exc()
            try:
                with open(cfgpath, encoding="utf-8") as f:
                    pf = json.load(f).get("prog")
                if pf: open(pf, "w").write("ERR")
            except Exception:
                pass
            sys.exit(1)
        return

    # 无参数运行时进入项目根目录的交互式 JSONL 多视频入口。
    # 带 video + ASS/RenderPlan + output 的内部调用仍走下面原有渲染路径。
    if not args:
        interactive_script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "render_jsonl_emoji.py")
        )
        raise SystemExit(subprocess.call([sys.executable, interactive_script]))

    setup_log()
    if len(args) < 3:
        print(__doc__); sys.exit(1)
    flv, ass, out_mp4 = os.path.abspath(args[0]), os.path.abspath(args[1]), os.path.abspath(args[2])
    def opt(name, default=None):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
    for t in (FFMPEG, FFPROBE):
        if not check_tool(t):
            log.error("找不到 %s,请确认已安装并加入 PATH(或放到脚本目录)。", t); sys.exit(1)
    vb = opt("--vb", "8M"); ab = opt("--ab", "160K")
    vencoder = opt("--vencoder", "h264_nvenc")
    aencoder = opt("--aencoder", "aac")
    # 完整编码器参数（与 DmRender 一致）；未传时为 None，下游回退到 -b:v / -b:a
    _va = opt("--venc-args"); venc_args = json.loads(_va) if _va else None
    _aa = opt("--aenc-args"); aenc_args = json.loads(_aa) if _aa else None
    use_hwaccel = "--no-hwaccel" not in sys.argv
    resize = opt("--resize")   # "宽x高" 或 缩放系数(如 0.5);留空=原分辨率
    subtitle_ass = opt("--subtitle")   # ASR 字幕 ass（可选）；和弹幕一起在同一遍 libass 烧
    emoji_font = resolve_emoji_font(opt("--emoji-font"))
    try:   # 位图字体(如 Noto)按 strike 缩放,较慢且略糊;矢量(Segoe)更快更清晰
        ImageFont.truetype(emoji_font, 32)
    except OSError:
        log.warning("%s 是位图字体(按 strike 缩放,较慢/略糊);追求速度与清晰可用矢量 Segoe UI Emoji(默认)。",
                    os.path.basename(emoji_font))
    pack = load_pack(opt("--emoji-pack"))
    emoticon_store = EmoticonStore(opt("--emoticon-dir"))
    for pth in (flv, ass):
        if not os.path.exists(pth):
            log.error("找不到文件: %s", pth); sys.exit(1)

    work = os.path.join(os.path.dirname(out_mp4) or ".", "_mt_" + os.path.splitext(os.path.basename(out_mp4))[0])
    if os.path.exists(work):
        if not rmtree_retry(work):
            log.error("无法清理上次残留的临时目录，终止本次渲染: %s", work)
            sys.exit(1)
        log.debug("已清理上次残留的临时目录: %s", work)
    os.makedirs(work, exist_ok=True)
    work_active = True
    child_processes = []
    cleanup_handles = []

    def cleanup_work():
        nonlocal work_active
        if not work_active:
            return
        running_children = [
            process for process in child_processes
            if process and process.poll() is None
        ]
        for process in running_children:
            kill_tree(process.pid)
        if running_children:
            wait_dead(running_children)
        for handle in cleanup_handles:
            try:
                if handle and not handle.closed:
                    handle.close()
            except Exception:
                pass
        if rmtree_retry(work):
            work_active = False
            log.debug("已清理 emoji 渲染临时目录: %s", work)

    # sys.exit、普通未捕获异常都会走 atexit；taskkill/进程崩溃则由父进程兜底清理。
    atexit.register(cleanup_work)

    import av
    c = av.open(flv); v = c.streams.video[0]
    source_W, source_H = v.codec_context.width, v.codec_context.height
    forced_canvas = opt("--canvas-resolution")
    if forced_canvas:
        try:
            W, H = (
                int(value)
                for value in forced_canvas.lower().split("x", 1)
            )
            if W <= 0 or H <= 0:
                raise ValueError
        except (TypeError, ValueError):
            log.error("--canvas-resolution must use a positive WIDTHxHEIGHT")
            sys.exit(2)
    else:
        W, H = resolve_render_canvas(
            (source_W, source_H),
            rescale_to_1080p="--rescale-to-1080p" in sys.argv,
        )
    render_plan_data = None
    render_plan_duration = 0.0
    if "--render-plan" in sys.argv:
        with open(ass, "r", encoding="utf-8") as fp:
            render_plan_data = json.load(fp)
        if int(render_plan_data.get("schema", 0)) != 1:
            log.error("不支持的 RenderPlan schema: %s", render_plan_data.get("schema"))
            sys.exit(1)
        try:
            render_plan_duration = max(
                0.0,
                float(render_plan_data.get("duration", 0) or 0),
            )
        except (TypeError, ValueError):
            render_plan_duration = 0.0
        generated_ass = os.path.join(work, "render_plan.ass")
        render_plan_to_ass(
            render_plan_data,
            generated_ass,
            include_raster_text=False,
            width=W,
            height=H,
        )
        ass = generated_ass
    # Keep frame-rate selection identical between the old and GPU renderers.
    source_fps = source_frame_rate(flv)
    minimum_fps = None
    if opt("--min-fps"):
        try:
            minimum_fps = float(opt("--min-fps"))
        except (TypeError, ValueError):
            log.error("--min-fps must be a positive number")
            sys.exit(2)
        if not np.isfinite(minimum_fps) or minimum_fps <= 0:
            log.error("--min-fps must be a positive number")
            sys.exit(2)
    try:
        fps = resolve_output_fps(source_fps, minimum_fps)
    except ValueError as exc:
        log.error("invalid frame rate: %s", exc)
        sys.exit(2)
    if minimum_fps is not None and fps > source_fps:
        log.info("源帧率 %.3f 低于最低 %.3f，将补帧到 %.3f fps",
                 source_fps, minimum_fps, fps)
    format_name = str(getattr(c.format, "name", "") or "").lower()
    is_flv = "flv" in format_name.split(",") or os.path.splitext(flv)[1].lower() == ".flv"

    def _normalize_duration(raw_duration, start_time):
        """直播 FLV 常保留上游绝对时间戳，demuxer 的 duration 实际是末尾 PTS。"""
        if is_flv and start_time > 0 and raw_duration > start_time:
            fixed = raw_duration - start_time
            log.debug("FLV 时长按时间戳区间修正: %.3f - %.3f = %.3f 秒",
                      raw_duration, start_time, fixed)
            return fixed
        return raw_duration

    raw_duration = float(c.duration) / 1e6 if c.duration else 0
    start_time = float(c.start_time) / 1e6 if c.start_time else 0
    dur = _normalize_duration(raw_duration, start_time)
    c.close()
    if not dur:
        def _probe(entries, sel=None):
            cmd = [FFPROBE, "-v", "error"]
            if sel: cmd += ["-select_streams", sel]
            cmd += ["-show_entries", entries, "-of", "csv=p=0", flv]
            try: return float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())
            except Exception: return 0
        raw_duration = _probe("format=duration")
        start_time = _probe("format=start_time")
        dur = _normalize_duration(raw_duration, start_time) if raw_duration else 0
        dur = dur or _probe("stream=duration", "v:0")
        if not dur:
            try:
                d = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                                    "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", flv],
                                   capture_output=True, text=True)
                dur = int(d.stdout.strip().splitlines()[-1]) / fps
            except Exception:
                dur = 0
    if render_plan_duration > 0:
        if dur > 0 and abs(render_plan_duration - dur) >= 0.05:
            log.debug(
                "使用RenderPlan统一媒体时长: 探测%.3fs -> 计划%.3fs",
                dur,
                render_plan_duration,
            )
        dur = render_plan_duration
    total = int(dur * fps) if dur else 0

    # 输出分辨率(--resize: "宽x高" 或缩放系数);与原分辨率相同则视为不缩放
    resize_str = None
    if resize:
        if "x" in str(resize).lower():
            rw, rh = str(resize).lower().split("x")[:2]; resize_str = f"{int(rw)}x{int(rh)}"
        else:
            sc = float(resize); resize_str = f"{int(W*sc)}x{int(H*sc)}"
        if resize_str == f"{W}x{H}": resize_str = None

    # 解析 ASS：R2L 样式(字体名/字号/粗体) + 拆 textonly / 特殊弹幕
    txt = open(ass, encoding="utf-8").read().splitlines()
    fontname, fs, bold = "Microsoft YaHei", 60, True
    for l in txt:
        if l.startswith("Style: R2L"):
            ps = l[len("Style:"):].split(",")
            fontname = ps[1].strip(); fs = int(ps[2]); bold = (ps[7].strip() == "-1")
            break
    textfont = resolve_font(fontname, bold)
    canvas_res = (
        f" 画布{W}x{H}"
        if (W, H) != (source_W, source_H)
        else ""
    )
    out_res = f" 输出{resize_str}" if resize_str else ""
    fps_text = (
        f"{source_fps:.3g}→{fps:.3g}fps"
        if abs(fps - source_fps) > 1e-6
        else f"{source_fps:.3g}fps"
    )
    log.info(
        "输入: %s %dx%d@%s 时长%s%s%s | 整段单路渲染 硬解%s",
        os.path.basename(flv),
        source_W,
        source_H,
        fps_text,
        fmt(dur),
        canvas_res,
        out_res,
        use_hwaccel,
    )
    log.debug("文字字体: %s%s -> %s | emoji字体: %s | 表情包: %d个",
              fontname, "(粗)" if bold else "", os.path.basename(textfont),
              os.path.basename(emoji_font), len(pack))

    header = [l for l in txt if not l.startswith("Dialogue")]
    dia_textonly = []
    emoji_all = []
    if render_plan_data is not None:
        try:
            for planned in render_plan_data.get("events") or []:
                if planned.get("mode") != "raster":
                    continue
                path_points = planned.get("path") or []
                if len(path_points) < 2:
                    continue
                visual = planned.get("visual") or {}
                style = visual.get("style") or {}
                image_pack = {}
                for token, asset in (visual.get("images") or {}).items():
                    local_path = asset.get("path")
                    if not local_path and asset.get("url"):
                        local_path = emoticon_store.resolve(asset)
                    name = str(token).strip()
                    if name.startswith("[") and name.endswith("]"):
                        name = name[1:-1]
                    if name and local_path:
                        image_pack[name.lower()] = local_path
                color = str(style.get("color", "#FFFFFF")).lstrip("#")
                if len(color) != 6:
                    color = "FFFFFF"
                outline_color = str(style.get("outline_color", "#000000")).lstrip("#")
                if len(outline_color) != 6:
                    outline_color = "000000"
                pill = visual.get("pill")
                if pill:
                    pill_color = str(pill.get("color", "#FFC0CB")).lstrip("#")
                    if len(pill_color) != 6:
                        pill_color = "FFC0CB"
                    pill = {
                        "color": [
                            int(pill_color[0:2], 16),
                            int(pill_color[2:4], 16),
                            int(pill_color[4:6], 16),
                        ],
                        "pad": int(pill.get("padding", pill.get("pad", 0)) or 0),
                        "border": max(1, int(pill.get("border", 2) or 1)),
                    }
                emoji_all.append({
                    "s": float(path_points[0][0]),
                    "e": float(path_points[-1][0]),
                    "x0": float(path_points[0][1]),
                    "y0": float(path_points[0][2]),
                    "x1": float(path_points[-1][1]),
                    "text": str(visual.get("text", "")).translate(_STRIP),
                    "rgb": [
                        int(color[0:2], 16),
                        int(color[2:4], 16),
                        int(color[4:6], 16),
                    ],
                    "op": float(style.get("opacity", 0.8) or 0.8),
                    "outline": float(style.get("outline", OUT) or 0),
                    "outline_rgb": [
                        int(outline_color[0:2], 16),
                        int(outline_color[2:4], 16),
                        int(outline_color[4:6], 16),
                    ],
                    "pill": pill,
                    # images 非空却没有解析出本地文件时传空 dict，禁止错误地
                    # 回退到同名的通用 emoji_pack 图片。
                    "emoticon_pack": (
                        image_pack if visual.get("images") else None
                    ),
                    "anchor": "top_left",
                    "bounds": planned.get("bounds") or {},
                })
            for line in txt:
                if not line.startswith("Dialogue"):
                    continue
                fields = line.split(",", 9)
                dia_textonly.append((ass_time(fields[1]), ass_time(fields[2]), line))
        except Exception as exc:
            log.error("读取 RenderPlan 失败: %s", exc)
            sys.exit(1)
    else:
        # 旧 ASS 兼容：没有显式计划时，仍从 ASS 注释和 Dialogue 解析图片事件。
        drop_pill = False
        pending_emoticon = None
        for l in txt:
            if l.startswith(EMOTICON_PREFIX):
                try:
                    meta = json.loads(l[len(EMOTICON_PREFIX):].strip())
                    path = emoticon_store.resolve(meta)
                    name = str(meta.get("name") or "")
                    if name.startswith("[") and name.endswith("]"):
                        name = name[1:-1]
                    pending_emoticon = {
                        "meta": meta,
                        "pack": ({name.lower(): path} if name and path else {}),
                    }
                except Exception as e:
                    log.warning("忽略损坏的 DMR_EMOTICON 注释: %s", e)
                    pending_emoticon = {"meta": {}, "pack": {}}
                continue
            if not l.startswith("Dialogue"):
                continue
            event_emoticon = pending_emoticon
            pending_emoticon = None
            f = l.split(",", 9)
            st, et = ass_time(f[1]), ass_time(f[2])
            effect = f[8].strip() if len(f) > 8 else ""
            if effect == "pilldraw":
                if drop_pill:
                    drop_pill = False
                    continue
                dia_textonly.append((st, et, l))
                continue
            event_pack = event_emoticon["pack"] if event_emoticon is not None else None
            active_pack = pack if event_pack is None else event_pack
            if f[3] == "R2L" and has_special(f[9], active_pack):
                name = f[4].strip() if len(f) > 4 else ""
                pill = parse_pill(name)
                body = f[9]
                mv = re.search(r"\\move\(([^)]*)\)", body)
                if mv:
                    x0, y0, x1, y1 = [float(x) for x in mv.group(1).split(",")[:4]]
                    cm = re.search(r"\\1c&H([0-9A-Fa-f]{6})&", body)
                    rgb = [int(cm.group(1)[4:6],16), int(cm.group(1)[2:4],16), int(cm.group(1)[0:2],16)] if cm else [255,255,255]
                    am = re.search(r"\\alpha&H([0-9A-Fa-f]{2})&", body)
                    aa = int(am.group(1),16) if am else 0x39
                    text = re.sub(r"\{[^}]*\}", "", body)
                    text = text.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip().translate(_STRIP)
                    emoji_all.append(dict(
                        s=st, e=et, x0=x0, y0=y0, x1=x1, text=text,
                        rgb=rgb, op=(255-aa)/255, pill=pill,
                        emoticon_pack=event_pack,
                    ))
                    if pill:
                        drop_pill = True
                    continue
            dia_textonly.append((st, et, l))
    emoticon_store.save()
    log.debug("特殊弹幕(emoji/表情): %d 行", len(emoji_all))

    # 字幕 ass（可选）：解析成 header + dialogue 列表，后面按段切分、与弹幕同遍 libass 烧
    sub_header, sub_dia = [], []
    if subtitle_ass and os.path.exists(subtitle_ass):
        stx = open(subtitle_ass, encoding="utf-8").read().splitlines()
        sub_header = [l for l in stx if not l.startswith("Dialogue")]
        for l in stx:
            if not l.startswith("Dialogue"):
                continue
            sf = l.split(",", 9)
            sub_dia.append((ass_time(sf[1]), ass_time(sf[2]), l))
        log.debug("字幕: %d 行", len(sub_dia))

    # 不再用 segment/reset_timestamps 物理切割输入。唯一 worker 直接读取整段
    # 原视频，让 fps 滤镜拥有完整时间戳上下文并在原位置补齐空洞。
    segs = [(flv, 0.0, dur)]
    log.debug("不切段：直接连续解码整段原视频")

    jobs = []
    for idx, (segfile, sstart, send) in enumerate(segs):
        off = sstart
        lines = list(header)
        for st, et, l in dia_textonly:
            if et <= off or st >= send: continue
            f = l.split(",", 9)
            nst, net = st - off, et - off
            body = f[9]
            if nst < 0:
                # 跨段进来:开始时间被钳到 0。\move 和 \fad 都相对"开始时间"，必须按"到本段起点已过的时间 elapsed"
                # 重定，否则会重新滑入(move)或重新淡入闪一下(fad)。
                elapsed = (off - st) * 1000.0   # 毫秒
                # --- \move：同时匹配 4 参数 \move(x0,y0,x1,y1) 和 6 参数 \move(...,t0,t1)(SC/醒目留言用后者) ---
                mv = re.search(r"\\move\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*(?:,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*)?\)", body)
                if mv and et > st:
                    x0, y0, x1, y1 = (float(mv.group(i)) for i in range(1, 5))
                    if mv.group(5) is not None:
                        # 6 参数:运动只在 [t0,t1](毫秒)内发生。按 elapsed 重算位置和剩余时间。
                        t0, t1 = float(mv.group(5)), float(mv.group(6))
                        if elapsed >= t1:                       # 运动早已结束 → 本段静止在终点(SC 的情形)
                            new_mv = f"\\pos({x1:.0f},{y1:.0f})"
                        elif elapsed <= t0:                     # 还没开始 → 起点不变，时间整体前移
                            new_mv = f"\\move({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f},{t0-elapsed:.0f},{t1-elapsed:.0f})"
                        else:                                    # 运动到一半 → 起点 lerp 到当前位置，剩余时间从 0 起
                            fr = (elapsed - t0) / (t1 - t0)
                            cx = x0 + (x1 - x0) * fr; cy = y0 + (y1 - y0) * fr
                            new_mv = f"\\move({cx:.0f},{cy:.0f},{x1:.0f},{y1:.0f},0,{t1-elapsed:.0f})"
                        body = body[:mv.start()] + new_mv + body[mv.end():]
                    else:
                        # 4 参数:运动跨整条弹幕时长(ASS 默认) → 按时长比例算起点(原逻辑)
                        frac = (off - st) / (et - st)
                        nx0 = x0 + (x1 - x0) * frac; ny0 = y0 + (y1 - y0) * frac
                        body = body[:mv.start()] + f"\\move({nx0:.0f},{ny0:.0f},{x1:.0f},{y1:.0f})" + body[mv.end():]
                # --- \fad(淡入,淡出)：淡入相对开始 → 扣掉 elapsed(SC 早已淡完→变 0 不再淡)；淡出相对结束 → 不动 ---
                fd = re.search(r"\\fad\(\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*\)", body)
                if fd:
                    fin, fout = float(fd.group(1)), float(fd.group(2))
                    new_fin = max(0.0, fin - elapsed)
                    body = body[:fd.start()] + f"\\fad({new_fin:.0f},{fout:.0f})" + body[fd.end():]
                nst = 0.0
            f[1] = sec_to_ass(nst); f[2] = sec_to_ass(net); f[9] = body
            lines.append(",".join(f))
        ass_k = os.path.join(work, f"textonly_{idx:03d}.ass")
        open(ass_k, "w", encoding="utf-8").write("\n".join(lines))
        # 字幕按段切分 + 重定时（静态字幕，无 \move，只需窗口过滤 + 平移时间）
        sub_k = None
        if sub_dia:
            slines = list(sub_header)
            for st, et, l in sub_dia:
                if et <= off or st >= send:
                    continue
                sf = l.split(",", 9)
                sf[1] = sec_to_ass(max(0.0, st - off)); sf[2] = sec_to_ass(et - off)
                slines.append(",".join(sf))
            sub_k = os.path.join(work, f"subtitle_{idx:03d}.ass")
            open(sub_k, "w", encoding="utf-8").write("\n".join(slines))
        ev_k = []
        for ev in emoji_all:
            if ev["e"] <= off or ev["s"] >= send: continue
            e2 = dict(ev); e2["s"] = ev["s"] - off; e2["e"] = ev["e"] - off; ev_k.append(e2)
        out_ts = os.path.join(work, f"done_{idx:03d}.ts")
        prog = os.path.join(work, f"prog_{idx:03d}.txt"); open(prog, "w").write("0")
        cfg = dict(seg=os.path.join(work, segfile), textonly=ass_k, subtitle=sub_k, out=out_ts, prog=prog,
                   W=W, H=H, fps=fps, vb=vb, venc=vencoder, venc_args=venc_args, hwaccel=use_hwaccel, fs=fs,
                   resize=resize_str,
                   textfont=textfont, emojifont=emoji_font, pack=pack, events=ev_k)
        cfg_path = os.path.join(work, f"cfg_{idx:03d}.json")
        # ensure_ascii=True：emoji 写成 \uXXXX 纯 ASCII，彻底杜绝读取时的编码错误
        with open(cfg_path, "w", encoding="utf-8") as fcfg:
            json.dump(cfg, fcfg)
        jobs.append(dict(idx=idx, cfg=cfg_path, prog=prog))

    # 源流音频可能带有断裂/跳跃时间戳。视频经过 fps 后会形成连续时间轴，
    # 因此不能再把原 AAC 直接 copy 回去，否则两套时间轴长时间播放后可能失步。
    # 音频修复和视频 worker 并行执行，避免把重编码耗时串行加在渲染末尾。
    fixed_audio = None
    audio_log_path = os.path.join(work, "audio_fix.log")
    audio_proc = None
    audio_log = None
    if audio_codec(flv):
        audio_encode_args = list(aenc_args) if aenc_args is not None else ["-b:a", ab]
        audio_encoder = aencoder if aencoder != "copy" else "aac"
        # AAC 用 M4A 保留精确采样时间基，避免 Matroska 毫秒时间基在片尾多出约 0.1 秒；
        # 其他编码器使用兼容范围更广的 MKA。
        audio_ext = ".m4a" if "aac" in audio_encoder.lower() else ".mka"
        fixed_audio = os.path.join(work, "fixed_audio" + audio_ext)
        audio_cmd = [
            FFMPEG, "-v", "error", "-y", "-nostdin",
            "-fflags", "+discardcorrupt+genpts",
            "-i", flv, "-map", "0:a:0", "-vn",
            "-af", "aresample=async=1000:first_pts=0",
            "-c:a", audio_encoder, *audio_encode_args,
            "-ar", "48000", fixed_audio,
        ]
        audio_log = open(audio_log_path, "w", encoding="utf-8", errors="replace")
        cleanup_handles.append(audio_log)
        audio_proc = subprocess.Popen(audio_cmd, stdout=subprocess.DEVNULL, stderr=audio_log)
        child_processes.append(audio_proc)
        log.debug("音频时间轴修复已并行启动")
    else:
        log.debug("输入没有音轨，跳过音频时间轴修复")

    log.debug("开始整段单路渲染（Ctrl+C 停止）...")
    procs = {}; pending = list(jobs); running = []
    t0 = time.time(); CREATE_GROUP = 0x00000200 if os.name == "nt" else 0
    def launch(job):
        process = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--worker", job["cfg"]],
            creationflags=CREATE_GROUP,
        )
        child_processes.append(process)
        return process
    def killall():
        for pr in procs.values():
            kill_tree(pr.pid)
        if audio_proc and audio_proc.poll() is None:
            kill_tree(audio_proc.pid)

    STALL = 180   # 渲染进度 180 秒无推进 → 判定卡死并终止
    failed = set()
    pstate = {}   # idx -> [last_n, last_t]
    _prog_last = {}
    def prog_n(job):
        try:
            v = int(open(job["prog"]).read().split()[0]); _prog_last[job["idx"]] = v; return v
        except Exception:
            return _prog_last.get(job["idx"], 0)   # 偶发读失败时返回上次值,避免进度回跳
    def prog_done(job):
        try: return open(job["prog"]).read().strip().endswith("done")
        except Exception: return False
    try:
        last = 0.0
        while pending or running:
            while pending and not running:
                job = pending.pop(0); pr = launch(job); procs[job["idx"]] = pr
                pstate[job["idx"]] = [0, time.time()]; running.append((job, pr))
            still = []
            for job, pr in running:
                rc = pr.poll()
                if rc is None:
                    n_now = prog_n(job); st = pstate[job["idx"]]
                    if n_now > st[0]: st[0] = n_now; st[1] = time.time()
                    elif time.time() - st[1] > STALL:
                        kill_tree(pr.pid)
                        failed.add(job["idx"]); continue
                    still.append((job, pr))
                else:   # 已退出：检查是否正常完成
                    if not prog_done(job): failed.add(job["idx"])
            running = still
            now = time.time()
            if now - last >= 0.5:
                last = now
                done = sum(prog_n(job) for job in jobs)
                el = now - t0; cur = done / el if el > 0 else 0
                fl = " 渲染失败" if failed else ""
                # 被 DMR 以管道捕获时只发机读进度行（供 WebUI 显示百分比），
                # 不刷进度条——那是给交互终端看的,进了日志只会刷屏。
                tty = sys.stdout.isatty()
                if total:
                    dtot = max(total, done)   # 估算帧数偏少时动态抬高,避免显示超过100%/负剩余
                    pct = done / dtot * 100; eta = max(0.0, (dtot - done) / cur) if cur > 0 else 0; bn = int(pct / 100 * 20)
                    if tty:
                        sys.stdout.write(f"\r[{'#'*bn}{'-'*(20-bn)}] {pct:5.1f}%  {done}/{dtot}帧  {cur:.0f}fps  已用{fmt(el)} 剩~{fmt(eta)}{fl}   ")
                    else:
                        sys.stdout.write(f"[DMR_PROGRESS] {pct:.1f}\n")
                elif tty:
                    sys.stdout.write(f"\r{done}帧  {cur:.0f}fps  已用{fmt(el)}{fl}   ")
                sys.stdout.flush()
            time.sleep(0.2)
    except KeyboardInterrupt:
        log.info("收到停止信号,结束所有子进程...")
        killall(); wait_dead(procs.values())
        if audio_proc:
            wait_dead([audio_proc])
        if audio_log and not audio_log.closed:
            audio_log.close()
        cleanup_work()
        log.info("已停止。"); return
    # 混音/清理之前必须确认 worker 全退干净,否则句柄还占着文件
    for pr in procs.values():
        try: pr.wait(timeout=5)
        except Exception: kill_tree(pr.pid)
    wait_dead(procs.values())
    audio_rc = 0
    if audio_proc:
        try:
            audio_rc = audio_proc.wait()
        finally:
            if audio_log:
                audio_log.close()
    if sys.stdout.isatty():
        print()   # 给进度条收尾换行,免得后面的日志接在同一行

    # 校验唯一的视频结果，失败时不产出残缺视频。
    missing = [j["idx"] for j in jobs if not os.path.exists(os.path.join(work, f"done_{j['idx']:03d}.ts"))]
    bad = sorted(failed | set(missing))
    if bad:
        log.error("整段视频渲染失败")
        sys.exit(1)
    if audio_proc and (
        audio_rc != 0
        or not os.path.exists(fixed_audio)
        or not audio_codec(fixed_audio)
    ):
        detail = ""
        try:
            detail = open(audio_log_path, encoding="utf-8", errors="replace").read().strip()
        except Exception:
            pass
        # A truncated live fragment can expose an AAC stream in its header but
        # contain no decodable audio packets.  The video is still renderable;
        # fall back to a video-only result instead of mapping an empty M4A.
        log.warning("音频时间轴修复未产生有效音轨，改为输出无音轨视频%s",
                    f": {detail[-500:]}" if detail else "")
        audio_proc = None
        fixed_audio = None

    # 单路模式只有一个视频结果，不再生成 concat.txt，也不做无意义的单段拼接。
    full = os.path.join(work, "done_000.ts")
    log.debug("视频渲染完成，混音中...")
    if audio_proc:
        # 音频已在视频渲染期间完成修复和编码，此处只需快速封装。
        mux_cmd = [
            FFMPEG, "-v", "error", "-y",
            "-i", full, "-i", fixed_audio,
            "-map", "0:v", "-map", "1:a:0",
            "-c", "copy", "-shortest",
        ]
    else:
        mux_cmd = [
            FFMPEG, "-v", "error", "-y",
            "-i", full, "-map", "0:v",
            "-c:v", "copy",
        ]
    if render_plan_duration > 0:
        mux_cmd += ["-t", f"{render_plan_duration:.6f}"]
    mux_cmd.append(out_mp4)
    r = subprocess.run(mux_cmd)
    dt = time.time() - t0
    if r.returncode == 0:
        cleanup_work()
        real_frames = sum(prog_n(job) for job in jobs) or total   # 用真实渲染帧数算平均,更准
        avg = real_frames / dt if dt > 0 else 0
        log.info("完成！视频时长 %s，用时 %s（平均 %.0ffps）,输出: %s",
                 fmt(dur), fmt(dt), avg, out_mp4)
    else:
        log.error("混音失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
