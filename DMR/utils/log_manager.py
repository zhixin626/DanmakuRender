from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from DMR.utils.console_buffer import install_console_tee

LOG_PATTERNS = ("DMR-*.log*", "console-*.txt")


def _logging_level(value, default):
    if isinstance(value, int):
        return value
    return getattr(logging, str(value or "").upper(), default)


def _safe_instance_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    value = re.sub(r"\s+", "_", value)
    value = value.strip(" ._-")
    return value or "default"


def _delete_log(path, current_log):
    try:
        if path.resolve() == current_log:
            return 0
        size = path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0


def _log_files(log_dir):
    return [
        path for pattern in LOG_PATTERNS
        for path in log_dir.glob(pattern)
        if path.is_file()
    ]


def cleanup_logs(log_dir, current_log, args, logger):
    """按保留天数和目录容量清理 DMR 主日志和控制台日志；其它文件不在清理范围内。"""
    now = time.time()
    retention_days = max(0.0, float(args.get("retention_days", 30) or 0))
    protected_days = max(0.0, float(args.get("protected_days", 7) or 0))
    max_total_bytes = max(
        0,
        int(float(args.get("max_total_size_mb", 200) or 0) * 1024 * 1024),
    )
    removed_count = 0
    removed_bytes = 0
    failures = []

    files = _log_files(log_dir)
    if retention_days:
        cutoff = now - retention_days * 86400
        for path in files:
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                removed_bytes += _delete_log(path, current_log)
                removed_count += int(not path.exists())
            except OSError as exc:
                failures.append((path, exc))

    files = _log_files(log_dir)
    total_bytes = 0
    existing_files = []
    for path in files:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append((path, exc))
            continue
        existing_files.append((path, stat))
        total_bytes += stat.st_size

    if max_total_bytes and total_bytes > max_total_bytes:
        protected_cutoff = now - protected_days * 86400
        candidates = []
        for path, stat in existing_files:
            try:
                if path.resolve() == current_log or stat.st_mtime >= protected_cutoff:
                    continue
                candidates.append((stat.st_mtime, path))
            except OSError as exc:
                failures.append((path, exc))
        for _, path in sorted(candidates):
            if total_bytes <= max_total_bytes:
                break
            try:
                freed = _delete_log(path, current_log)
                if freed:
                    total_bytes -= freed
                    removed_bytes += freed
                    removed_count += 1
            except OSError as exc:
                failures.append((path, exc))

    if removed_count:
        logger.debug(
            "日志清理完成: 删除%d个文件，释放%.2f MB",
            removed_count,
            removed_bytes / 1024 / 1024,
        )
    if failures:
        preview = "; ".join(f"{path}: {exc}" for path, exc in failures[:3])
        logger.warning("有%d个旧日志清理失败: %s", len(failures), preview)


def configure_logging(config_path, args):
    args = args or {}
    log_dir = Path(args.get("directory") or "./logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    # 必须在下面 StreamHandler(sys.stdout) 创建前接管 stdout，WebUI 日志页才能看到 logging 输出
    install_console_tee(log_dir)

    instance_name = args.get("instance_name")
    if not instance_name:
        config_parent = Path(config_path).resolve().parent
        instance_name = config_parent.name or Path(config_path).stem
    instance_name = _safe_instance_name(instance_name)

    timestamp = time.strftime("%Y%m%d-%H%M")
    pid = os.getpid()
    stem = f"DMR-{timestamp}-{instance_name}-p{pid}"
    log_path = log_dir / f"{stem}.log"
    collision = 2
    while log_path.exists():
        log_path = log_dir / f"{stem}-{collision}.log"
        collision += 1

    console_level = _logging_level(args.get("console_level"), logging.INFO)
    file_level = _logging_level(args.get("file_level"), logging.DEBUG)
    logger = logging.getLogger("DMR")
    logger.setLevel(min(console_level, file_level))

    # WebUI 会接管 stdout 并转发到日志页，因此必须明确写到 stdout。
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        logging.Formatter("[%(asctime)s][%(levelname)s]: %(message)s")
    )

    max_file_bytes = max(
        0,
        int(float(args.get("max_file_size_mb", 20) or 0) * 1024 * 1024),
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_file_bytes,
        backupCount=3 if max_file_bytes else 0,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s][%(name)s][%(levelname)s]: %(message)s"
        )
    )

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    current_log = log_path.resolve()
    cleanup_logs(log_dir, current_log, args, logger)

    interval_hours = max(
        0.0,
        float(args.get("cleanup_interval_hours", 24) or 0),
    )
    if interval_hours:
        def cleanup_loop():
            while True:
                time.sleep(interval_hours * 3600)
                cleanup_logs(log_dir, current_log, args, logger)

        threading.Thread(
            target=cleanup_loop,
            name="DMRLogCleanup",
            daemon=True,
        ).start()

    logger.debug(
        "日志实例: %s | PID: %d | 配置: %s | 文件: %s",
        instance_name,
        pid,
        Path(config_path).resolve(),
        current_log,
    )
    return logger
