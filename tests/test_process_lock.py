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
跨进程锁测试：acquire/release 语义、stale 覆盖、不同 op 互不影响。
"""

import json
import os
import time

from manager import process_lock


def test_acquire_release_cycle(tmp_path):
    cache = str(tmp_path)
    assert process_lock.acquire(cache, "scan") is None
    assert os.path.exists(os.path.join(cache, ".lock-scan"))

    # 活锁拒绝
    busy = process_lock.acquire(cache, "scan")
    assert busy is not None
    assert busy["pid"] == os.getpid()
    assert busy["op"] == "scan"

    # release
    process_lock.release(cache, "scan")
    assert not os.path.exists(os.path.join(cache, ".lock-scan"))

    # release 不存在的锁不报错
    process_lock.release(cache, "scan")


def test_different_ops_do_not_collide(tmp_path):
    cache = str(tmp_path)
    assert process_lock.acquire(cache, "scan") is None
    assert process_lock.acquire(cache, "match") is None
    assert process_lock.acquire(cache, "prewarm") is None

    # 同名都被占
    assert process_lock.acquire(cache, "scan") is not None
    assert process_lock.acquire(cache, "match") is not None


def test_stale_lock_is_overwritten(tmp_path):
    cache = str(tmp_path)
    stale = {
        "pid": 99999,
        "hostname": "gone",
        "started_at": time.time() - 9999,  # 远超 10 min 阈值
        "op": "scan",
    }
    with open(os.path.join(cache, ".lock-scan"), "w") as f:
        json.dump(stale, f)

    busy = process_lock.acquire(cache, "scan")
    assert busy is None  # 应被视为遗弃并覆盖


def test_fresh_lock_not_overwritten(tmp_path):
    cache = str(tmp_path)
    fresh = {
        "pid": 99999,
        "hostname": "other",
        "started_at": time.time() - 5,  # 5 秒前，新鲜
        "op": "scan",
    }
    with open(os.path.join(cache, ".lock-scan"), "w") as f:
        json.dump(fresh, f)

    busy = process_lock.acquire(cache, "scan")
    assert busy is not None
    assert busy["pid"] == 99999  # 保留原持有者信息
    assert busy["age_seconds"] >= 4  # 含年龄字段


def test_format_busy_message():
    msg = process_lock.format_busy_message("scan", {
        "pid": 123, "hostname": "host", "age_seconds": 45.0,
    })
    assert "PID 123" in msg
    assert "host" in msg
    assert "scan" in msg


def test_malformed_lock_file_treated_as_absent(tmp_path):
    """损坏的 lock 文件不应阻止 acquire"""
    cache = str(tmp_path)
    with open(os.path.join(cache, ".lock-scan"), "w") as f:
        f.write("not json {{{")

    busy = process_lock.acquire(cache, "scan")
    assert busy is None  # 损坏视为不存在
