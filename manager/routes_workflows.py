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
工作流路由主文件：图库 CRUD + 本地导入 + 图片服务 + check-resources。

其他端点已按功能拆到同级文件：
  - `routes_workflows_fetch.py`   ：CivitAI fetch/save + 扩展一步保存
  - `routes_workflows_recipes.py` ：配方补全/同指纹查重/批量导入
  - `routes_workflows_common.py`  ：共享 helpers

以下 re-export 保持 `from .routes_workflows import X` 旧调用路径兼容。
"""

import asyncio
import math
import os
from aiohttp import web

from .routes_common import _safe_handler, get_manager, logger, parse_int, path_within_roots, spawn_background
from .image_meta import extract_image_meta
from .routes_workflows_common import (
    _enrich_resources,  # noqa: F401 给老调用点留一手
    _compute_resource_status,
    _build_local_index,
)

# ---- re-export：兼容原来 from .routes_workflows import <handler> 的调用路径 ----
from .routes_workflows_fetch import (  # noqa: F401
    api_wf_fetch_civitai_image,
    api_wf_save_civitai_image,
    api_extension_save_image,
)
from .routes_workflows_recipes import (  # noqa: F401
    api_recipe_fetch_missing,
    api_recipe_by_fingerprint,
    api_recipe_batch_import,
    api_recipe_batch_import_status,
    api_recipe_lora_syntax,
)


# ==================== 图库 CRUD ====================

@_safe_handler
async def api_wf_gallery_list(request):
    """图库列表（分页 + 搜索 + tag 筛选 + 收藏过滤 + 资源完整度过滤）

    resources_filter = all (默认) / missing（有资源缺失） / complete（资源齐全）
    列表项附 resource_status = {total, missing, complete}，前端可据此显示徽章
    """
    mgr = get_manager()
    # 夹紧分页参数：裸 int() 遇到非法值会 500，page_size=0 会让 ceil(total/0) 抛除零
    page = parse_int(request.query.get("page", 1), default=1, minimum=1)
    page_size = parse_int(request.query.get("page_size", 40), default=40, minimum=1, maximum=200)
    search = request.query.get("search", "")
    tag = request.query.get("tag", "")
    favorite_only = request.query.get("favorite", "").lower() in ("1", "true", "yes")
    has_workflow_only = request.query.get("workflow", "").lower() in ("1", "true", "yes")
    resources_filter = request.query.get("resources_filter", "").lower()
    fmt = request.query.get("format", "")
    media = request.query.get("media", "").lower()
    folder = request.query.get("folder", "")  # Billfish 文件夹过滤（该目录及子目录）
    # 图库 NSFW 设置：优先图库独立键，未设置则回退到全局（show_only_sfw / nsfw_blur_threshold）
    # 画布图像选择器会带 sfw=0/1 直接覆盖（不写配置，独立控制），其余场景读配置。
    sfw_q = request.query.get("sfw")
    if sfw_q not in (None, ""):
        sfw_only = sfw_q.lower() in ("1", "true", "yes")
    else:
        sfw_only = bool(mgr.config.get("gallery_show_only_sfw", mgr.config.get("show_only_sfw")))
    nsfw_threshold = int(
        mgr.config.get("gallery_nsfw_blur_threshold", mgr.config.get("nsfw_blur_threshold")) or 4
    )

    if resources_filter in ("missing", "complete"):
        # 过滤模式：拉全量匹配项，Python 端按资源状态过滤再分页
        full = mgr.db.list_workflow_images(
            page=1, page_size=100000,
            search=search, tag=tag, favorite_only=favorite_only,
            sfw_only=sfw_only, nsfw_threshold=nsfw_threshold,
            has_workflow_only=has_workflow_only, fmt=fmt, media=media,
            folder=folder,
        )
        local_index = _build_local_index(mgr, full["images"])  # 两次批量查库，替代逐资源 N+1
        filtered = []
        for img in full["images"]:
            status = _compute_resource_status(mgr, img.get("resources"), local_index=local_index)
            img["resource_status"] = status
            if resources_filter == "missing" and status["missing"] > 0:
                filtered.append(img)
            elif resources_filter == "complete" and status["complete"] and status["total"] > 0:
                filtered.append(img)
        total = len(filtered)
        offset = (page - 1) * page_size
        images = filtered[offset:offset + page_size]
        return web.json_response({
            "success": True,
            "images": images,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, math.ceil(total / page_size) if total else 1),
        })

    # 常规路径：只给当前页算资源状态
    result = mgr.db.list_workflow_images(
        page, page_size, search, tag, favorite_only,
        sfw_only=sfw_only, nsfw_threshold=nsfw_threshold,
        has_workflow_only=has_workflow_only, fmt=fmt, media=media,
        folder=folder,
    )
    local_index = _build_local_index(mgr, result["images"])  # 当前页也批量预取，免 N+1
    for img in result["images"]:
        img["resource_status"] = _compute_resource_status(mgr, img.get("resources"), local_index=local_index)
    return web.json_response({"success": True, **result})


@_safe_handler
async def api_wf_gallery_tags(request):
    """返回图库里出现过的所有 tag + 计数"""
    mgr = get_manager()
    tags = mgr.db.list_workflow_tags()
    return web.json_response({"success": True, "tags": [
        {"name": name, "count": count} for name, count in tags
    ]})


@_safe_handler
async def api_wf_gallery_formats(request):
    """返回图库里出现过的文件格式（扩展名）+ 计数，供格式筛选用"""
    mgr = get_manager()
    formats = mgr.db.list_workflow_image_formats()
    return web.json_response({"success": True, "formats": [
        {"name": name, "count": count} for name, count in formats
    ]})


# ==================== Billfish 文件夹模型 ====================

# 图库扫描运行状态（后台跑，刷新页面后前端可从 folders 接口查到并恢复"扫描中"）
_GALLERY_SCAN = {"running": False, "result": None}


@_safe_handler
async def api_wf_gallery_folders(request):
    """返回注册文件夹树（含递归图片数），供图库左侧文件夹栏。

    附带扫描运行状态 scanning + 上次结果 last_scan，供刷新后恢复"扫描中"按钮。"""
    from .gallery_scanner import build_folder_tree
    mgr = get_manager()
    loop = asyncio.get_running_loop()
    counts = await loop.run_in_executor(None, mgr.db.gallery_dir_counts)
    # build_folder_tree 会对每个注册根做 os.scandir，慢盘/大目录会阻塞事件循环 → 卸载线程池
    tree = await loop.run_in_executor(None, build_folder_tree, mgr.config, counts)
    return web.json_response({
        "success": True, "folders": tree,
        "scanning": _GALLERY_SCAN["running"], "last_scan": _GALLERY_SCAN["result"],
    })


@_safe_handler
async def api_wf_gallery_scan(request):
    """手动扫描所有注册文件夹（后台跑，立即返回）。前端轮询 folders 接口看是否完成。"""
    from .gallery_scanner import scan_gallery
    mgr = get_manager()
    if _GALLERY_SCAN["running"]:
        return web.json_response({"success": True, "running": True})

    _GALLERY_SCAN["running"] = True
    _GALLERY_SCAN["result"] = None
    loop = asyncio.get_running_loop()

    async def _run():
        try:
            _GALLERY_SCAN["result"] = await loop.run_in_executor(None, scan_gallery, mgr.db, mgr.config)
        except Exception as e:
            logger.warning("[Noctyra-WF] 图库扫描失败: %s", e)
            _GALLERY_SCAN["result"] = {"error": str(e)}
        finally:
            _GALLERY_SCAN["running"] = False

    # 别用裸 asyncio.create_task：任务可能被 GC 回收 → _run 的 finally 不执行 →
    # running 永远 True，"扫描中"卡死需重启。spawn_background 持引用直到任务结束。
    spawn_background(_run())
    return web.json_response({"success": True, "started": True})


@_safe_handler
async def api_wf_gallery_folder_add(request):
    """注册一个真实文件夹（原地索引，不拷贝）。body: {path, name?}"""
    mgr = get_manager()
    data = await request.json()
    path = (data.get("path") or "").strip()
    if not path:
        return web.json_response({"success": False, "error": "缺少路径"})
    if not os.path.isdir(path):
        return web.json_response({"success": False, "error": "路径不存在或不是文件夹"})

    def _norm(p):
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))

    nkey = _norm(path)
    # 与内置/已注册去重
    if any(_norm(f["path"]) == nkey for f in mgr.config.gallery_folders):
        return web.json_response({"success": False, "error": "该文件夹已存在"})

    raw = list(mgr.config.get("gallery_folders", []) or [])
    raw.append({
        "path": path,
        "name": (data.get("name") or os.path.basename(path.rstrip("\\/")) or path),
        "enabled": True,
    })
    mgr.config.set("gallery_folders", raw)
    mgr.config.save()
    return web.json_response({"success": True, "folders": mgr.config.gallery_folders})


@_safe_handler
async def api_wf_gallery_folder_remove(request):
    """取消注册文件夹并删除其索引记录（磁盘文件不动）。body: {path}"""
    mgr = get_manager()
    data = await request.json()
    path = (data.get("path") or "").strip()
    if not path:
        return web.json_response({"success": False, "error": "缺少路径"})

    def _norm(p):
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))

    nkey = _norm(path)
    # 内置「下载/导入」不可删
    builtin = mgr.config.workflow_gallery_dir
    if _norm(builtin) == nkey:
        return web.json_response({"success": False, "error": "内置文件夹不可移除"})

    raw = list(mgr.config.get("gallery_folders", []) or [])
    new_raw = [f for f in raw if _norm((f.get("path") or "")) != nkey]
    mgr.config.set("gallery_folders", new_raw)
    mgr.config.save()
    loop = asyncio.get_running_loop()
    removed = await loop.run_in_executor(None, mgr.db.delete_gallery_under, path)
    return web.json_response({
        "success": True,
        "removed_records": removed,
        "folders": mgr.config.gallery_folders,
    })


@_safe_handler
async def api_wf_gallery_detail(request):
    """图库单条详情"""
    mgr = get_manager()
    image_id = parse_int(request.match_info["id"])
    if image_id is None:
        return web.json_response({"success": False, "error": "无效的 id"}, status=404)
    image = mgr.db.get_workflow_image(image_id)
    if not image:
        return web.json_response({"success": False, "error": "not found"})

    # 旧记录的 resources 可能缺少 name（civitaiResources 只返回 modelVersionId）
    # 按需补全并回写，之后的访问就不用再查
    resources = image.get("resources") or []
    needs_fill = any(
        (not r.get("name")) and (r.get("modelVersionId") or r.get("modelVersionID"))
        for r in resources
    )
    if needs_fill:
        # 补资源名要联网查 CivitAI，慢/限流时别把详情响应拖住（会表现为点开详情一片空白）。
        # 最多等 2.5s，超时就先返回（名称留待下次打开时再补），保证详情秒开。
        try:
            enriched = await asyncio.wait_for(_enrich_resources(mgr, resources), timeout=2.5)
        except asyncio.TimeoutError:
            enriched = None
        if enriched:
            image["resources"] = enriched
            try:
                # update_workflow_image 会顺带重算指纹（resources 变更 → fingerprint 变更）
                mgr.db.update_workflow_image(image_id, {"resources": enriched})
            except Exception as e:
                logger.debug("[Noctyra-WF] 回写 resources 失败: %s", e)

    # 老记录（recipe_version=0）按需回填指纹
    if not image.get("recipe_version"):
        try:
            new_fp = mgr.db.backfill_workflow_fingerprint(image_id)
            if new_fp:
                image["fingerprint"] = new_fp
                image["recipe_version"] = 1
        except Exception as e:
            logger.debug("[Noctyra-WF] 回填指纹失败: %s", e)

    return web.json_response({"success": True, "image": image})


@_safe_handler
async def api_wf_gallery_delete(request):
    """删除图库记录 + 本地文件"""
    data = await request.json()
    image_id = data.get("id")
    delete_file = data.get("delete_file", False)
    if not image_id:
        return web.json_response({"success": False, "error": "缺少 id"})

    mgr = get_manager()
    image = mgr.db.get_workflow_image(int(image_id))
    if not image:
        return web.json_response({"success": False, "error": "not found"})

    if delete_file and image.get("file_path"):
        # 数据安全：只删 App 自己存的副本（内置图库目录 / 缓存目录）。用户在 Billfish
        # 注册目录里的图是唯一原件，一律只删索引、绝不碰磁盘——防一次点击就永久删掉
        # 用户的原图（这些目录是 ComfyUI output 等真实目录，无回收站）。
        app_owned_roots = [r for r in (mgr.config.workflow_gallery_dir, mgr.config.cache_dir) if r]
        if path_within_roots(image["file_path"], app_owned_roots):
            try:
                if os.path.exists(image["file_path"]):
                    os.remove(image["file_path"])
                    logger.info("[Noctyra-WF] 已删除 App 副本文件: %s", image["file_path"])
            except OSError as e:
                logger.warning("[Noctyra-WF] 删除文件失败: %s", e)
        else:
            logger.info("[Noctyra-WF] 用户原件受保护，仅删索引不删磁盘: %s", image["file_path"])

    mgr.db.delete_workflow_image(int(image_id))
    return web.json_response({"success": True})


@_safe_handler
async def api_wf_check_resources(request):
    """检查资源列表在本地模型库中的状态"""
    data = await request.json()
    resources = data.get("resources") or []
    if not resources:
        return web.json_response({"success": True, "results": []})

    mgr = get_manager()
    results = []

    for res in resources:
        name = res.get("name", "")
        version_id = res.get("modelVersionId")
        model_id = res.get("modelId")
        res_type = res.get("type", "")

        local = None

        # 客户端传来的 id 可能是非数字字符串，裸 int() 会 500；parse_int 非法即跳过该次查库
        # （与 _resource_is_local 的容错一致），仍会回退 model_id / name 匹配。
        if version_id:
            vid = parse_int(version_id)
            if vid is not None:
                rows = mgr.db.query_by_version_id(vid)
                if rows:
                    local = rows[0]

        if not local and model_id:
            mid = parse_int(model_id)
            if mid is not None:
                rows = mgr.db.query_by_model_id(mid)
                if rows:
                    local = rows[0]

        if not local and name:
            base_name = name.split(".")[0].strip()
            if base_name:
                all_models, _ = mgr.db.get_all(
                    filters={"search": base_name},
                    sort_by="file_name", page=1, page_size=5
                )
                if all_models:
                    local = all_models[0]

        results.append({
            "name": name,
            "type": res_type,
            "version_id": version_id,
            "model_id": model_id,
            "found": local is not None,
            "local_path": local.get("file_path", "") if local else "",
            "local_name": local.get("model_name") or local.get("file_name", "") if local else "",
            "local_sha256": (local.get("sha256") or "") if local else "",
        })

    return web.json_response({"success": True, "results": results})


@_safe_handler
async def api_wf_import_local(request):
    """上传本地图片导入到图库"""
    reader = await request.multipart()
    field = await reader.next()
    if not field or field.name != 'file':
        return web.json_response({"success": False, "error": "缺少文件"})

    # basename 化，防 multipart filename 带 ../ 或绝对路径逃出图库目录
    filename = os.path.basename(field.filename or "upload.png") or "upload.png"
    mgr = get_manager()

    save_dir = mgr.config.workflow_gallery_dir
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)

    counter = 1
    base, ext = os.path.splitext(save_path)
    while os.path.exists(save_path):
        save_path = f"{base}_{counter}{ext}"
        counter += 1

    with open(save_path, "wb") as f:
        while True:
            chunk = await field.read_chunk(256 * 1024)
            if not chunk:
                break
            f.write(chunk)

    loop = asyncio.get_running_loop()
    embed = await loop.run_in_executor(None, extract_image_meta, save_path)

    try:
        from PIL import Image
        img = Image.open(save_path)
        width, height = img.size
    except Exception:
        width, height = None, None

    record_id = mgr.db.save_workflow_image({
        "file_path": save_path,
        "file_name": os.path.basename(save_path),
        "source": "local",
        "source_url": "",
        "civitai_image_id": None,
        "width": width,
        "height": height,
        "nsfw_level": 0,
        "meta": embed.get("parsed", {}),
        "resources": [],
        "has_workflow": bool(embed.get("workflow")),
        "workflow_json": embed.get("workflow"),
        "api_prompt_json": embed.get("api_prompt"),
        "parameters_text": embed.get("parameters", ""),
        "parsed_params": embed.get("parsed", {}),
        "embed_source": embed.get("source_type", "none"),
    })

    logger.info("[Noctyra-WF] 本地图片已导入: %s (id=%s)", filename, record_id)
    return web.json_response({
        "success": True,
        "id": record_id,
        "file_path": save_path,
        "has_workflow": bool(embed.get("workflow")),
        "source_type": embed.get("source_type", "none"),
    })


@_safe_handler
async def api_wf_update_info(request):
    """更新图库记录的名称和标签"""
    data = await request.json()
    image_id = parse_int(data.get("id"))
    if image_id is None:
        return web.json_response({"success": False, "error": "无效的 id"}, status=404)

    mgr = get_manager()
    image = mgr.db.get_workflow_image(image_id)
    if not image:
        return web.json_response({"success": False, "error": "not found"})

    updates = {}
    if "custom_name" in data:
        updates["custom_name"] = data["custom_name"]
    if "tags" in data:
        updates["tags"] = data["tags"]
    if "notes" in data:
        updates["notes"] = data["notes"]
    if "favorite" in data:
        updates["favorite"] = 1 if data["favorite"] else 0
    if "user_nsfw" in data:
        updates["user_nsfw"] = 1 if data["user_nsfw"] else 0

    if updates:
        mgr.db.update_workflow_image(image_id, updates)

    return web.json_response({"success": True})


@_safe_handler
async def api_wf_cleanup_missing(request):
    """清理 file_path 已不存在于磁盘的图库记录"""
    mgr = get_manager()
    removed = mgr.db.cleanup_missing_workflow_images()
    return web.json_response({"success": True, "removed": removed})


@_safe_handler
async def api_model_recipes(request):
    """反查：哪些图库配方用过这个模型（给模型详情页"被哪些配方用过"用）。
    Query: ?version_id=&model_id=  →  { success, recipes: [{id, file_name, custom_name, media_type}] }"""
    vid = parse_int(request.query.get("version_id"))
    mid = parse_int(request.query.get("model_id"))
    if not vid and not mid:
        return web.json_response({"success": True, "recipes": []})
    mgr = get_manager()
    recipes = mgr.db.get_workflow_images_for_model(version_id=vid, model_id=mid)
    return web.json_response({"success": True, "recipes": recipes})


def _gallery_serve_roots(mgr):
    """图库 serve / copy 端点允许读取文件的根目录白名单：所有注册文件夹（含内置
    「下载/导入」目录）+ 缓存目录。防残留/陈旧 DB 记录借图片服务把库外任意文件读出去
    （与 gallery_delete 的 path_within_roots 防护对齐）。"""
    roots = [f.get("path") for f in mgr.config.gallery_folders if f.get("path")]
    if mgr.config.cache_dir:
        roots.append(mgr.config.cache_dir)
    return roots


@_safe_handler
async def api_wf_serve_image(request):
    """本地图库图片服务"""
    try:
        image_id = int(request.match_info["id"])
    except (ValueError, TypeError):
        return web.Response(status=404)
    mgr = get_manager()
    image = mgr.db.get_workflow_image(image_id)
    if not image or not image.get("file_path"):
        return web.Response(status=404)

    file_path = image["file_path"]
    if not os.path.isfile(file_path):
        return web.Response(status=404)

    # 白名单校验：残留/陈旧记录的 file_path 可能指向注册文件夹外的任意文件，
    # 未落在允许根内一律 404，别把库外文件通过图片服务读出去。
    if not path_within_roots(file_path, _gallery_serve_roots(mgr)):
        return web.Response(status=404)

    # 图库卡片用 480px WebP 缩略图（原图保持不动，详情/放大拿原图）
    serve_path = file_path
    if request.query.get("size") == "card":
        from .routes_common import make_card_thumb
        thumb = await make_card_thumb(file_path)
        if thumb:
            serve_path = thumb

    resp = web.FileResponse(serve_path)
    ext = os.path.splitext(serve_path)[1].lstrip(".").lower()
    resp.headers["Content-Type"] = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
        "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    }.get(ext, "application/octet-stream")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


def _comfyui_input_dir():
    """ComfyUI 的 input 目录。优先用官方 folder_paths，拿不到则按目录结构回退。"""
    try:
        import folder_paths  # ComfyUI 运行时一定在 sys.path
        d = folder_paths.get_input_directory()
        if d:
            return d
    except Exception:
        pass
    # 回退：custom_nodes/<plugin>/manager/routes_workflows.py → 上溯到 ComfyUI 根
    here = os.path.abspath(__file__)
    comfy_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
    return os.path.join(comfy_root, "input")


# 图库图片统一落到 input 下这个子目录，方便用户整文件夹清理（不污染 input 根目录）。
INPUT_SUBDIR = "NoctyraInput"


@_safe_handler
async def api_wf_copy_to_input(request):
    """把图库图片复制进 ComfyUI 的 input/NoctyraInput 子目录，供 LoadImage / 视频节点选用。

    POST /api/noctyra/workflow/image/{id}/to-input
    返回 { success, filename }，filename 形如 "NoctyraInput/xxx.png"，即节点 widget 取值
    （ComfyUI 子目录取值的加载/预览/校验均按 input 根解析，和 clipspace 同机制）。
    复用同名同大小的已有文件，避免反复选同一张时堆重复。
    """
    try:
        image_id = int(request.match_info["id"])
    except (ValueError, TypeError):
        return web.json_response({"success": False, "error": "invalid id"}, status=400)

    mgr = get_manager()
    image = mgr.db.get_workflow_image(image_id)
    if not image or not image.get("file_path"):
        return web.json_response({"success": False, "error": "图片不存在"}, status=404)
    src = image["file_path"]
    if not os.path.isfile(src):
        return web.json_response({"success": False, "error": "源文件已丢失"}, status=404)

    # 白名单校验：残留/陈旧记录可能指向注册文件夹外的任意文件，拒绝把库外文件拷进 input。
    if not path_within_roots(src, _gallery_serve_roots(mgr)):
        return web.json_response({"success": False, "error": "文件不在图库范围内"}, status=404)

    target_dir = os.path.join(_comfyui_input_dir(), INPUT_SUBDIR)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e:
        return web.json_response({"success": False, "error": f"input 目录不可写: {e}"}, status=500)

    # widget 取值用正斜杠拼子目录，跨平台稳妥
    ret = lambda name: web.json_response({"success": True, "filename": f"{INPUT_SUBDIR}/{name}"})

    base, ext = os.path.splitext(os.path.basename(src))
    src_size = os.path.getsize(src)
    # 用图库 id 给文件名加唯一后缀：同一张稳定复用，不同图绝不撞名
    # （避免"恰好同名同大小"被误判成同一张而载入别人的图）。
    filename = f"{base}_{image_id}{ext}"
    target = os.path.join(target_dir, filename)
    if os.path.exists(target):
        try:
            if os.path.getsize(target) == src_size:
                return ret(filename)   # 已在且完整 → 直接复用
        except OSError:
            pass
        # 残缺 / 大小不符 → 落到下面覆盖重拷

    import shutil
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, shutil.copy2, src, target)
    except OSError as e:
        return web.json_response({"success": False, "error": f"复制失败: {e}"}, status=500)

    logger.info("[Noctyra-WF] 图库图片已送入 input: %s", target)
    return ret(os.path.basename(target))
