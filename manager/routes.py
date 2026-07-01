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
aiohttp 路由入口：装配所有端点到 ComfyUI 的 PromptServer。

子模块：
  - routes_common.py     : get_manager / _get_preview_cache / _safe_handler
  - routes_models.py     : 模型 CRUD / 标签 / base_model / 收藏 / 笔记 / 自定义 / 绑定 / 删除
  - routes_ops.py        : scan / match / organize / rebuild / batch / duplicates / check-update
  - routes_downloads.py  : civitai/hf 版本 / download / import / extension
  - routes_previews.py   : preview 代理 / 本地 sidecar / 上传 / 预热 / 清理

本文件保留：settings/detect-dirs、export/import、page_manager、noctyra_static_handler、setup_routes。
"""

import os
import re
import mimetypes
from aiohttp import web

# 默认 mimetypes 不识别 woff2 → FileResponse 会发 application/octet-stream，注册一下
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

# 向后兼容：manager.py / usage_tracker.py 仍从 .routes 导入这两个
from .routes_common import _safe_handler, get_manager, _get_preview_cache, logger  # noqa: F401
from .config import get_config
from .websocket import get_progress_ws

from .routes_models import (
    api_models_list, api_model_detail, api_model_safetensors, api_picker_match, api_folders,
    api_tags, api_add_tags, api_remove_tag, api_set_tags,
    api_move_model, api_base_models, api_base_model_stats, api_refresh_base_models,
    api_status, api_bind, api_favorite, api_notes, api_update_custom,
    api_check_integrity, api_redownload,
    api_delete_model, api_soft_delete_model, api_restore_model,
    api_filter_presets_list, api_filter_presets_save, api_filter_presets_delete,
    api_model_versions, api_version_ignore, api_version_unignore,
    api_reveal_in_explorer, api_trigger_words,
)
from .routes_ops import (
    api_scan, api_match, api_check_model_updates, api_match_single,
    api_cancel_match, api_cancel_prewarm,
    api_organize_single, api_organize_preview, api_organize_execute,
    api_rebuild, api_duplicates, api_batch_delete, api_batch_refresh, api_statistics,
    api_check_update,
    api_batch_tag, api_batch_set_base_model, api_batch_move,
)
from .routes_downloads import (
    api_civitai_versions, api_hf_files,
    api_download_model, api_downloads_list,
    api_download_cancel, api_download_remove, api_download_clear, api_download_retry,
    api_download_pause, api_download_resume, api_download_redownload,
    api_import_upload, api_import_path,
    api_extension_check, api_extension_download, api_extension_ping,
)
from .routes_previews import (
    api_prewarm_previews, api_upload_preview, api_local_preview,
    api_preview, api_cleanup_previews, api_cache_stats, api_clear_thumbs,
)
from .routes_workflows import (
    api_wf_fetch_civitai_image, api_wf_save_civitai_image,
    api_wf_gallery_list, api_wf_gallery_detail, api_wf_gallery_tags, api_wf_gallery_formats,
    api_wf_gallery_folders, api_wf_gallery_scan,
    api_wf_gallery_folder_add, api_wf_gallery_folder_remove,
    api_wf_gallery_delete, api_wf_cleanup_missing, api_wf_serve_image, api_wf_copy_to_input,
    api_wf_check_resources, api_wf_import_local, api_wf_update_info, api_model_recipes,
    api_extension_save_image, api_recipe_fetch_missing, api_recipe_batch_import, api_recipe_batch_import_status,
    api_recipe_by_fingerprint, api_recipe_lora_syntax,
)


# ==================== 设置 ====================

# 不暴露给前端的 key 列表
_HIDDEN_KEYS = {"cache_dir", "server_port"}
# 密钥字段：只返回是否已设置
_SECRET_KEYS = {"civitai_api_key", "huggingface_token", "proxy_password"}


@_safe_handler
async def api_settings_get(request):
    """获取设置"""
    from .routes_common import get_runtime_mode
    config = get_config()
    settings = {}
    for k, v in config._data.items():
        if k in _HIDDEN_KEYS:
            continue
        if k in _SECRET_KEYS:
            settings[k] = "***" if v else ""
        else:
            settings[k] = v
    # 运行模式信息（前端据此显示"独立模式"徽章）
    settings["_runtime_mode"] = get_runtime_mode()
    # 图库实际路径：若 workflow_gallery_dir 为空则解析成默认 <plugin_dir>/gallery/
    settings["_workflow_gallery_dir_resolved"] = config.workflow_gallery_dir
    # 存档夹实际路径：若 archive_dir 为空则解析成默认 <plugin_dir>/archive/
    settings["_archive_dir_resolved"] = config.archive_dir
    # 项目文件夹（data_root）状态，供设置页展示
    settings["_data_root"] = config.data_root                  # ''=传统插件目录模式
    settings["_data_root_active"] = bool(config.data_root)
    settings["_data_root_missing"] = config.data_root_missing
    settings["_plugin_dir"] = config.plugin_dir
    settings["_cache_dir_resolved"] = config.cache_dir
    return web.json_response({"success": True, "settings": settings})


@_safe_handler
async def api_settings_save(request):
    """保存设置（支持增量更新）"""
    data = await request.json()
    config = get_config()

    # 存档夹不能设在模型扫描根内：否则扫描会把存档文件当成在库模型重新收录，软删除白做
    if "archive_dir" in data:
        ad = (data.get("archive_dir") or "").strip()
        if ad:
            ad_abs = os.path.normcase(os.path.abspath(ad))
            for root in config.model_roots:
                r = os.path.normcase(os.path.abspath(root))
                if ad_abs == r or ad_abs.startswith(r + os.sep):
                    return web.json_response(
                        {"success": False, "error": f"存档目录不能设在模型目录内（{root}）。请换一个模型目录之外的位置。"})

    for key, value in data.items():
        if key in _HIDDEN_KEYS:
            continue
        # 密钥字段：*** 表示不修改
        if key in _SECRET_KEYS and value == "***":
            continue
        config.set(key, value)

    config.save()

    # 更新管理器的客户端
    mgr = get_manager()
    mgr.civitai.api_key = config.civitai_api_key
    mgr.huggingface.token = config.huggingface_token

    return web.json_response({"success": True})


@_safe_handler
async def api_detect_dirs(request):
    """自动检测 ComfyUI/models 子目录"""
    from .config import _detect_comfyui_model_dirs
    dirs = _detect_comfyui_model_dirs()
    return web.json_response({"success": True, "dirs": dirs})


@_safe_handler
async def api_data_root_set(request):
    """设置/迁移项目文件夹（Billfish 式数据目录）。body {path}。

    智能：目标已有库→采用；否则复制当前数据进去。复制+校验+写指针，重启生效。"""
    import asyncio
    data = await request.json()
    path = (data.get("path") or "").strip()
    if not path:
        return web.json_response({"success": False, "error": "缺少路径"})
    config = get_config()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, config.migrate_to, path)
    return web.json_response(result)


@_safe_handler
async def api_data_root_clear(request):
    """清除项目文件夹指针（下次启动回退插件目录；只删指针，不动数据）。"""
    config = get_config()
    config.write_data_root_pointer("")
    return web.json_response({"success": True, "restart": True})


# ==================== 页面 / 静态 ====================


async def page_manager(request):
    """管理器主页面"""
    html_path = os.path.join(
        os.path.dirname(__file__), "web", "index.html"
    )
    resp = web.FileResponse(html_path)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


async def page_workflows(request):
    """工作流管理页面"""
    html_path = os.path.join(
        os.path.dirname(__file__), "web", "workflows.html"
    )
    resp = web.FileResponse(html_path)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


async def page_statistics(request):
    """统计页面"""
    html_path = os.path.join(
        os.path.dirname(__file__), "web", "statistics.html"
    )
    resp = web.FileResponse(html_path)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


async def noctyra_static_handler(request):
    """静态文件服务（禁用缓存）"""
    rel_path = request.match_info["path"]
    static_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "web", "static"))
    file_path = os.path.normpath(os.path.join(static_dir, rel_path))

    # 安全检查：防止路径穿越（必须以 static_dir + sep 开头，否则 /staticX 会误通过）
    if file_path != static_dir and not file_path.startswith(static_dir + os.sep):
        return web.Response(status=403)

    if not os.path.isfile(file_path):
        return web.Response(status=404)

    resp = web.FileResponse(file_path)
    # 字体不可变且较大（~1MB/字重），长缓存避免每次刷新重下（更新字体改文件名即可）；
    # 其余静态资源沿用 no-cache（靠 ?v= 破缓存）。
    if os.path.splitext(file_path)[1].lower() in (".woff2", ".woff", ".ttf", ".otf"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ==================== 导入导出 ====================


@_safe_handler
async def api_export(request):
    """导出所有模型数据为 JSON"""
    mgr = get_manager()
    models = mgr.db.export_all()
    logger.info("[Noctyra-MM] 导出 %d 个模型数据", len(models))
    data = {
        "version": 1,
        "exported_at": __import__("time").time(),
        "count": len(models),
        "models": models,
    }
    return web.json_response(data)


@_safe_handler
async def api_import(request):
    """导入模型数据"""
    body = await request.json()
    models = body.get("models", [])
    mode = body.get("mode", "merge")
    if not models or not isinstance(models, list):
        return web.json_response({"success": False, "error": "没有可导入的数据"})
    if mode not in ("merge", "overwrite"):
        mode = "merge"

    mgr = get_manager()
    logger.info("[Noctyra-MM] 导入 %d 个模型数据 (mode=%s)", len(models), mode)
    result = mgr.db.import_models(models, mode=mode)
    return web.json_response({"success": True, **result})


# ==================== CSRF 防御中间件 ====================

# 合法 Origin：同机 http(s)://127.0.0.1|localhost|[::1] 任意端口、Chrome 扩展
# Chrome 扩展 ID 是 32 个小写字母 a-p（base16h）
_ALLOWED_ORIGIN_RE = re.compile(
    r"^(?:https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?"
    r"|chrome-extension://[a-p]+)$",
    re.IGNORECASE,
)


@web.middleware
async def _noctyra_csrf_middleware(request: web.Request, handler):
    """CSRF 防御：状态变更请求必须带合法 Origin（localhost / 本机扩展）。

    - 只作用于 /api/noctyra/* 路径，避免影响 ComfyUI 其他端点
    - 空 Origin 放行（curl / 扩展 declarativeNetRequest 剥离 Origin / 非浏览器客户端）
    - 浏览器跨源 POST 会自动带 Origin → 恶意站点被拒
    """
    if not request.path.startswith("/api/noctyra/"):
        return await handler(request)
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("Origin", "")
        if origin and not _ALLOWED_ORIGIN_RE.match(origin):
            logger.warning(
                "[Noctyra-MM] 拒绝跨源请求 (CSRF 防御): %s %s origin=%s",
                request.method, request.path, origin[:120],
            )
            return web.json_response(
                {"success": False, "error": "forbidden origin"},
                status=403,
            )
    return await handler(request)


# ==================== 注册路由 ====================


def setup_routes(app: web.Application):
    """注册所有路由到 ComfyUI 的 aiohttp app"""
    ws = get_progress_ws()

    # CSRF 中间件（aiohttp 允许运行时 append，只要 app 还没启动完成）
    if _noctyra_csrf_middleware not in app.middlewares:
        app.middlewares.append(_noctyra_csrf_middleware)

    # 管理页面
    app.router.add_get("/noctyra-manager", page_manager)
    app.router.add_get("/noctyra-workflows", page_workflows)
    app.router.add_get("/noctyra-statistics", page_statistics)

    # WebSocket 进度推送
    app.router.add_get("/api/noctyra/ws", ws.handle)

    # 模型 API
    app.router.add_get("/api/noctyra/models", api_models_list)
    app.router.add_post("/api/noctyra/picker/match", api_picker_match)
    app.router.add_get("/api/noctyra/model-safetensors", api_model_safetensors)
    app.router.add_get("/api/noctyra/models/{identifier}", api_model_detail)
    app.router.add_get("/api/noctyra/folders", api_folders)
    app.router.add_get("/api/noctyra/tags", api_tags)
    app.router.add_post("/api/noctyra/tags/add", api_add_tags)
    app.router.add_post("/api/noctyra/tags/remove", api_remove_tag)
    app.router.add_post("/api/noctyra/tags/set", api_set_tags)
    app.router.add_post("/api/noctyra/move", api_move_model)
    app.router.add_get("/api/noctyra/base-models", api_base_models)
    app.router.add_get("/api/noctyra/base-models/stats", api_base_model_stats)
    app.router.add_post("/api/noctyra/base-models/refresh", api_refresh_base_models)
    app.router.add_get("/api/noctyra/status", api_status)

    # 操作 API
    app.router.add_post("/api/noctyra/scan", api_scan)
    app.router.add_post("/api/noctyra/match", api_match)
    app.router.add_post("/api/noctyra/match-single", api_match_single)
    app.router.add_post("/api/noctyra/cancel-match", api_cancel_match)
    app.router.add_post("/api/noctyra/cancel-prewarm", api_cancel_prewarm)
    app.router.add_post("/api/noctyra/civitai-versions", api_civitai_versions)
    app.router.add_post("/api/noctyra/hf-files", api_hf_files)
    app.router.add_post("/api/noctyra/download", api_download_model)
    app.router.add_get("/api/noctyra/downloads", api_downloads_list)
    app.router.add_post("/api/noctyra/download/cancel", api_download_cancel)
    app.router.add_post("/api/noctyra/download/remove", api_download_remove)
    app.router.add_post("/api/noctyra/download/clear", api_download_clear)
    app.router.add_post("/api/noctyra/download/retry", api_download_retry)
    app.router.add_post("/api/noctyra/download/pause", api_download_pause)
    app.router.add_post("/api/noctyra/download/resume", api_download_resume)
    app.router.add_post("/api/noctyra/download/redownload", api_download_redownload)
    app.router.add_get("/api/noctyra/check-model-updates", api_check_model_updates)
    app.router.add_get("/api/noctyra/check-update", api_check_update)
    app.router.add_post("/api/noctyra/bind", api_bind)
    app.router.add_post("/api/noctyra/favorite", api_favorite)
    app.router.add_post("/api/noctyra/notes", api_notes)
    app.router.add_post("/api/noctyra/custom", api_update_custom)
    app.router.add_post("/api/noctyra/check-integrity", api_check_integrity)
    app.router.add_post("/api/noctyra/redownload", api_redownload)
    app.router.add_post("/api/noctyra/preview-upload", api_upload_preview)
    app.router.add_get("/api/noctyra/local-preview", api_local_preview)
    app.router.add_post("/api/noctyra/import-upload", api_import_upload)
    app.router.add_post("/api/noctyra/import-path", api_import_path)
    app.router.add_post("/api/noctyra/delete", api_delete_model)
    app.router.add_post("/api/noctyra/soft-delete", api_soft_delete_model)
    app.router.add_post("/api/noctyra/restore", api_restore_model)
    app.router.add_get("/api/noctyra/organize/preview", api_organize_preview)
    app.router.add_post("/api/noctyra/organize/execute", api_organize_execute)
    app.router.add_post("/api/noctyra/organize/single", api_organize_single)
    app.router.add_post("/api/noctyra/rebuild", api_rebuild)
    app.router.add_post("/api/noctyra/cleanup-previews", api_cleanup_previews)
    app.router.add_get("/api/noctyra/cache-stats", api_cache_stats)
    app.router.add_post("/api/noctyra/clear-thumbs", api_clear_thumbs)
    app.router.add_post("/api/noctyra/prewarm-previews", api_prewarm_previews)
    app.router.add_get("/api/noctyra/duplicates", api_duplicates)
    app.router.add_get("/api/noctyra/statistics", api_statistics)
    app.router.add_post("/api/noctyra/batch-delete", api_batch_delete)
    app.router.add_post("/api/noctyra/batch-refresh", api_batch_refresh)
    app.router.add_post("/api/noctyra/batch-tag", api_batch_tag)
    app.router.add_post("/api/noctyra/batch-set-base-model", api_batch_set_base_model)
    app.router.add_post("/api/noctyra/batch-move", api_batch_move)

    # 导入导出
    app.router.add_get("/api/noctyra/export", api_export)
    app.router.add_post("/api/noctyra/import", api_import)

    # 筛选预设
    app.router.add_get("/api/noctyra/filter-presets", api_filter_presets_list)
    app.router.add_post("/api/noctyra/filter-presets", api_filter_presets_save)
    app.router.add_post("/api/noctyra/filter-presets/delete", api_filter_presets_delete)

    # 版本管理
    app.router.add_get("/api/noctyra/model-versions", api_model_versions)
    app.router.add_post("/api/noctyra/version/ignore", api_version_ignore)
    app.router.add_post("/api/noctyra/version/unignore", api_version_unignore)

    # 文件系统辅助
    app.router.add_post("/api/noctyra/reveal", api_reveal_in_explorer)

    # Trigger word 聚合
    app.router.add_get("/api/noctyra/trigger-words", api_trigger_words)

    # 浏览器扩展
    app.router.add_get("/api/noctyra/extension/ping", api_extension_ping)
    app.router.add_post("/api/noctyra/extension/check", api_extension_check)
    app.router.add_post("/api/noctyra/extension/download", api_extension_download)
    app.router.add_post("/api/noctyra/extension/save-image", api_extension_save_image)

    # 预览图代理缓存
    app.router.add_get("/api/noctyra/preview", api_preview)

    # 工作流图库
    app.router.add_post("/api/noctyra/workflow/fetch-civitai-image", api_wf_fetch_civitai_image)
    app.router.add_post("/api/noctyra/workflow/save-civitai-image", api_wf_save_civitai_image)
    app.router.add_get("/api/noctyra/workflow/gallery", api_wf_gallery_list)
    app.router.add_get("/api/noctyra/workflow/gallery-tags", api_wf_gallery_tags)
    app.router.add_get("/api/noctyra/workflow/gallery-formats", api_wf_gallery_formats)
    app.router.add_get("/api/noctyra/workflow/gallery-folders", api_wf_gallery_folders)
    app.router.add_post("/api/noctyra/workflow/gallery-scan", api_wf_gallery_scan)
    app.router.add_post("/api/noctyra/workflow/gallery-folder/add", api_wf_gallery_folder_add)
    app.router.add_post("/api/noctyra/workflow/gallery-folder/remove", api_wf_gallery_folder_remove)
    app.router.add_get("/api/noctyra/workflow/gallery/{id}", api_wf_gallery_detail)
    app.router.add_post("/api/noctyra/workflow/gallery/delete", api_wf_gallery_delete)
    app.router.add_post("/api/noctyra/workflow/gallery/cleanup-missing", api_wf_cleanup_missing)
    app.router.add_get("/api/noctyra/workflow/image/{id}", api_wf_serve_image)
    app.router.add_post("/api/noctyra/workflow/image/{id}/to-input", api_wf_copy_to_input)
    app.router.add_get("/api/noctyra/model/recipes", api_model_recipes)
    app.router.add_post("/api/noctyra/workflow/check-resources", api_wf_check_resources)
    app.router.add_post("/api/noctyra/workflow/import-local", api_wf_import_local)
    app.router.add_post("/api/noctyra/workflow/update-info", api_wf_update_info)

    # Recipe（配方）操作
    app.router.add_post("/api/noctyra/recipe/fetch-missing", api_recipe_fetch_missing)
    app.router.add_post("/api/noctyra/recipe/batch-import", api_recipe_batch_import)
    app.router.add_get("/api/noctyra/recipe/batch-import/status", api_recipe_batch_import_status)
    app.router.add_get("/api/noctyra/recipe/by-fingerprint", api_recipe_by_fingerprint)
    app.router.add_get("/api/noctyra/recipe/lora-syntax", api_recipe_lora_syntax)

    # 设置 API
    app.router.add_get("/api/noctyra/settings", api_settings_get)
    app.router.add_post("/api/noctyra/settings", api_settings_save)
    app.router.add_get("/api/noctyra/settings/detect-dirs", api_detect_dirs)
    app.router.add_post("/api/noctyra/data-root", api_data_root_set)
    app.router.add_post("/api/noctyra/data-root/clear", api_data_root_clear)

    # 静态文件（带 no-cache 头）
    app.router.add_get("/noctyra_static/{path:.*}", noctyra_static_handler)
