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
预览图路由：代理缓存、本地 sidecar、用户上传、清理、预热。
"""

import asyncio
import os
from aiohttp import web

from .routes_common import _safe_handler, get_manager, _get_preview_cache, logger, spawn_background, make_card_thumb
from .websocket import get_progress_ws
from . import process_lock


@_safe_handler
async def api_prewarm_previews(request):
    """把所有模型预览图加入后台预热队列（与浏览时按需预热同一套引擎），立即返回真实状态。
    实际下载由 preview_cache 的后台 worker 慢慢做，不阻塞、不再起第二套下载。"""
    mgr = get_manager()
    res = await mgr.prewarm_previews()   # 只入队，瞬时返回 {total, cached, dead, queued}
    return web.json_response({"success": True, **res})


@_safe_handler
async def api_upload_preview(request):
    """用户上传本地预览图（multipart/form-data）"""
    reader = await request.multipart()
    identifier = ""
    file_bytes = None
    ext = ""

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "identifier":
            identifier = (await part.text()).strip()
        elif part.name == "file":
            ext = os.path.splitext(part.filename or "")[1].lstrip(".").lower()
            file_bytes = await part.read(decode=False)

    if not identifier or not file_bytes:
        return web.json_response({"success": False, "error": "missing identifier or file"})

    max_size = 10 * 1024 * 1024  # 10MB 上限
    if len(file_bytes) > max_size:
        return web.json_response({"success": False, "error": "文件过大（上限 10MB）"})

    mgr = get_manager()
    result = mgr.save_uploaded_preview(identifier, file_bytes, ext)
    return web.json_response(result)


@_safe_handler
async def api_local_preview(request):
    """本地预览图（sidecar://<id>）：根据 identifier 找到模型目录下的 .preview.{ext} 返回"""
    identifier = request.query.get("id", "")
    if not identifier:
        return web.Response(status=400, text="missing id")
    mgr = get_manager()
    path = mgr.resolve_sidecar_preview(identifier)
    if not path:
        return web.Response(status=404, text="sidecar preview not found")
    serve_path = path
    if request.query.get("size") == "card":
        thumb = await make_card_thumb(path)
        if thumb:
            serve_path = thumb
    resp = web.FileResponse(serve_path)
    ext = os.path.splitext(serve_path)[1].lstrip(".").lower()
    resp.headers["Content-Type"] = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
    }.get(ext, "application/octet-stream")
    # 本地文件随时可能被覆盖，不缓存
    resp.headers["Cache-Control"] = "no-store"
    return resp


@_safe_handler
async def api_preview(request):
    """代理预览图：首次请求下载并缓存，后续直接返回本地文件"""
    url = request.query.get("url", "")
    if not url:
        return web.Response(status=400, text="missing url")

    cache = _get_preview_cache()
    cached_path = cache.get_cached_path(url)

    if not cached_path:
        if request.query.get("nofetch"):
            # 模型网格缩略图（本地优先）：未命中绝不前台联网——丢后台队列、立即 404，
            # 前端落占位 + 有界重试，后台缓存好后自愈补显。这是消除"后台跑任务时浏览器
            # 连接被占满 → 卡片卡住/滚动顿"的关键：网格请求永不阻塞在网络上。
            # 已知未命中（上面 get_cached_path 刚返回空），入队时跳过重复的磁盘 isfile 复查
            cache.schedule_prewarm([url], skip_cached_check=True)
            resp = web.Response(status=404, text="not cached")
            resp.headers["Cache-Control"] = "no-store"
            return resp
        # 详情/放大（单张、用户主动触发）：仍前台快速失败下载（短超时 + 不重试）。
        cached_path = await cache.ensure_cached(url, foreground=True)

    if cached_path and os.path.isfile(cached_path):
        serve_path = cached_path
        # 列表卡片用 480px WebP 缩略图（原图保持不动）；详情/放大不带 size 拿原图
        if request.query.get("size") == "card":
            thumb = await make_card_thumb(cached_path)
            if thumb:
                serve_path = thumb
        resp = web.FileResponse(serve_path)
        resp.headers["Content-Type"] = cache.get_content_type(serve_path)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    return web.Response(status=502, text="preview download failed")


@_safe_handler
async def api_cleanup_previews(request):
    """清理未引用的预览图缓存"""
    try:
        data = await request.json()
        force = data.get("force", False)
    except Exception:
        force = False

    mgr = get_manager()
    stats = mgr.db.get_stats()
    total = stats.get("total", 0)
    matched = stats.get("matched", 0)

    if not force and total > 0 and matched / total < 0.3:
        return web.json_response({
            "success": False,
            "error": "unsafe",
            "total": total,
            "matched": matched,
        })

    valid_urls = mgr.db.get_all_preview_urls()
    cache = _get_preview_cache()
    removed = cache.cleanup_orphaned(valid_urls)
    return web.json_response({"success": True, "removed": removed})


@_safe_handler
async def api_cache_stats(request):
    """预览图 + 缩略图缓存统计（文件数 / 字节数）"""
    cache = _get_preview_cache()
    # 2 万+文件全盘 scandir 约 0.8s，扔线程池跑，避免冻结事件循环
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, cache.get_cache_stats)
    return web.json_response({"success": True, "stats": stats})


@_safe_handler
async def api_clear_thumbs(request):
    """清空缩略图缓存（可再生，下次浏览自动重建）"""
    cache = _get_preview_cache()
    removed = cache.clear_thumbs()
    return web.json_response({"success": True, "removed": removed})
