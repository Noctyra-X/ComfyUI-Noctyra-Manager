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
下载/导入/浏览器扩展路由：civitai-versions/hf-files/download/downloads*/import-upload/import-path/extension/*。
"""

import os
import uuid
from aiohttp import web

from .routes_common import _safe_handler, get_manager, logger
from .websocket import get_progress_ws
from .civitai import build_model_url, BASE_URL as _CIVITAI_API_BASE


def _derive_hf_page(download_url: str) -> str:
    """HF 下载直链 → 仓库页 URL；非 HF 返回空（CivitAI 直链无 model_id，无法反推页面）。"""
    if not download_url or "huggingface.co" not in download_url:
        return ""
    idx = download_url.find("/resolve/")
    if idx > 0:
        return download_url[:idx]   # https://huggingface.co/<org>/<repo>
    return download_url if download_url.startswith("https://huggingface.co/") else ""


@_safe_handler
async def api_civitai_versions(request):
    """从 CivitAI URL 获取模型版本列表"""
    data = await request.json()
    url = data.get("url", "")
    if not url:
        return web.json_response({"success": False, "error": "missing url"})

    mgr = get_manager()
    result = await mgr.fetch_civitai_versions(url)
    if result:
        return web.json_response({"success": True, **result})
    return web.json_response({"success": False, "error": "无法获取模型信息，请检查 URL"})


@_safe_handler
async def api_hf_files(request):
    """从 HuggingFace repo URL 获取模型文件列表"""
    data = await request.json()
    url = data.get("url", "")
    if not url:
        return web.json_response({"success": False, "error": "missing url"})

    mgr = get_manager()
    result = await mgr.fetch_hf_files(url)
    if result:
        return web.json_response({"success": True, **result})
    return web.json_response({"success": False, "error": "无法获取 HF repo 信息，请检查 URL"})


@_safe_handler
async def api_download_model(request):
    """下载 CivitAI 模型（异步执行，通过 WebSocket 推送进度）"""
    data = await request.json()
    download_url = data.get("download_url", "")
    save_dir = data.get("save_dir", "")
    file_name = data.get("file_name", "")
    version_id = data.get("version_id")

    if not download_url or not save_dir or not file_name:
        return web.json_response({"success": False, "error": "missing params"})

    mgr = get_manager()
    # 安全：save_dir/file_name 来自客户端，强制 file_name 为纯文件名、save_dir 必须落在 model_roots 内，
    # 防被构造请求把文件写到任意位置（路径穿越 / 绝对 file_name 覆盖 / save_dir 越界）
    file_name = os.path.basename(file_name)
    if not file_name:
        return web.json_response({"success": False, "error": "非法文件名"})
    save_dir = os.path.abspath(save_dir)
    sd_nc = os.path.normcase(save_dir)
    roots = [os.path.normcase(os.path.abspath(r)) for r in mgr.config.model_roots]
    if not any(sd_nc == r or sd_nc.startswith(r + os.sep) for r in roots):
        logger.warning("[Noctyra-MM] 下载拒绝：save_dir %s 不在模型目录内", save_dir)
        return web.json_response({"success": False, "error": "保存目录不在模型目录内"})
    # 先解析文件名冲突（与 downloader.start 内同一逻辑），再用解析后的名去查重 + 启动：
    # 否则查重用原名、start 里才改名，两个并发同名请求会各自解析到同一新名、写坏同一个 .tmp。
    # 解析对 .tmp 不计数 + 这里无 await 保持原子，故 req2 会解析到同名并被 find_active_download 命中去重。
    from .downloader import _resolve_filename_conflict
    file_name = _resolve_filename_conflict(save_dir, file_name, download_url=download_url, version_id=version_id)
    # 同一目标已有进行中下载（连点/多入口）→ 返回它，不重复下载
    dup_id = mgr.find_active_download(save_dir, file_name)
    if dup_id:
        return web.json_response({"success": True, "download_id": dup_id, "already_downloading": True})

    ws = get_progress_ws()
    download_id = uuid.uuid4().hex[:12]
    progress_cb = ws.make_download_progress_callback(download_id)

    async def on_download_complete(dl):
        await ws.broadcast("download_progress", {
            "download_id": download_id,
            "status": dl["status"],
            "error": dl.get("error", ""),
            "file_name": file_name,
            "progress": dl.get("progress", 0),
        })

    mgr.start_download(download_id, download_url, save_dir, file_name,
                        progress_cb, on_complete=on_download_complete,
                        version_id=version_id,
                        source_url=_derive_hf_page(download_url),
                        expected_sha256=(data.get("expected_sha256") or ""))
    logger.info("[Noctyra-MM] 下载任务已启动: %s -> %s", download_id, file_name)
    return web.json_response({"success": True, "download_id": download_id})


@_safe_handler
async def api_downloads_list(request):
    """获取所有下载任务状态"""
    mgr = get_manager()
    downloads = mgr.get_downloads()
    return web.json_response({"success": True, "downloads": downloads})


@_safe_handler
async def api_download_cancel(request):
    """取消下载任务"""
    data = await request.json()
    download_id = data.get("download_id", "")
    if not download_id:
        return web.json_response({"success": False, "error": "missing download_id"})
    mgr = get_manager()
    cancelled = mgr.cancel_download(download_id)
    return web.json_response({"success": cancelled})


@_safe_handler
async def api_download_remove(request):
    """移除已完成/失败/取消的下载记录"""
    data = await request.json()
    download_id = data.get("download_id", "")
    if not download_id:
        return web.json_response({"success": False, "error": "missing download_id"})
    mgr = get_manager()
    removed = mgr.remove_download(download_id)
    return web.json_response({"success": removed})


@_safe_handler
async def api_download_clear(request):
    """清空终态下载记录。
    可选 ?status=failed|complete|all（默认 all，向后兼容）。
    - failed: 仅清 error/cancelled/interrupted
    - complete: 仅清 complete
    - all/缺省: 全部终态
    """
    status_param = request.query.get("status", "all").lower()
    if status_param == "failed":
        statuses = ("error", "cancelled", "interrupted")
    elif status_param == "complete":
        statuses = ("complete",)
    else:
        statuses = None  # 全部终态
    mgr = get_manager()
    removed = mgr.cleanup_downloads(statuses=statuses)
    return web.json_response({"success": True, "removed": removed})


@_safe_handler
async def api_download_retry(request):
    """重试已经失败/取消/中断的下载任务（复用同 download_id）"""
    data = await request.json()
    download_id = data.get("download_id", "")
    if not download_id:
        return web.json_response({"success": False, "error": "missing download_id"})
    mgr = get_manager()
    # 重新生成 WebSocket 进度回调（原 callback 是临时对象，不持久化）
    ws = get_progress_ws()
    progress_cb = ws.make_download_progress_callback(download_id)
    ok = mgr.retry_download(download_id, progress_callback=progress_cb)
    if not ok:
        return web.json_response({"success": False, "error": "not a terminal task or not found"})
    return web.json_response({"success": True})


@_safe_handler
async def api_download_pause(request):
    """暂停下载（保留 .tmp，可断点续传）"""
    data = await request.json()
    download_id = data.get("download_id", "")
    if not download_id:
        return web.json_response({"success": False, "error": "missing download_id"})
    mgr = get_manager()
    ok = mgr.pause_download(download_id)
    return web.json_response({"success": ok})


@_safe_handler
async def api_download_resume(request):
    """恢复已暂停的下载（断点续传）"""
    data = await request.json()
    download_id = data.get("download_id", "")
    if not download_id:
        return web.json_response({"success": False, "error": "missing download_id"})
    mgr = get_manager()
    # 重新生成 WebSocket 进度回调（原 callback 不持久化）
    ws = get_progress_ws()
    progress_cb = ws.make_download_progress_callback(download_id)
    ok = mgr.resume_download(download_id, progress_callback=progress_cb)
    if not ok:
        return web.json_response({"success": False, "error": "not paused or busy"})
    return web.json_response({"success": True})


@_safe_handler
async def api_download_redownload(request):
    """重新下载（从头，覆盖旧成品）"""
    data = await request.json()
    download_id = data.get("download_id", "")
    if not download_id:
        return web.json_response({"success": False, "error": "missing download_id"})
    mgr = get_manager()
    ws = get_progress_ws()
    progress_cb = ws.make_download_progress_callback(download_id)
    ok = mgr.redownload(download_id, progress_callback=progress_cb)
    if not ok:
        return web.json_response({"success": False, "error": "busy or not found"})
    return web.json_response({"success": True})


@_safe_handler
async def api_import_upload(request):
    """拖拽/文件选择 → multipart 流式写盘到 Unknown/。
    约定字段：file（必填，单个模型文件）；可选 filename（覆盖原文件名）。
    """
    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    # ComfyUI 默认 client_max_size=100MB，大模型会被 413 掉；流式读取 → 解除上限
    request._client_max_size = 0
    reader = await request.multipart()
    filename = ""
    file_part = None

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "filename":
            filename = (await part.text()).strip()
        elif part.name == "file":
            # 先拿到 filename 再流式读取；filename 字段应放在 file 之前
            if not filename:
                filename = part.filename or ""
            file_part = part
            break  # file 必须最后读（流式），读完就退出

    if file_part is None or not filename:
        return web.json_response({"success": False, "error": "缺少 file 字段"})

    # 复核：读 multipart 头部期间可能有扫描启动（前面的 is_busy 检查到这里隔着 await）；
    # 此处到 with 之间无 await，是原子的
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = await mgr.import_from_multipart(filename, file_part)
    return web.json_response(result)


@_safe_handler
async def api_import_path(request):
    """本地路径导入：shutil.move 或 copy2。
    Body: { "path": "D:\\Downloads\\x.safetensors", "move": false }
    """
    data = await request.json()
    src_path = (data.get("path") or "").strip()
    move = bool(data.get("move", False))
    if not src_path:
        return web.json_response({"success": False, "error": "missing path"})

    mgr = get_manager()
    if mgr.is_busy:
        return web.json_response({"success": False, "error": "busy"})
    with mgr.exclusive_op():
        result = await mgr.import_from_path(src_path, move=move)
    return web.json_response(result)


@_safe_handler
async def api_extension_check(request):
    """浏览器扩展：批量查询哪些 CivitAI version_ids 已在本地库

    Body: { "version_ids": [int, ...] }
    Returns: { "success": True, "downloaded": { "<version_id>": {file_path, file_name, model_name, sha256} } }
    """
    data = await request.json()
    version_ids = data.get("version_ids") or []
    model_ids = data.get("model_ids") or []
    if not isinstance(version_ids, list) or not isinstance(model_ids, list):
        return web.json_response({"success": False, "error": "version_ids/model_ids must be lists"})

    mgr = get_manager()
    downloaded = {}
    record_only = {}  # 软删（文件已删但留了记录）→ 扩展显示"已有记录"，点击仍可重下
    for vid in version_ids:
        try:
            vid_int = int(vid)
        except (TypeError, ValueError):
            continue
        rows = mgr.db.query_by_version_id(vid_int, include_deleted=True)
        if rows:
            row = rows[0]
            info = {
                "file_path": row.get("file_path"),
                "file_name": row.get("file_name"),
                "model_name": row.get("model_name"),
                "sha256": row.get("sha256"),
            }
            (record_only if row.get("file_deleted") else downloaded)[str(vid_int)] = info

    by_model = {}
    model_total = {}   # model_id -> CivitAI 上该模型总版本数（判"本地是否下全"）
    for mid in model_ids:
        try:
            mid_int = int(mid)
        except (TypeError, ValueError):
            continue
        rows = mgr.db.query_by_model_id(mid_int)  # 只算真实下载的版本，软删不计入"本地已有 N 个版本"
        if rows:
            by_model[str(mid_int)] = [
                {
                    "file_path": r.get("file_path"),
                    "file_name": r.get("file_name"),
                    "model_name": r.get("model_name"),
                    "version_name": r.get("version_name"),
                    "civitai_version_id": r.get("civitai_version_id"),
                }
                for r in rows
            ]
            total = mgr.db.get_model_version_total(mid_int)
            if total:
                model_total[str(mid_int)] = total
    return web.json_response({
        "success": True,
        "downloaded": downloaded,
        "record_only": record_only,
        "by_model": by_model,
        "model_total": model_total,
    })


async def _start_civitai_download_by_ref(mgr, model_id, version_id) -> dict:
    """共享逻辑：给定 CivitAI model_id / version_id，启动下载任务并返回结果 dict。

    供 `api_extension_download`（单个下载）和 `api_recipe_fetch_missing`（批量）复用。
    返回 {success, download_id?, file_name?, save_dir?, already_exists?, error?}
    """
    if not model_id and not version_id:
        return {"success": False, "error": "需要 model_id 或 version_id"}

    # 这个 URL 最终会被 fetch_civitai_versions 正则解析出 IDs，host 不参与实际请求，
    # 所以走 build_model_url（尊重用户的 source_host 设置，和其他 user-facing 链接保持一致）
    url = build_model_url(model_id, version_id) if model_id else ""
    # 只有 version_id 时直接打 API 端点（API host 固定 civitai.com，两站共用同一后端）
    if not url and version_id:
        url = f"{_CIVITAI_API_BASE}/model-versions/{version_id}"

    info = await mgr.fetch_civitai_versions(url) if url else None

    # Fallback: 直接按 version_id 取版本
    if (not info or not info.get("versions")) and version_id:
        v = await mgr.civitai.get_model_version(int(version_id))
        if v:
            files = v.get("files", []) or []
            primary = next((f for f in files if (f.get("type") or "").lower() == "model"), None)
            if primary is None and files:
                primary = files[0]
            if primary:
                images = v.get("images", []) or []
                preview = images[0].get("url", "") if images else ""
                info = {
                    "model_id": (v.get("model") or {}).get("id"),
                    "model_name": (v.get("model") or {}).get("name", ""),
                    "model_type": (v.get("model") or {}).get("type", ""),
                    "versions": [{
                        "version_id": v.get("id"),
                        "version_name": v.get("name", ""),
                        "base_model": v.get("baseModel", ""),
                        "download_url": primary.get("downloadUrl", ""),
                        "file_name": primary.get("name", ""),
                        "file_size": primary.get("sizeKB", 0) * 1024,
                        "preview_url": preview,
                        "sha256": (primary.get("hashes") or {}).get("SHA256", ""),
                    }],
                }

    if not info or not info.get("versions"):
        return {"success": False, "error": "无法获取 CivitAI 版本信息（可能是网络/代理/API key 问题，详见 ComfyUI 控制台日志）"}

    versions = info["versions"]
    target = None
    if version_id:
        target = next((v for v in versions if int(v.get("version_id", 0)) == int(version_id)), None)
    if not target:
        target = versions[0]

    download_url = target.get("download_url")
    file_name = target.get("file_name") or f"{target.get('version_id')}.safetensors"
    if not download_url:
        return {"success": False, "error": "版本无下载链接"}

    roots = list(mgr.config.model_roots)
    if not roots:
        return {"success": False, "error": "未配置 model_roots"}

    # base_model 用 CivitAI 原值（视频 UNet 覆盖 + 落点子目录都用它），与显示一致、不按文件名细化
    from .base_models import normalize_base_model
    base_model = normalize_base_model((target.get("base_model") or "").strip() or "Unknown")

    civitai_type = info.get("model_type", "")
    type_key = mgr._TEMPLATE_TYPE_ALIAS.get(civitai_type.strip().lower(), civitai_type.strip().lower())
    # 视频模型 / 纯 UNet 的 Checkpoint 强制改走 unet（配合 diffusion_model_base_models 列表）
    type_key = mgr._apply_diffusion_override(type_key, base_model)

    # 目标根决定链：
    # 1. 用户在设置里为该类型配置了"默认下载目录"（default_roots[type_key]），且路径仍有效 → 用它
    # 2. 按类型关键字在 model_roots 中找匹配根
    # 3. 兜底用第一个 model_root
    default_roots_cfg = mgr.config.get("default_roots") or {}
    configured = (default_roots_cfg.get(type_key) or "").strip()
    if configured and os.path.isdir(configured):
        root_dir = configured
    else:
        root_dir = mgr._find_correct_root(roots, "", type_key) or roots[0]

    templates = mgr.config.get("organize_path_templates") or {}
    template = templates.get(type_key, "")
    # 缺 base_model 时避免塞进 Unknown/ 子目录
    if "{base_model}" in template and (not base_model or base_model == "Unknown"):
        template = ""

    fake_model = {
        "file_path": file_name,
        "model_name": info.get("model_name", ""),
        "version_name": target.get("version_name", ""),
        "creator": info.get("creator", "") or "",
        "source": "civitai",
        "civitai_tags": info.get("tags", []) or [],
        "hf_tags": [],
        "trained_words": target.get("trained_words", []) or [],
    }
    sub_dir = mgr._render_path_template(template, fake_model, base_model)
    save_dir = os.path.join(root_dir, sub_dir.replace("/", os.sep)) if sub_dir else root_dir

    save_path = os.path.join(save_dir, file_name)
    existing_path = save_path if os.path.exists(save_path) else (
        os.path.join(root_dir, file_name) if os.path.exists(os.path.join(root_dir, file_name)) else None
    )
    if existing_path:
        return {
            "success": False,
            "already_exists": True,
            "error": f"目标文件已存在: {existing_path}（可能未扫描入库，请在 Noctyra 管理器点扫描）",
            "save_dir": os.path.dirname(existing_path),
            "file_name": file_name,
        }

    # 同一目标已有进行中下载（图库连点 / 多入口重复触发）→ 返回它，不重复下载
    dup_id = mgr.find_active_download(save_dir, file_name)
    if dup_id:
        return {
            "success": True,
            "download_id": dup_id,
            "file_name": file_name,
            "save_dir": save_dir,
            "already_downloading": True,
        }

    ws = get_progress_ws()
    download_id = uuid.uuid4().hex[:12]
    progress_cb = ws.make_download_progress_callback(download_id)

    async def on_download_complete(dl):
        await ws.broadcast("download_progress", {
            "download_id": download_id,
            "status": dl["status"],
            "error": dl.get("error", ""),
            "file_name": file_name,
            "progress": dl.get("progress", 0),
        })

    mgr.start_download(download_id, download_url, save_dir, file_name,
                       progress_cb, on_complete=on_download_complete,
                       version_id=target.get("version_id"),
                       preview_url=target.get("preview_url", ""),
                       source_url=build_model_url(model_id, target.get("version_id")),
                       expected_sha256=target.get("sha256", ""))
    logger.info("[Noctyra-MM] CivitAI 下载任务: %s -> %s (dir=%s)", download_id, file_name, save_dir)
    return {
        "success": True,
        "download_id": download_id,
        "file_name": file_name,
        "save_dir": save_dir,
    }


@_safe_handler
async def api_extension_download(request):
    """浏览器扩展：一键下载 CivitAI 模型（服务端自动选版本+目录）

    Body: { "model_id": int, "version_id": int (可选，缺省取最新版本) }
    """
    data = await request.json()
    mgr = get_manager()
    result = await _start_civitai_download_by_ref(
        mgr, data.get("model_id"), data.get("version_id")
    )
    return web.json_response(result)


@_safe_handler
async def api_extension_ping(request):
    """浏览器扩展：健康检查（轻量，支持 CORS）"""
    mgr = get_manager()
    stats = mgr.get_stats()
    return web.json_response({
        "success": True,
        "version": "noctyra-1.0",
        "total": stats.get("total", 0),
        "matched": stats.get("matched", 0),
    })
