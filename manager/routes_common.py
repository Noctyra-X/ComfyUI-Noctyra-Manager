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
路由层共享工具：全局管理器单例、预览缓存单例、统一异常装饰器。
被 routes.py 和各个 routes_*.py 子模块共用。
"""

import asyncio
import concurrent.futures
import logging
import os
from aiohttp import web

from .manager import ModelManager
from .config import get_config
from .preview_cache import PreviewCache

logger = logging.getLogger("noctyra.routes")

_manager = None
_preview_cache = None

# 运行模式：默认 "integrated"（作为 ComfyUI 插件跑）；独立启动时 __main__.py 会改为 "standalone"
_runtime_mode = "integrated"


def set_runtime_mode(mode: str):
    """由 __main__.py 调用，声明独立模式；前端通过 /api/noctyra/settings 读取"""
    global _runtime_mode
    _runtime_mode = mode if mode in ("integrated", "standalone") else "integrated"


def get_runtime_mode() -> str:
    return _runtime_mode


def get_manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager


def _get_preview_cache() -> PreviewCache:
    global _preview_cache
    if _preview_cache is None:
        config = get_config()
        _preview_cache = PreviewCache(config.cache_dir)
    return _preview_cache


# 缩略图生成（PIL 解码+缩放+编码）是 CPU 密集。用专用有界线程池，避免占满 asyncio
# 默认 executor 而拖累其它 run_in_executor 调用；worker 数压低，防止打满 CPU。
_thumb_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="noctyra-thumb"
)


async def make_card_thumb(source_path):
    """专用线程池里生成/取用 480px 卡片缩略图，失败返回 None（调用方回退原图）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _thumb_executor, _get_preview_cache().get_thumb, source_path
    )


def _safe_handler(fn):
    """为路由处理函数添加统一异常捕获"""
    async def wrapper(request):
        try:
            return await fn(request)
        except web.HTTPException:
            raise
        except Exception as e:
            logger.error("[Noctyra-MM] 路由异常 [%s]: %s", fn.__name__, e, exc_info=True)
            return web.json_response(
                {"success": False, "error": str(e)}, status=500
            )
    wrapper.__name__ = fn.__name__
    return wrapper


# 后台任务强引用集合：asyncio 不持有 task 引用，未完成的 task 可能被 GC 静默
# 回收并取消（CPython 官方文档明确警告）。spawn_background 持有引用直到任务结束。
_background_tasks: set = set()


def parse_int(value, default=None, *, minimum=None, maximum=None):
    """把任意输入安全转成 int；失败返回 default。可选夹紧到 [minimum, maximum]。
    用于路由层校验客户端传来的 id / 数字参数，避免裸 int() 抛 ValueError 变 500。"""
    try:
        n = int(value)
    except (ValueError, TypeError):
        return default
    if minimum is not None and n < minimum:
        n = minimum
    if maximum is not None and n > maximum:
        n = maximum
    return n


def path_within_roots(file_path, roots) -> bool:
    """校验 file_path 是否落在任一 root 目录内（含 root 自身）。

    用于删除/移动等危险文件操作前的白名单校验，防止被构造请求拿任意路径去
    os.remove/os.replace（本地服务 + 浏览器扩展/CSRF 可达）。Windows 大小写不敏感。
    任一参数非法 → False（拒绝）。"""
    if not file_path:
        return False
    try:
        norm = os.path.normcase(os.path.normpath(os.path.abspath(file_path)))
    except Exception:
        return False
    for r in roots:
        if not r:
            continue
        try:
            nr = os.path.normcase(os.path.normpath(os.path.abspath(r)))
        except Exception:
            continue
        if norm == nr or norm.startswith(nr + os.sep):
            return True
    return False


def spawn_background(coro):
    """在当前运行的 event loop 上启动后台任务并持有强引用，防止被 GC 回收。
    必须在有运行 loop 的 async 上下文调用（所有 route handler 都满足）。
    替代已弃用的 asyncio.get_event_loop().create_task()。"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
