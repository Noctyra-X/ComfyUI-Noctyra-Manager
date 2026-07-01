# ComfyUI-Noctyra-Manager
# Copyright (C) 2026 Noctyra
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
跨进程操作锁

解决的场景：同时开着 ComfyUI（集成模式）和独立模式服务时，两个进程都访问同一个
`model_cache.sqlite`，各自的 `asyncio.Lock` 互相不可见。用户在两边都点"扫描"
会导致一个排队等 SQLite 锁、超时失败。

实现：每个重操作（scan / match / prewarm）在 `.cache/` 里写一个 lock 文件
`.lock-<op>`，内容是 JSON `{pid, hostname, started_at, op}`。开始前读一下，若
- 文件不存在 → 直接获取
- 文件存在但 `started_at` 早于 `STALE_AFTER_SECONDS` → 视为遗弃，覆盖
- 文件新鲜 → 返回 busy 信息（含持有者 PID）让调用方告诉用户"另一个进程正在跑"

注意：这是 **咨询锁**（advisory lock），没有 OS 级强制；只要所有访问方都走
acquire/release 就能协调。进程崩溃或 kill -9 时 lock 文件会残留，靠 stale
timeout 兜底。
"""

import json
import logging
import os
import socket
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("noctyra.process_lock")

# 超过此时间未释放的锁文件视为遗弃（进程崩溃 / kill -9）
# 10 分钟足够扫描/匹配/prewarm 正常完成；异常卡住的也超过这个时长就该清了
STALE_AFTER_SECONDS = 600


def _lock_path(cache_dir: str, op: str) -> str:
    """锁文件路径：<cache_dir>/.lock-<op>"""
    return os.path.join(cache_dir, f".lock-{op}")


def _read_lock(path: str) -> Optional[dict]:
    """读锁文件；不存在或损坏返回 None"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "started_at" in data:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _is_stale(lock_data: dict) -> bool:
    """已过期（> STALE_AFTER_SECONDS）视为遗弃"""
    try:
        started = float(lock_data.get("started_at", 0))
    except (TypeError, ValueError):
        return True
    return (time.time() - started) > STALE_AFTER_SECONDS


def acquire(cache_dir: str, op: str) -> Optional[dict]:
    """尝试获取 op 的跨进程锁。

    Returns:
        None             - 获取成功，调用方应在完成后调 release()
        dict (busy_info) - 已被占用，{pid, hostname, started_at, op, age_seconds}
    """
    os.makedirs(cache_dir, exist_ok=True)
    path = _lock_path(cache_dir, op)
    existing = _read_lock(path)
    if existing is not None and not _is_stale(existing):
        # 活锁存在，拒绝
        busy = dict(existing)
        busy["age_seconds"] = round(time.time() - float(existing.get("started_at", 0)), 1)
        return busy

    if existing is not None:
        logger.warning(
            "[Noctyra-MM] 遗弃锁被覆盖：%s（持有者 PID=%s，已 %.0fs 未更新）",
            path, existing.get("pid"),
            time.time() - float(existing.get("started_at", 0)),
        )

    # 原子写：先 .tmp 再 replace，防中途崩溃留半成品
    payload = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": time.time(),
        "op": op,
    }
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("[Noctyra-MM] 无法写锁文件 %s: %s；跳过跨进程锁", path, e)
        # 写锁文件失败不阻塞操作，只是失去跨进程保护
        return None
    return None


def release(cache_dir: str, op: str) -> None:
    """释放锁：仅当锁文件确属本进程（pid+hostname 匹配）时才删除。

    防止「本进程的锁超时→被另一进程抢占覆盖→本进程完成后误删抢占者的锁」——那会让
    第三方在抢占者仍在跑时拿到锁，恰好破坏长任务防护。锁文件已是别人的 → 不动它。"""
    path = _lock_path(cache_dir, op)
    data = _read_lock(path)
    if data is not None and (data.get("pid") != os.getpid()
                             or data.get("hostname") != socket.gethostname()):
        logger.warning("[Noctyra-MM] 释放锁时发现已被 PID=%s 抢占，跳过删除：%s",
                       data.get("pid"), path)
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug("[Noctyra-MM] 释放锁文件失败 %s: %s", path, e)


@contextmanager
def hold(cache_dir: str, op: str):
    """上下文管理器：with hold(cache_dir, 'scan') as busy: if busy: ...else: 干活。

    使用模式：
        busy = acquire(cache_dir, op)
        if busy is not None:
            return {"error": "busy", ...}
        try:
            ... 干活 ...
        finally:
            release(cache_dir, op)
    """
    busy = acquire(cache_dir, op)
    try:
        yield busy
    finally:
        if busy is None:
            release(cache_dir, op)


def format_busy_message(op: str, busy: dict) -> str:
    """格式化友好的 busy 提示给用户看"""
    pid = busy.get("pid", "?")
    host = busy.get("hostname", "?")
    age = busy.get("age_seconds", "?")
    return (
        f"另一个进程正在执行 {op} 操作（PID {pid} @ {host}，已运行 {age}s）。"
        f"请等待完成或等待其自然超时（>{STALE_AFTER_SECONDS//60} 分钟）。"
    )
