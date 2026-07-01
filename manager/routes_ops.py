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
长时间操作路由：scan/match/match-single/check-model-updates/organize/rebuild/batch/duplicates/check-update。
"""

import asyncio
from aiohttp import web

from .routes_common import _safe_handler, get_manager, logger, spawn_background, path_within_roots
from .websocket import get_progress_ws
from . import process_lock


@_safe_handler
async def api_scan(request):
    """触发扫描（异步执行，通过 WebSocket 推送进度）"""
    mgr = get_manager()
    if mgr.is_busy:
        logger.info("[Noctyra-MM] 扫描请求被拒绝：已有操作在进行中")
        return web.json_response({"success": False, "error": "busy"})

    # 跨进程锁：防止 ComfyUI 和独立模式同时点扫描
    busy = process_lock.acquire(mgr.config.cache_dir, "scan")
    if busy is not None:
        msg = process_lock.format_busy_message("scan", busy)
        logger.info("[Noctyra-MM] %s", msg)
        return web.json_response({"success": False, "error": "busy_other_process", "message": msg})

    force = request.query.get("force", "").lower() in ("1", "true", "yes")
    logger.info("[Noctyra-MM] 开始扫描任务%s", "（全量重扫）" if force else "")
    ws = get_progress_ws()
    progress_cb = ws.make_progress_callback("scan_progress")

    async def run():
        try:
            total = await mgr.scan(progress_callback=progress_cb, force=force)
            stats = mgr.get_stats()
            await ws.send_complete("scan_progress", {"total": total, **stats})
        except Exception as e:
            logger.error("[Noctyra-MM] 扫描异常: %s", e, exc_info=True)
            await ws.send_error("scan_progress", str(e))
        finally:
            process_lock.release(mgr.config.cache_dir, "scan")

    spawn_background(run())
    return web.json_response({"success": True, "started": True})


@_safe_handler
async def api_match(request):
    """触发在线匹配（异步执行，通过 WebSocket 推送进度）"""
    mgr = get_manager()
    if mgr.is_busy:
        logger.info("[Noctyra-MM] 匹配请求被拒绝：已有操作在进行中")
        return web.json_response({"success": False, "error": "busy"})

    busy = process_lock.acquire(mgr.config.cache_dir, "match")
    if busy is not None:
        msg = process_lock.format_busy_message("match", busy)
        logger.info("[Noctyra-MM] %s", msg)
        return web.json_response({"success": False, "error": "busy_other_process", "message": msg})

    try:
        data = await request.json()
        rematch = data.get("rematch", False)
    except Exception:
        rematch = False

    logger.info("[Noctyra-MM] 开始匹配任务 (rematch=%s)", rematch)
    ws = get_progress_ws()
    progress_cb = ws.make_progress_callback("match_progress")

    async def run():
        try:
            stats = await mgr.match_all(progress_callback=progress_cb, rematch=rematch)
            await ws.send_complete("match_progress", {"stats": stats})
        except Exception as e:
            logger.error("[Noctyra-MM] 匹配异常: %s", e, exc_info=True)
            await ws.send_error("match_progress", str(e))
        finally:
            process_lock.release(mgr.config.cache_dir, "match")

    spawn_background(run())
    return web.json_response({"success": True, "started": True})


@_safe_handler
async def api_cancel_match(request):
    """请求中途停止匹配(已匹配的保留,未处理的下次再匹配)。"""
    get_manager().cancel_match()
    return web.json_response({"success": True})


@_safe_handler
async def api_cancel_prewarm(request):
    """请求停止后台预缓存(清空待下载队列,在途批次尽快跳过)。"""
    from .routes_common import _get_preview_cache
    _get_preview_cache().cancel_prewarm()
    return web.json_response({"success": True})


@_safe_handler
async def api_check_model_updates(request):
    """检查模型是否有新版本"""
    mgr = get_manager()
    if mgr.is_busy:
        logger.info("[Noctyra-MM] 更新检查被拒绝：已有操作在进行中")
        return web.json_response({"success": False, "error": "busy"})

    logger.info("[Noctyra-MM] 开始检查模型更新")
    updates = await mgr.check_model_updates()
    return web.json_response({"success": True, "updates": updates, "count": len(updates)})


@_safe_handler
async def api_match_single(request):
    """匹配单个模型。source: ''/'both'/'civitai'/'huggingface'"""
    data = await request.json()
    file_path = data.get("file_path", "")
    source = data.get("source", "") or ""
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})

    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = await mgr.match_single(file_path, source=source)
    matched = result["civitai"] or result["huggingface"]
    return web.json_response({"success": True, "matched": matched, "detail": result})


@_safe_handler
async def api_organize_single(request):
    """对单个模型执行自动整理"""
    data = await request.json()
    file_path = data.get("file_path", "")
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})

    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = mgr.organize_single(file_path)
    return web.json_response(result)


@_safe_handler
async def api_organize_preview(request):
    """预览自动整理结果"""
    mgr = get_manager()
    moves = mgr.preview_organize()
    return web.json_response({"success": True, "moves": moves, "count": len(moves)})


@_safe_handler
async def api_organize_execute(request):
    """执行自动整理"""
    data = await request.json()
    moves = data.get("moves", [])
    if not moves:
        return web.json_response({"success": False, "error": "no moves"})

    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = mgr.execute_organize(moves)
    return web.json_response({"success": True, **result})


@_safe_handler
async def api_rebuild(request):
    """重建缓存（清空数据库 + 重新扫描）"""
    mgr = get_manager()
    if mgr.is_busy:
        logger.info("[Noctyra-MM] 重建缓存被拒绝：已有操作在进行中")
        return web.json_response({"success": False, "error": "busy"})

    logger.info("[Noctyra-MM] 开始重建缓存")
    ws = get_progress_ws()
    progress_cb = ws.make_progress_callback("scan_progress")

    async def run():
        try:
            total = await mgr.rebuild_cache(progress_callback=progress_cb)
            stats = mgr.get_stats()
            await ws.send_complete("scan_progress", {"total": total, **stats})
        except Exception as e:
            logger.error("[Noctyra-MM] 重建缓存异常: %s", e, exc_info=True)
            await ws.send_error("scan_progress", str(e))

    spawn_background(run())
    return web.json_response({"success": True, "started": True})


@_safe_handler
async def api_duplicates(request):
    """获取重复模型列表"""
    mgr = get_manager()
    groups = mgr.get_duplicates()
    return web.json_response({"success": True, "groups": groups, "count": len(groups)})


@_safe_handler
async def api_statistics(request):
    """统计页数据：总览 / 类型 / 基础模型 / 来源 / 使用 / Top 标签。"""
    mgr = get_manager()
    stats = mgr.db.get_statistics()
    stats["top_tags"] = mgr.db.get_tags(20)
    return web.json_response({"success": True, "stats": stats})


@_safe_handler
async def api_batch_delete(request):
    """批量删除模型"""
    data = await request.json()
    file_paths = data.get("file_paths", [])
    delete_files = data.get("delete_files", False)
    if not file_paths:
        return web.json_response({"success": False, "error": "missing file_paths"})

    mgr = get_manager()
    # 安全：批量删文件前逐个校验路径落在扫描根/缓存内，剔除越界路径，
    # 防被构造请求删盘上任意文件（与单删 api_delete_model 一致）
    if delete_files:
        roots = list(mgr.config.model_roots) + [mgr.config.cache_dir]
        safe = [fp for fp in file_paths if path_within_roots(fp, roots)]
        if len(safe) != len(file_paths):
            logger.warning("[Noctyra-MM] 批量删除：剔除 %d 个越界路径", len(file_paths) - len(safe))
        file_paths = safe
        if not file_paths:
            return web.json_response({"success": False, "error": "路径不在允许范围内"})

    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = mgr.batch_delete(file_paths, delete_files=delete_files)
    return web.json_response({"success": True, **result})


@_safe_handler
async def api_batch_refresh(request):
    """批量重新匹配模型"""
    data = await request.json()
    file_paths = data.get("file_paths", [])
    if not file_paths:
        return web.json_response({"success": False, "error": "missing file_paths"})

    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})

    with mgr.exclusive_op():
        result = await mgr.batch_refresh(file_paths)
    return web.json_response({"success": True, **result})


@_safe_handler
async def api_batch_tag(request):
    """批量打标签（并集）"""
    data = await request.json()
    file_paths = data.get("file_paths") or []
    tags_raw = data.get("tags") or []
    if not file_paths:
        return web.json_response({"success": False, "error": "missing file_paths"})
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    if not tags:
        return web.json_response({"success": False, "error": "没有有效标签"})

    mgr = get_manager()
    result = mgr.batch_add_tags(file_paths, tags)
    return web.json_response({"success": True, **result})


@_safe_handler
async def api_batch_set_base_model(request):
    """批量设 base_model"""
    data = await request.json()
    file_paths = data.get("file_paths") or []
    base_model = (data.get("base_model") or "").strip()
    if not file_paths:
        return web.json_response({"success": False, "error": "missing file_paths"})
    if not base_model:
        return web.json_response({"success": False, "error": "base_model 不能为空"})

    mgr = get_manager()
    result = mgr.batch_set_base_model(file_paths, base_model)
    return web.json_response({"success": True, **result})


@_safe_handler
async def api_batch_move(request):
    """批量移动到目标文件夹"""
    data = await request.json()
    file_paths = data.get("file_paths") or []
    target_folder = (data.get("target_folder") or "").strip()
    if not file_paths:
        return web.json_response({"success": False, "error": "missing file_paths"})

    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = mgr.batch_move(file_paths, target_folder)
    return web.json_response({"success": True, **result})


@_safe_handler
async def api_check_update(request):
    """检查 GitHub 最新版本"""
    import aiohttp
    from .. import __version__

    repo = "Noctyra-X/ComfyUI-Noctyra-Manager"
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    logger.info("[Noctyra-MM] 正在检查插件更新...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    latest = data.get("tag_name", "").lstrip("v")
                    return web.json_response({
                        "success": True,
                        "current_version": __version__,
                        "latest_version": latest,
                        "has_update": latest != __version__ and latest != "",
                        "release_url": data.get("html_url", ""),
                        "release_notes": data.get("body", ""),
                        "published_at": data.get("published_at", ""),
                    })
                elif resp.status == 404:
                    return web.json_response({
                        "success": True,
                        "current_version": __version__,
                        "latest_version": __version__,
                        "has_update": False,
                        "release_url": "",
                    })
                else:
                    return web.json_response({"success": False, "error": f"GitHub API {resp.status}"})
    except Exception as e:
        logger.warning("[Noctyra-MM] 检查更新失败: %s", e)
        return web.json_response({"success": False, "error": str(e)})
