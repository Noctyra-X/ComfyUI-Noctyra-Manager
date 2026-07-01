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
Recipe（配方）端点：补全缺失资源 / 按指纹查同配方 / 批量导入 URL。
"""

import asyncio
import math
import os

from aiohttp import web

from .routes_common import _safe_handler, get_manager, logger, spawn_background, parse_int
from .routes_workflows_common import (
    _extract_comfy_resources,
    _lookup_resources_from_version_ids,
    _enrich_resources,
    _parse_civitai_image_id,
    _resource_is_local,
    _download_and_save_image,
)

# LoRA 变体类型（CivitAI / ComfyUI 提取里这些都算 lora）
_LORA_TYPES = {"lora", "locon", "lycoris", "dora", "loha", "lokr"}


@_safe_handler
async def api_recipe_lora_syntax(request):
    """把某配方（workflow_images 一行）里本地命中的 LoRA 拼成可粘贴的
    `<lora:文件名:权重>` 语法串（ComfyUI 文本-LoRA 节点 / A1111 通用）。本地缺失的不包含。

    Query: ?id=<int>
    返回: { success, syntax, included, missing, names: [...] }
    """
    image_id = parse_int(request.query.get("id"))
    if image_id is None:
        return web.json_response({"success": False, "error": "无效的 id"}, status=404)
    mgr = get_manager()
    image = mgr.db.get_workflow_image(image_id)
    if not image:
        return web.json_response({"success": False, "error": "not found"}, status=404)

    parts = []
    names = []
    triggers = []  # 各本地 LoRA 的触发词（去重，从本地模型记录取，不额外打 API）
    seen_trig = set()
    missing = 0
    for res in image.get("resources") or []:
        rtype = (res.get("type") or "").lower()
        if rtype not in _LORA_TYPES and "lora" not in rtype:
            continue
        local = _resource_is_local(mgr, res)
        if not local:
            missing += 1
            continue
        fname = os.path.splitext(local.get("file_name") or "")[0]
        if not fname:
            continue
        try:
            weight = round(float(res.get("weight", 1) or 1), 3)
        except (TypeError, ValueError):
            weight = 1
        if not math.isfinite(weight):  # 脏数据里的 nan/inf 不能进 <lora:...> 串
            weight = 1
        parts.append(f"<lora:{fname}:{weight:g}>")
        names.append(fname)
        # 触发词：本地完整记录里有 trained_words（扫描/匹配时提取，无需 API）
        full = mgr.db.get_by_path(local.get("file_path") or "")
        for w in ((full or {}).get("trained_words") or []):
            w = str(w or "").strip()
            if w and w.lower() not in seen_trig:
                seen_trig.add(w.lower())
                triggers.append(w)

    syntax = " ".join(parts)
    trigger_str = ", ".join(triggers)
    # 合并串：LoRA 语法 + 触发词，粘进 prompt 即用
    combined = syntax
    if trigger_str:
        combined = f"{syntax}, {trigger_str}" if syntax else trigger_str

    return web.json_response({
        "success": True,
        "syntax": syntax,
        "triggers": trigger_str,
        "combined": combined,
        "included": len(parts),
        "trigger_count": len(triggers),
        "missing": missing,
        "names": names,
    })


@_safe_handler
async def api_recipe_fetch_missing(request):
    """对某个配方（workflow_images 一行）遍历资源，把本地缺失的 CivitAI 资源批量加入下载队列。

    Body: { recipe_id: int }
    返回: { success, missing, already_local, started: [{download_id, file_name, model_id, version_id}],
            failed: [{model_id, version_id, error}] }
    """
    from .routes_downloads import _start_civitai_download_by_ref

    data = await request.json()
    try:
        recipe_id = int(data.get("recipe_id") or 0)
    except (TypeError, ValueError):
        recipe_id = 0
    if not recipe_id:
        return web.json_response({"success": False, "error": "缺少 recipe_id"})

    mgr = get_manager()
    recipe = mgr.db.get_workflow_image(recipe_id)
    if not recipe:
        return web.json_response({"success": False, "error": "配方不存在"})

    resources = recipe.get("resources") or []
    if not resources:
        return web.json_response({
            "success": True, "missing": 0, "already_local": 0,
            "started": [], "failed": [],
        })

    started = []
    failed = []
    already_local = 0

    for res in resources:
        local = _resource_is_local(mgr, res)
        if local:
            already_local += 1
            continue

        model_id = res.get("modelId")
        version_id = res.get("modelVersionId") or res.get("versionId")
        if not model_id and not version_id:
            # 没有任何可用引用，跳过（比如纯 embedding 名无 CivitAI 映射）
            failed.append({
                "model_id": None, "version_id": None,
                "name": res.get("name", ""),
                "error": "无 modelId / versionId，无法下载",
            })
            continue

        try:
            result = await _start_civitai_download_by_ref(mgr, model_id, version_id)
        except Exception as e:
            logger.error("[Noctyra-WF] fetch-missing 异常: %s", e, exc_info=True)
            result = {"success": False, "error": str(e)}

        if result.get("success"):
            started.append({
                "download_id": result.get("download_id"),
                "file_name": result.get("file_name"),
                "model_id": model_id,
                "version_id": version_id,
            })
        else:
            failed.append({
                "model_id": model_id,
                "version_id": version_id,
                "name": res.get("name", ""),
                "error": result.get("error", "未知错误"),
                "already_exists": result.get("already_exists", False),
            })

    missing = len(resources) - already_local
    logger.info(
        "[Noctyra-WF] 配方 %s 补全：总 %d，已本地 %d，启动 %d，失败 %d",
        recipe_id, len(resources), already_local, len(started), len(failed)
    )
    return web.json_response({
        "success": True,
        "total": len(resources),
        "missing": missing,
        "already_local": already_local,
        "started": started,
        "failed": failed,
    })


@_safe_handler
async def api_recipe_by_fingerprint(request):
    """按指纹查所有同配方图。

    Query: ?fingerprint=<hex> [&exclude_id=<int>]
    返回 {success, recipes: [...]}（最多 200 条，按 saved_at 降序）
    """
    fingerprint = (request.query.get("fingerprint") or "").strip().lower()
    if not fingerprint or len(fingerprint) != 64:
        return web.json_response({"success": False, "error": "fingerprint 必须是 64 字符 SHA256"})

    try:
        exclude_id = int(request.query.get("exclude_id") or 0)
    except (TypeError, ValueError):
        exclude_id = 0

    mgr = get_manager()
    recipes = mgr.db.list_workflow_images_by_fingerprint(fingerprint, exclude_id=exclude_id)
    return web.json_response({"success": True, "recipes": recipes, "count": len(recipes)})


# 批量导入运行状态（后台跑，刷新页面后前端可查到并恢复进度弹窗）
_BATCH_IMPORT = {"running": False, "total": 0}


@_safe_handler
async def api_recipe_batch_import(request):
    """批量导入 CivitAI 图片 URL 列表，服务端并发 fetch + save。

    Body: { urls: [string] }
    WS 事件: "recipe_import_progress" 推进度（stage=progress/complete/error）
    返回: { success, started: true }（立即返回，进度通过 WS 推送）
    """
    from .websocket import get_progress_ws

    data = await request.json()
    urls_in = data.get("urls") or []
    if not isinstance(urls_in, list):
        return web.json_response({"success": False, "error": "urls 必须是字符串数组"})

    # 解析 + 去重
    tasks = []
    seen = set()
    for u in urls_in:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u:
            continue
        img_id = _parse_civitai_image_id(u)
        if not img_id or img_id in seen:
            continue
        seen.add(img_id)
        tasks.append({"url": u, "image_id": img_id})

    if not tasks:
        return web.json_response({"success": False, "error": "没有可解析的 CivitAI 图片链接"})

    if _BATCH_IMPORT["running"]:
        return web.json_response({"success": False, "error": "已有批量导入在进行中"})

    mgr = get_manager()
    ws = get_progress_ws()
    _BATCH_IMPORT.update({"running": True, "total": len(tasks)})

    async def run():
        total = len(tasks)
        done = 0
        ok = 0
        dup = 0
        fail = 0
        sem = asyncio.Semaphore(3)
        lock = asyncio.Lock()

        async def one(t):
            nonlocal done, ok, dup, fail
            async with sem:
                try:
                    info = await mgr.civitai.get_image_info(t["image_id"])
                    if not info:
                        async with lock:
                            fail += 1
                            done += 1
                        await ws.broadcast("recipe_import_progress", {
                            "stage": "progress", "current": done, "total": total,
                            "file": t["url"], "result": "failed: 无法获取元数据",
                        })
                        return

                    # 复用 api_extension_save_image 的 meta 提炼逻辑
                    raw_meta = info.get("meta") or {}
                    if "meta" in raw_meta and isinstance(raw_meta["meta"], dict):
                        meta = {**raw_meta["meta"]}
                        for k, v in raw_meta.items():
                            if k != "meta" and k not in meta:
                                meta[k] = v
                    else:
                        meta = raw_meta

                    resources = (
                        meta.get("resources")
                        or meta.get("civitaiResources")
                        or _extract_comfy_resources(meta.get("comfy"))
                    )
                    if not resources:
                        resources = await _lookup_resources_from_version_ids(
                            mgr, info.get("modelVersionIds") or []
                        )
                    resources = await _enrich_resources(mgr, resources)

                    image_url = info.get("url", "")
                    if not image_url:
                        async with lock:
                            fail += 1
                            done += 1
                        await ws.broadcast("recipe_import_progress", {
                            "stage": "progress", "current": done, "total": total,
                            "file": t["url"], "result": "failed: CivitAI 未返回图片 URL",
                        })
                        return

                    image_info = {
                        "id": info.get("id"),
                        "url": image_url,
                        "width": info.get("width"),
                        "height": info.get("height"),
                        "nsfw_level": info.get("nsfwLevel", 0),
                        "meta": meta,
                        "resources": resources,
                        "has_workflow": bool(
                            meta.get("comfy") or meta.get("comfy_workflow") or meta.get("workflow")
                        ),
                    }

                    result = await _download_and_save_image(mgr, image_url, image_info, False)
                    async with lock:
                        done += 1
                        if result.get("already_exists"):
                            dup += 1
                            status = "duplicate"
                        elif result.get("success"):
                            ok += 1
                            status = "ok"
                        else:
                            fail += 1
                            status = f"failed: {result.get('error', '')}"
                    await ws.broadcast("recipe_import_progress", {
                        "stage": "progress", "current": done, "total": total,
                        "file": t["url"], "result": status,
                    })
                except Exception as e:
                    logger.error("[Noctyra-WF] 批量导入异常: %s", e, exc_info=True)
                    async with lock:
                        fail += 1
                        done += 1
                    await ws.broadcast("recipe_import_progress", {
                        "stage": "progress", "current": done, "total": total,
                        "file": t["url"], "result": f"failed: {e}",
                    })

        try:
            await asyncio.gather(*(one(t) for t in tasks))
            await ws.send_complete("recipe_import_progress", {
                "total": total, "ok": ok, "duplicate": dup, "failed": fail,
            })
            logger.info("[Noctyra-WF] 批量导入完成: 总 %d, 成功 %d, 重复 %d, 失败 %d",
                        total, ok, dup, fail)
        except Exception as e:
            logger.error("[Noctyra-WF] 批量导入任务异常: %s", e, exc_info=True)
            await ws.send_error("recipe_import_progress", str(e))
        finally:
            _BATCH_IMPORT["running"] = False

    spawn_background(run())
    return web.json_response({"success": True, "started": True, "total": len(tasks)})


@_safe_handler
async def api_recipe_batch_import_status(request):
    """批量导入运行状态，供刷新页面后恢复进度弹窗。"""
    return web.json_response({"success": True, **_BATCH_IMPORT})
