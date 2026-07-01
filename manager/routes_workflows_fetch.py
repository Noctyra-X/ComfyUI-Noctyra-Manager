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
工作流路由：CivitAI 图片 fetch / save / 扩展端一步保存。
"""

from aiohttp import web

from .routes_common import _safe_handler, get_manager, logger
from .routes_workflows_common import (
    _extract_comfy_resources,
    _lookup_resources_from_version_ids,
    _enrich_resources,
    _parse_civitai_image_id,
    _download_and_save_image,
)


@_safe_handler
async def api_wf_fetch_civitai_image(request):
    """获取 CivitAI 图片详情 + 生成参数"""
    data = await request.json()
    url = (data.get("url") or "").strip()
    if not url:
        return web.json_response({"success": False, "error": "缺少 URL"})

    image_id = _parse_civitai_image_id(url)
    if not image_id:
        return web.json_response({"success": False, "error": "无法解析图片 ID，请粘贴 CivitAI 图片页面链接"})

    mgr = get_manager()

    # 检查是否已保存
    existing = mgr.db.get_workflow_image_by_civitai_id(image_id)

    info = await mgr.civitai.get_image_info(image_id)
    if not info:
        return web.json_response({"success": False, "error": "无法获取图片信息，请检查链接或 API Key"})

    raw_meta = info.get("meta") or {}
    # CivitAI 有时嵌套: meta.meta 包含实际生成参数
    if "meta" in raw_meta and isinstance(raw_meta["meta"], dict):
        meta = {**raw_meta["meta"]}
        for k, v in raw_meta.items():
            if k != "meta" and k not in meta:
                meta[k] = v
    else:
        meta = raw_meta

    logger.debug("[Noctyra-WF] Image %s meta keys: %s", image_id, list(meta.keys()))

    resources = (
        meta.get("resources")
        or meta.get("civitaiResources")
        or _extract_comfy_resources(meta.get("comfy"))
    )
    # 若 meta 稀疏（常见于 Z-Image 等新平台），用图片根层 modelVersionIds 查询
    if not resources:
        resources = await _lookup_resources_from_version_ids(mgr, info.get("modelVersionIds") or [])
    # civitaiResources 常只给 {type, modelVersionId}，补全缺失的 name / modelId
    resources = await _enrich_resources(mgr, resources)

    # nsfwLevel 字段在 CivitAI API 里是字符串（"None"/"Soft"/"Mature"/"X"），
    # browsingLevel 才是对应的位图数值（1/2/4/8/16）。我们存数字便于前端阈值比较
    nsfw_level = info.get("browsingLevel")
    if not isinstance(nsfw_level, int):
        nsfw_level = _nsfw_label_to_int(info.get("nsfwLevel"))

    return web.json_response({
        "success": True,
        "image": {
            "id": info.get("id"),
            "url": info.get("url", ""),
            "width": info.get("width"),
            "height": info.get("height"),
            "nsfw_level": nsfw_level,
            "type": info.get("type", "image"),
            "post_id": info.get("postId"),
            "created_at": info.get("createdAt", ""),
            "meta": meta,
            "resources": resources,
            "has_workflow": bool(meta.get("comfy") or meta.get("comfy_workflow") or meta.get("workflow")),
        },
        "already_saved": existing is not None,
        "saved_id": existing["id"] if existing else None,
    })


# CivitAI 标签 → 位图数值（与设置里 nsfw_blur_threshold 的 2/4/8/16 对齐）
_NSFW_LABEL_MAP = {
    "none": 1, "soft": 2, "mature": 4, "x": 8, "xxx": 16,
}

def _nsfw_label_to_int(label) -> int:
    if isinstance(label, int):
        return label
    if isinstance(label, str):
        return _NSFW_LABEL_MAP.get(label.strip().lower(), 0)
    return 0


@_safe_handler
async def api_wf_save_civitai_image(request):
    """下载 CivitAI 图片原图并保存到图库（两步式：前端先 fetch 再 save）"""
    data = await request.json()
    image_url = (data.get("image_url") or "").strip()
    image_info = data.get("image_info") or {}
    if not image_url:
        return web.json_response({"success": False, "error": "缺少 image_url"})

    mgr = get_manager()
    force_update = data.get("force_update", False)
    result = await _download_and_save_image(mgr, image_url, image_info, force_update)
    return web.json_response(result)


@_safe_handler
async def api_extension_save_image(request):
    """浏览器扩展：一次请求串联 fetch + save。

    Body: { image_id: int } 或 { url: string }；返回与 api_wf_save_civitai_image 同结构。
    """
    data = await request.json()
    image_id = data.get("image_id")
    if not image_id:
        url = (data.get("url") or "").strip()
        if url:
            image_id = _parse_civitai_image_id(url)
    try:
        image_id = int(image_id) if image_id else None
    except (TypeError, ValueError):
        image_id = None

    if not image_id:
        return web.json_response({"success": False, "error": "缺少 image_id 或 url"})

    mgr = get_manager()

    # 先看是否已保存（扩展按钮按多次不造成重复下载）
    existing = mgr.db.get_workflow_image_by_civitai_id(image_id)
    if existing:
        return web.json_response({
            "success": True, "already_exists": True,
            "id": existing["id"], "file_path": existing["file_path"],
        })

    # 拉取 CivitAI 图片元数据
    info = await mgr.civitai.get_image_info(image_id)
    if not info:
        return web.json_response({"success": False, "error": "无法获取图片信息（检查 API Key 或代理）"})

    # meta 可能嵌套
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
        resources = await _lookup_resources_from_version_ids(mgr, info.get("modelVersionIds") or [])
    resources = await _enrich_resources(mgr, resources)

    image_url = info.get("url", "")
    if not image_url:
        return web.json_response({"success": False, "error": "CivitAI 未返回图片 URL"})

    image_info = {
        "id": info.get("id"),
        "url": image_url,
        "width": info.get("width"),
        "height": info.get("height"),
        "nsfw_level": info.get("nsfwLevel", 0),
        "meta": meta,
        "resources": resources,
        "has_workflow": bool(meta.get("comfy") or meta.get("comfy_workflow") or meta.get("workflow")),
    }

    result = await _download_and_save_image(mgr, image_url, image_info, force_update=False)
    return web.json_response(result)
