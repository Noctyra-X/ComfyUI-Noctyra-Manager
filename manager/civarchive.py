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
CivArchive 兜底客户端

CivArchive (https://civarchive.com) 是 CivitAI 的社区镜像/归档。当 CivitAI 自己
返回 404（模型被删 / 作者私有化），我们按 SHA256 在 CivArchive 查一次，把返回
的数据转成 CivitAI-compatible info dict，直接喂给 update_online_info。

API：
  GET https://civarchive.com/api/sha256/<sha256_lower>
  → { data: { id, name, type, creator_*, version: {...}, files: [...], ... } }
"""

import asyncio
import logging
import os
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger("noctyra.civarchive")

BASE_URL = "https://civarchive.com/api"
SOURCE_NAME = "civarchive"

# 请求超时：CivArchive 是社区服务，别设太死
_TIMEOUT_TOTAL = 20
_TIMEOUT_CONNECT = 10


# 代理解析统一到 proxy_util，避免与 huggingface / preview_cache 各自一份漂移
from .proxy_util import get_proxy as _get_proxy, make_connector


async def get_model_by_hash(sha256: str) -> Optional[Dict]:
    """按 SHA256 在 CivArchive 查模型。找到返回 CivitAI-like info dict，找不到返回 None。"""
    if not sha256:
        return None

    url = f"{BASE_URL}/sha256/{sha256.lower()}"
    proxy = _get_proxy()
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_TOTAL, connect=_TIMEOUT_CONNECT)

    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=make_connector()) as session:
            async with session.get(url, proxy=proxy) as resp:
                if resp.status == 404:
                    logger.debug("[Noctyra-MM] CivArchive 未收录 %s", sha256[:10])
                    return None
                if resp.status != 200:
                    logger.info("[Noctyra-MM] CivArchive HTTP %d: %s", resp.status, sha256[:10])
                    return None
                payload = await resp.json()
    except asyncio.TimeoutError:
        logger.info("[Noctyra-MM] CivArchive 超时: %s", sha256[:10])
        return None
    except Exception as e:
        logger.info("[Noctyra-MM] CivArchive 查询异常: %s - %s", sha256[:10], e)
        return None

    return _transform_to_civitai_info(payload)


def _transform_to_civitai_info(payload: Dict) -> Optional[Dict]:
    """把 CivArchive 响应转成 Noctyra update_online_info 能直接吃的 info dict
    （字段集与 CivitaiClient.parse_version_info + enrich_with_model_info 对齐）
    """
    if not isinstance(payload, dict):
        return None

    # CivArchive 把正文可能包在 data 里
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    # 模型主体 + version 主体
    model_ctx = data.get("model") if isinstance(data.get("model"), dict) else data
    version = data.get("version") if isinstance(data.get("version"), dict) else {}
    if not version and isinstance(model_ctx.get("version"), dict):
        version = model_ctx["version"]

    model_id = model_ctx.get("id") or data.get("modelId")
    version_id = version.get("id")
    model_name = model_ctx.get("name") or data.get("name") or ""
    version_name = version.get("name") or data.get("versionName") or ""
    base_model = version.get("baseModel") or data.get("baseModel") or "Unknown"
    model_type = model_ctx.get("type") or data.get("type") or ""
    description = model_ctx.get("description") or data.get("description") or ""
    is_nsfw = bool(model_ctx.get("is_nsfw") or model_ctx.get("nsfw") or data.get("is_nsfw"))

    # 预览图：CivArchive 的 images 与 CivitAI 同构（url / nsfwLevel / type）
    images = (version.get("images") or data.get("images") or model_ctx.get("images") or []) or []
    preview_url = ""
    preview_images = []
    for img in images:
        if not isinstance(img, dict):
            continue
        url = img.get("url", "")
        if not url:
            continue
        preview_images.append({
            "url": url,
            # CivArchive 的 image 同样带 type（image/video）。之前漏读 → 视频被当图片用
            # <img> 渲染成"no image"占位（LTX/Wan 等视频模型整组占位即此因）
            "type": img.get("type") or "image",
            # 字段名必须是 nsfw_level（下划线），与 civitai.py + database 读取口径一致。
            # 之前误存成驼峰 nsfwLevel → database 读 nsfw_level 永远是 0 → NSFW 模型不打码
            "nsfw_level": img.get("nsfwLevel", img.get("nsfw_level", 0)),
            "width": img.get("width"),
            "height": img.get("height"),
        })
    # 主预览优先选静态图（与 civitai.py 一致，卡片缩略不必加载视频）；全是视频时退回第一个
    if preview_images:
        first_img = next((p for p in preview_images if p.get("type") != "video"), None)
        preview_url = (first_img or preview_images[0])["url"]

    # 下载 URL：CivArchive 可能有镜像 mirrors，选第一个非删除的
    files = version.get("files") or data.get("files") or model_ctx.get("files") or []
    download_url = ""
    for f in files:
        if not isinstance(f, dict):
            continue
        url = f.get("downloadUrl") or f.get("url")
        if url:
            download_url = url
            break
        mirrors = f.get("mirrors") or []
        for m in mirrors:
            if isinstance(m, dict) and not m.get("deletedAt"):
                url = m.get("url") or m.get("downloadUrl")
                if url:
                    download_url = url
                    break
        if download_url:
            break

    trained_words = version.get("trainedWords") or data.get("trainedWords") or []
    if not isinstance(trained_words, list):
        trained_words = []

    tags = model_ctx.get("tags") or data.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    creator = (model_ctx.get("creator") or {}) if isinstance(model_ctx.get("creator"), dict) else {}
    creator_name = (creator.get("username")
                    or model_ctx.get("creator_username")
                    or data.get("creator_username") or "")
    creator_avatar = (creator.get("image")
                      or model_ctx.get("creator_image")
                      or model_ctx.get("creator_avatar") or "")

    if not model_id and not version_id:
        # 连最基本的 ID 都没有的话视为无效响应
        logger.debug("[Noctyra-MM] CivArchive 响应缺 model_id / version_id，丢弃")
        return None

    # 标记 source=civarchive；后续前端可据此显示"归档来源"徽章区分
    info = {
        "source": SOURCE_NAME,
        "source_url": f"https://civarchive.com/models/{model_id}" if model_id else "",
        "model_name": model_name,
        "version_name": version_name,
        "model_description": description,
        "preview_url": preview_url,
        "preview_images": preview_images,
        "download_url": download_url,
        "civitai_model_id": model_id,
        "civitai_version_id": version_id,
        "civitai_model_type": model_type,
        "base_model": base_model,
        "trained_words": trained_words,
        "tags": tags,
        "creator": creator_name,
        "creator_avatar": creator_avatar,
        "nsfw": is_nsfw,
        "published_at": version.get("publishedAt") or data.get("publishedAt") or "",
        "downloads": 0,
        "rating": 0,
        "rating_count": 0,
        "thumbs_up": 0,
        "comment_count": 0,
        "_raw_data": {"civarchive": payload},
    }
    logger.info("[Noctyra-MM] CivArchive 命中: %s (by %s)", model_name, creator_name)
    return info
