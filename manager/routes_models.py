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
模型相关路由：list/detail、文件夹、标签、base_model、收藏、笔记、自定义信息、删除、绑定。
"""

import asyncio
import math
import os
import time
from aiohttp import web

from .routes_common import _safe_handler, get_manager, logger, spawn_background, path_within_roots
from .websocket import get_progress_ws

# preview_status 过滤(missing/complete/failed)需全量扫描算预览状态,开销大。按"过滤签名"做
# 短 TTL 缓存:翻页/反复请求同一过滤直接复用,不每页重扫全库。下载落盘 8s 内的轻微陈旧可接受。
_preview_filter_cache: dict = {}
_PREVIEW_FILTER_TTL = 8.0


def _collect_preview_urls(model: dict) -> list:
    """从单个模型里拉出所有需要缓存的预览 URL（主封面 + 所有预览图）。
    sidecar:// 开头的本地引用跳过。"""
    urls = []
    pu = model.get("preview_url")
    if pu and not pu.startswith("sidecar://"):
        urls.append(pu)
    for img in model.get("preview_images") or []:
        if isinstance(img, dict):
            u = img.get("url")
            if u and not u.startswith("sidecar://"):
                urls.append(u)
    return urls


def _compute_preview_status(cache, model: dict, cached_keys=None) -> dict:
    """算单个模型的预览图缓存状态。cache 是 PreviewCache 实例。
    cached_keys 传入 cache.cached_keys_snapshot() → 内存判断，避免每 URL 多次磁盘 stat。"""
    return cache.check_urls_status(_collect_preview_urls(model), cached_keys=cached_keys)


@_safe_handler
async def api_models_list(request):
    """获取模型列表（分页 + 过滤 + 预览图完整度）"""
    mgr = get_manager()
    from .routes_common import _get_preview_cache
    cache = _get_preview_cache()

    try:
        page = max(1, int(request.query.get("page", 1)))
        # 上限放宽到 10 万：虚拟滚动前端一次性载入全部模型（行数据已裁剪轻量），前端只渲染可视窗口
        page_size = max(1, min(100000, int(request.query.get("page_size", 40))))
    except (ValueError, TypeError):
        page, page_size = 1, 40
    sort_by = request.query.get("sort_by", "file_name")
    sort_dir = request.query.get("sort_dir", "")

    filters = {}
    for key in ("search", "folder", "base_model", "source", "model_type", "tag", "license", "lora_subtype"):
        val = request.query.get(key, "")
        if val:
            filters[key] = val
    if request.query.get("sfw_only") in ("1", "true", "True"):
        filters["sfw_only"] = True

    preview_filter = request.query.get("preview_status", "").lower()

    if preview_filter in ("missing", "complete", "failed"):
        # 过滤模式：拉全量匹配项，逐个算预览状态，再分页。只对已匹配的模型有意义
        # （未匹配模型 preview_images 空，自动跳过 complete，留在 missing 分组会干扰
        # 判断 —— 只算 total>0 的）。failed = 有死链(404/410，下架，重试无用)。
        # 全量扫描开销大 → 按过滤签名短 TTL 缓存,翻页/重复请求复用,不每页重扫全库。
        sig = (preview_filter, sort_by, sort_dir, tuple(sorted(filters.items())))
        now = time.monotonic()
        hit = _preview_filter_cache.get(sig)
        if hit and (now - hit[0]) < _PREVIEW_FILTER_TTL:
            filtered = hit[1]
        else:
            all_models, _all_total = mgr.get_models_paginated(
                page=1, page_size=100000, sort_by=sort_by, sort_dir=sort_dir, **filters
            )
            cached_keys = cache.cached_keys_snapshot()   # 一次 listdir，全量过滤共用
            filtered = []
            for m in all_models:
                status = _compute_preview_status(cache, m, cached_keys)
                m["preview_status"] = status
                if status["total"] == 0:
                    continue  # 未匹配的模型没有在线预览，不进任何预览筛选结果
                # missing 排除"纯死链"(缺失全是 404/410)→ 只算真正还能下载的,让 failed
                # 与 missing 不在纯死链模型上重叠(纯死链只进 failed)
                if preview_filter == "missing" and (status["missing"] - status.get("dead", 0)) > 0:
                    filtered.append(m)
                elif preview_filter == "complete" and status["complete"]:
                    filtered.append(m)
                elif preview_filter == "failed" and status.get("dead", 0) > 0:
                    filtered.append(m)
            _preview_filter_cache[sig] = (now, filtered)
            if len(_preview_filter_cache) > 32:   # 防无界增长,丢最旧
                oldest = min(_preview_filter_cache, key=lambda k: _preview_filter_cache[k][0])
                _preview_filter_cache.pop(oldest, None)
        total = len(filtered)
        offset = (page - 1) * page_size
        models = filtered[offset:offset + page_size]
        total_pages = max(1, math.ceil(total / page_size) if total else 1)
        return web.json_response({
            "success": True,
            "models": models, "total": total,
            "page": page, "page_size": page_size, "total_pages": total_pages,
        })

    models, total = mgr.get_models_paginated(
        page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir, **filters
    )
    # 当前页附 preview_status，方便前端显示徽章。一次 listdir 建快照，避免每 URL 多次磁盘 stat
    cached_keys = cache.cached_keys_snapshot()
    for m in models:
        m["preview_status"] = _compute_preview_status(cache, m, cached_keys)
    total_pages = math.ceil(total / page_size) if page_size > 0 else 1

    return web.json_response({
        "success": True,
        "models": models,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    })


@_safe_handler
async def api_picker_match(request):
    """画布模型选择器：传一组 ComfyUI widget 选项名，返回每个名字匹配到的
    本地模型元数据（预览图 / 显示名 / base_model / NSFW 等级）。
    body: {"names": ["sd_xl.safetensors", "SDXL/foo.safetensors", ...]}
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    names = data.get("names") or []
    if not isinstance(names, list):
        return web.json_response({"success": False, "error": "names 必须是数组"})
    # 限制数量防滥用（单个 loader 的选项数远小于此）
    names = [str(n) for n in names[:5000] if n]
    mgr = get_manager()
    items = mgr.db.get_models_by_names(names)
    return web.json_response({"success": True, "items": items})


@_safe_handler
async def api_model_detail(request):
    """获取单个模型详情（含本地其他版本）"""
    identifier = request.match_info["identifier"]
    mgr = get_manager()
    model = mgr.get_model(identifier)
    if not model:
        return web.json_response({"success": False, "error": "not found"}, status=404)

    local_versions = []
    mid = model.get("civitai_model_id")
    if mid:
        local_versions = mgr.get_local_versions(mid, model.get("file_path", ""))

    related = mgr.db.get_related(model.get("file_path", ""))

    return web.json_response({
        "success": True,
        "model": model,
        "local_versions": local_versions,
        "related_models": related,
    })


@_safe_handler
async def api_model_safetensors(request):
    """读取模型 safetensors 文件头：__metadata__ + 张量清单（name/dtype/shape）。

    懒加载给详情弹窗的「结构」Tab 用。按 id（sha256 或 file_path）取库内已知模型，
    只读其文件头，杜绝任意文件读取。"""
    import asyncio
    from .scanner import read_safetensors_header
    identifier = request.query.get("id", "")
    if not identifier:
        return web.json_response({"success": False, "error": "缺少 id"})
    mgr = get_manager()
    model = mgr.get_model(identifier)
    if not model:
        return web.json_response({"success": False, "error": "not found"}, status=404)
    file_path = model.get("file_path", "")
    if not file_path or not file_path.endswith(".safetensors"):
        return web.json_response({"success": False, "error": "非 safetensors 文件，无结构可读"})
    if not os.path.isfile(file_path):
        return web.json_response({"success": False, "error": "文件不存在（可能已删除/移动）"})
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, read_safetensors_header, file_path)
    return web.json_response({"success": True, **info})


@_safe_handler
async def api_folders(request):
    """获取文件夹列表及计数"""
    mgr = get_manager()
    folders = mgr.get_folders()
    return web.json_response({"success": True, "folders": folders})


@_safe_handler
async def api_tags(request):
    """获取 Top N 标签"""
    try:
        limit = max(1, min(500, int(request.query.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    mgr = get_manager()
    tags = mgr.get_tags(limit)
    return web.json_response({"success": True, "tags": tags})


@_safe_handler
async def api_add_tags(request):
    """为模型添加标签"""
    body = await request.json()
    file_path = body.get("file_path", "")
    tags = body.get("tags", [])
    if not file_path or not tags:
        return web.json_response({"success": False, "error": "缺少参数"})
    if not isinstance(tags, list) or len(tags) > 50:
        return web.json_response({"success": False, "error": "标签格式无效或数量超限"})
    tags = [str(t).strip()[:100] for t in tags if str(t).strip()]
    mgr = get_manager()
    mgr.db.add_tags(file_path, tags)
    logger.info("[Noctyra-MM] 添加标签: %s +%d tags", os.path.basename(file_path), len(tags))
    return web.json_response({"success": True})


@_safe_handler
async def api_remove_tag(request):
    """删除模型的一个标签"""
    body = await request.json()
    file_path = body.get("file_path", "")
    tag = body.get("tag", "")
    if not file_path or not tag:
        return web.json_response({"success": False, "error": "缺少参数"})
    mgr = get_manager()
    mgr.db.remove_tag(file_path, tag)
    logger.info("[Noctyra-MM] 删除标签: %s -%s", os.path.basename(file_path), tag)
    return web.json_response({"success": True})


@_safe_handler
async def api_set_tags(request):
    """一次性替换模型全部标签（给 textarea 式编辑用）"""
    body = await request.json()
    file_path = body.get("file_path", "")
    tags = body.get("tags", [])
    if not file_path:
        return web.json_response({"success": False, "error": "缺少 file_path"})
    if not isinstance(tags, list) or len(tags) > 100:
        return web.json_response({"success": False, "error": "标签格式无效或数量超限"})
    tags = [str(t).strip()[:100] for t in tags if str(t).strip()]
    mgr = get_manager()
    mgr.db.set_tags(file_path, tags)
    logger.info("[Noctyra-MM] 设置标签: %s (%d tags)", os.path.basename(file_path), len(tags))
    return web.json_response({"success": True})


@_safe_handler
async def api_move_model(request):
    """移动模型到目标文件夹"""
    body = await request.json()
    file_path = body.get("file_path", "")
    target_folder = body.get("target_folder", "")
    if not file_path:
        return web.json_response({"success": False, "error": "缺少参数"})
    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = mgr.move_model(file_path, target_folder)
    if result.get("success"):
        logger.info("[Noctyra-MM] 移动模型: %s -> %s", os.path.basename(file_path), target_folder or "(根目录)")
    return web.json_response(result)


@_safe_handler
async def api_base_models(request):
    """获取所有 base model"""
    mgr = get_manager()
    base_models = mgr.get_base_models()
    return web.json_response({"success": True, "base_models": base_models})


@_safe_handler
async def api_base_model_stats(request):
    """各 base_model 的条目统计（只读展示用）"""
    mgr = get_manager()
    stats = mgr.get_base_model_stats()
    return web.json_response({"success": True, "stats": stats})


@_safe_handler
async def api_refresh_base_models(request):
    """后台批量刷新 base_model（调 CivitAI API，只动 base_model 列）"""
    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})

    ws = get_progress_ws()
    progress_cb = ws.make_progress_callback("refresh_base_models_progress")

    async def run():
        # 后台任务期间占用 is_busy，防扫描/匹配/整理等中途插入
        with mgr.exclusive_op():
            try:
                result = await mgr.refresh_base_models(progress_callback=progress_cb)
                await ws.send_complete("refresh_base_models_progress", result)
            except Exception as e:
                logger.error("[Noctyra-MM] 刷新 base_model 异常: %s", e, exc_info=True)
                await ws.send_error("refresh_base_models_progress", str(e))

    spawn_background(run())
    return web.json_response({"success": True, "started": True})


@_safe_handler
async def api_status(request):
    """管理器状态。?source=deleted 时 type_counts 按软删模型统计（给已删除视图的类型 tab）"""
    mgr = get_manager()
    source = request.query.get("source", "")
    stats = mgr.get_stats(source)
    from .routes_common import _get_preview_cache
    prewarm = _get_preview_cache().get_prewarm_status()
    # 把失败的预览 URL 反查成所属模型名，让任务中心显示"是哪个模型"而非一串图片 hash
    fails = prewarm.get("recent_failures") or []
    if fails:
        owners = mgr.resolve_preview_owners([f.get("url", "") for f in fails])
        # 用新 dict,不原地改 preview_cache 里存的失败项(否则每 2s 轮询都重复写回同一批对象)。
        # 附 model(名)+ model_path(file_path,供前端点击跳转到该模型详情)。
        prewarm["recent_failures"] = [
            {**f,
             "model": (owners.get(f.get("url", "")) or {}).get("name", ""),
             "model_path": (owners.get(f.get("url", "")) or {}).get("file_path", "")}
            for f in fails]
    # 附带扫描/匹配运行状态 + 当前进度 + 预热下载进度，供前端任务中心展示
    return web.json_response({"success": True, **stats, **mgr.runtime_status, "prewarm": prewarm})


@_safe_handler
async def api_bind(request):
    """手动绑定来源"""
    data = await request.json()
    sha256 = data.get("sha256", "")
    url = data.get("url", "")

    if not sha256 or not url:
        return web.json_response({"success": False, "error": "missing params"})

    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})

    from .civitai import is_civitai_host
    with mgr.exclusive_op():
        if is_civitai_host(url):
            success = await mgr.bind_civitai(sha256, url)
        elif "huggingface.co" in url:
            success = await mgr.bind_huggingface(sha256, url)
        else:
            return web.json_response({"success": False, "error": "unsupported url"})

    return web.json_response({"success": success})


@_safe_handler
async def api_favorite(request):
    """切换收藏状态"""
    data = await request.json()
    file_path = data.get("file_path", "")
    favorite = data.get("favorite", False)
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})
    mgr = get_manager()
    mgr.toggle_favorite(file_path, favorite)
    return web.json_response({"success": True})


@_safe_handler
async def api_notes(request):
    """更新笔记"""
    data = await request.json()
    file_path = data.get("file_path", "")
    notes = data.get("notes", "")
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})
    mgr = get_manager()
    mgr.update_notes(file_path, notes)
    return web.json_response({"success": True})


@_safe_handler
async def api_update_custom(request):
    """用户"自定义"Tab 保存手填信息"""
    data = await request.json()
    identifier = data.get("identifier", "") or data.get("sha256", "") or data.get("file_path", "")
    if not identifier:
        return web.json_response({"success": False, "error": "missing identifier"})

    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        return web.json_response({"success": False, "error": "fields must be object"})

    mgr = get_manager()
    result = mgr.update_custom_info(identifier, fields)
    return web.json_response(result)


@_safe_handler
async def api_check_integrity(request):
    """检测模型文件是否损坏（截断/头错乱/safetensors 无法加载）"""
    data = await request.json()
    file_path = data.get("file_path", "")
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})
    mgr = get_manager()
    if not path_within_roots(file_path, list(mgr.config.model_roots) + [mgr.config.cache_dir]):
        return web.json_response({"success": False, "error": "路径不在允许范围内"})
    result = await mgr.check_model_integrity(file_path)
    return web.json_response(result)


@_safe_handler
async def api_redownload(request):
    """重新下载并覆盖损坏的模型文件（CivitAI 已匹配的按 version_id 取原文件）"""
    data = await request.json()
    file_path = data.get("file_path", "")
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})
    mgr = get_manager()
    # 会覆盖写盘，目标路径必须在模型目录白名单内
    if not path_within_roots(file_path, list(mgr.config.model_roots) + [mgr.config.cache_dir]):
        return web.json_response({"success": False, "error": "路径不在允许范围内"})
    result = await mgr.redownload_model(file_path)
    return web.json_response(result)


@_safe_handler
async def api_delete_model(request):
    """删除模型"""
    data = await request.json()
    file_path = data.get("file_path", "")
    delete_file = data.get("delete_file", False)
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})
    mgr = get_manager()
    # 安全：删文件前校验路径落在扫描根/缓存内，防被构造请求删盘上任意文件
    if delete_file and not path_within_roots(
            file_path, list(mgr.config.model_roots) + [mgr.config.cache_dir]):
        logger.warning("[Noctyra-MM] 删除拒绝：路径 %s 不在允许范围内", file_path)
        return web.json_response({"success": False, "error": "路径不在允许范围内"})
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        success = mgr.delete_model(file_path, delete_file=delete_file)
    if success:
        logger.info("[Noctyra-MM] 模型已删除: %s (delete_file=%s)", file_path, delete_file)
    return web.json_response({"success": success})


@_safe_handler
async def api_soft_delete_model(request):
    """删除模型文件但保留记录"""
    data = await request.json()
    file_path = data.get("file_path", "")
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})
    mgr = get_manager()
    # 安全：soft delete 会删文件，同样校验路径白名单
    if not path_within_roots(
            file_path, list(mgr.config.model_roots) + [mgr.config.cache_dir]):
        logger.warning("[Noctyra-MM] 软删除拒绝：路径 %s 不在允许范围内", file_path)
        return web.json_response({"success": False, "error": "路径不在允许范围内"})
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        success = mgr.soft_delete_model(file_path)
    return web.json_response({"success": success})


@_safe_handler
async def api_restore_model(request):
    """恢复软删除(存档)的模型记录：文件已回到磁盘时取消"已删除"标记。"""
    data = await request.json()
    file_path = data.get("file_path", "")
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})
    mgr = get_manager()
    # 安全：恢复会把文件从存档夹移回 file_path（目标来自请求），校验目标在模型根白名单内，
    # 防被构造请求往任意位置写文件（与 api_soft_delete_model 一致）
    if not path_within_roots(
            file_path, list(mgr.config.model_roots) + [mgr.config.cache_dir]):
        logger.warning("[Noctyra-MM] 恢复拒绝：路径 %s 不在允许范围内", file_path)
        return web.json_response({"success": False, "error": "路径不在允许范围内"})
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        success = mgr.restore_model(file_path)
    if not success:
        return web.json_response({"success": False, "error": "文件还没回到磁盘，无法恢复"})
    return web.json_response({"success": True})


@_safe_handler
async def api_trigger_words(request):
    """汇总所有本地模型的 trained_words。

    Query: ?limit=<N> ?min_count=<N>
    返回 {success, words: [{word, count, model_types: [...]}]}
    """
    try:
        limit = max(1, min(5000, int(request.query.get("limit", 500))))
    except (ValueError, TypeError):
        limit = 500
    try:
        min_count = max(1, int(request.query.get("min_count", 1)))
    except (ValueError, TypeError):
        min_count = 1

    mgr = get_manager()
    words = mgr.db.aggregate_trained_words(limit=limit, min_count=min_count)
    return web.json_response({"success": True, "count": len(words), "words": words})


@_safe_handler
async def api_reveal_in_explorer(request):
    """在系统文件管理器中高亮目标文件。

    Body: { "file_path": str }
    Windows: explorer /select,<path>   macOS: open -R <path>   Linux: xdg-open <dirname>
    只允许操作 model_roots 内的路径，防止被用来探测/暴露其他目录。
    """
    import os
    import sys
    import subprocess

    data = await request.json()
    file_path = (data.get("file_path") or "").strip()
    if not file_path:
        return web.json_response({"success": False, "error": "missing file_path"})

    mgr = get_manager()
    # 安全校验：必须在某个已配置的 model_root 内（或 cache_dir 内，给工作流图库用）
    norm = os.path.normcase(os.path.normpath(os.path.abspath(file_path)))
    allowed_roots = [os.path.normcase(os.path.normpath(os.path.abspath(r))) for r in mgr.config.model_roots]
    cache_root = os.path.normcase(os.path.normpath(os.path.abspath(mgr.config.cache_dir)))
    allowed_roots.append(cache_root)
    if not any(norm == r or norm.startswith(r + os.sep) for r in allowed_roots):
        logger.warning("[Noctyra-MM] reveal 拒绝：路径 %s 不在任何扫描根内", file_path)
        return web.json_response({"success": False, "error": "路径不在允许范围内"})

    if not os.path.exists(file_path):
        return web.json_response({"success": False, "error": "文件不存在"})

    try:
        if sys.platform == "win32":
            # /select 高亮该文件；要求路径用反斜杠 + 不加引号的逗号
            subprocess.Popen(["explorer", "/select,", os.path.normpath(file_path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", file_path])
        else:
            # Linux/其他：文件管理器无统一 "reveal"，退化为打开其所在目录
            subprocess.Popen(["xdg-open", os.path.dirname(file_path)])
    except Exception as e:
        logger.error("[Noctyra-MM] reveal 调用失败: %s", e)
        return web.json_response({"success": False, "error": str(e)})

    return web.json_response({"success": True})


# ==================== 版本管理 ====================

@_safe_handler
async def api_model_versions(request):
    """获取某个 CivitAI model 的全部版本，并标注本地状态 / 忽略状态。

    Query: ?model_id=<int>
    返回: {success, versions: [{version_id, name, base_model, published_at, file_size,
                                 download_url, preview_url, local, ignored}]}
    """
    try:
        model_id = int(request.query.get("model_id") or 0)
    except (TypeError, ValueError):
        model_id = 0
    if not model_id:
        return web.json_response({"success": False, "error": "缺少 model_id"})

    mgr = get_manager()
    model_data = await mgr.civitai.get_model_info(model_id)
    if not model_data:
        return web.json_response({"success": False, "error": "无法获取模型信息"})

    # 本地已有的 version_id
    local_rows = mgr.db.query_by_model_id(model_id) or []
    local_vids = set()
    local_by_vid = {}
    for r in local_rows:
        vid = r.get("civitai_version_id")
        if vid is not None:
            local_vids.add(int(vid))
            local_by_vid[int(vid)] = r

    ignored_vids = set(mgr.db.list_ignored_versions(model_id))

    versions = []
    for v in model_data.get("modelVersions", []) or []:
        vid = v.get("id")
        if not vid:
            continue
        files = v.get("files", []) or []
        primary = next((f for f in files if (f.get("type") or "").lower() == "model"), None)
        if primary is None and files:
            primary = files[0]
        images = v.get("images", []) or []
        preview = images[0].get("url", "") if images else ""
        local_info = local_by_vid.get(int(vid))
        versions.append({
            "version_id": vid,
            "name": v.get("name", ""),
            "base_model": v.get("baseModel", ""),
            "published_at": v.get("publishedAt", ""),
            "download_url": primary.get("downloadUrl", "") if primary else "",
            "file_name": primary.get("name", "") if primary else "",
            "file_size": (primary.get("sizeKB", 0) * 1024) if primary else 0,
            "preview_url": preview,
            "local": int(vid) in local_vids,
            "local_file_name": local_info.get("file_name", "") if local_info else "",
            "ignored": int(vid) in ignored_vids,
        })

    return web.json_response({
        "success": True,
        "model_id": model_id,
        "model_name": model_data.get("name", ""),
        "model_type": model_data.get("type", ""),
        "versions": versions,
    })


@_safe_handler
async def api_version_ignore(request):
    """忽略某个版本（从更新检查里排除）"""
    data = await request.json()
    try:
        model_id = int(data.get("model_id") or 0)
        version_id = int(data.get("version_id") or 0)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "model_id / version_id 必须是整数"})
    if not model_id or not version_id:
        return web.json_response({"success": False, "error": "缺少 model_id 或 version_id"})

    mgr = get_manager()
    mgr.db.add_ignored_version(model_id, version_id)
    logger.info("[Noctyra-MM] 忽略版本：model_id=%s version_id=%s", model_id, version_id)
    return web.json_response({"success": True})


@_safe_handler
async def api_version_unignore(request):
    """取消忽略"""
    data = await request.json()
    try:
        model_id = int(data.get("model_id") or 0)
        version_id = int(data.get("version_id") or 0)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "model_id / version_id 必须是整数"})
    if not model_id or not version_id:
        return web.json_response({"success": False, "error": "缺少 model_id 或 version_id"})

    mgr = get_manager()
    mgr.db.remove_ignored_version(model_id, version_id)
    return web.json_response({"success": True})


# ==================== Filter Presets ====================

# 允许保存到预设里的 filter 字段白名单（防止客户端乱塞）
_PRESET_FILTER_KEYS = {
    "search", "folder", "base_model", "source", "model_type", "tag", "license",
    "lora_subtype", "sort_by", "sort_dir", "sfw_only",
}


def _sanitize_preset_filters(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for k, v in raw.items():
        if k not in _PRESET_FILTER_KEYS:
            continue
        if v is None:
            continue
        if isinstance(v, bool):
            clean[k] = v
        elif isinstance(v, (int, float)):
            clean[k] = v
        elif isinstance(v, str):
            s = v.strip()
            if s:
                clean[k] = s
    return clean


@_safe_handler
async def api_filter_presets_list(request):
    """列出所有筛选预设"""
    mgr = get_manager()
    presets = mgr.db.list_filter_presets()
    return web.json_response({"success": True, "presets": presets})


@_safe_handler
async def api_filter_presets_save(request):
    """保存/更新筛选预设（按 name upsert）"""
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return web.json_response({"success": False, "error": "name 不能为空"})
    if len(name) > 64:
        return web.json_response({"success": False, "error": "name 过长（最多 64 字符）"})
    filters = _sanitize_preset_filters(data.get("filters"))

    mgr = get_manager()
    try:
        preset = mgr.db.save_filter_preset(name, filters)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})
    return web.json_response({"success": True, "preset": preset})


@_safe_handler
async def api_filter_presets_delete(request):
    """删除筛选预设（按 id 或 name）"""
    data = await request.json()
    identifier = data.get("id")
    if identifier is None:
        identifier = data.get("name")
    if identifier is None:
        return web.json_response({"success": False, "error": "缺少 id 或 name"})

    mgr = get_manager()
    removed = mgr.db.delete_filter_preset(identifier)
    return web.json_response({"success": removed})
