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
工作流路由共享 helpers —— 从 routes_workflows.py 拆出。

分组：
  - ComfyUI 节点图解析：`_extract_comfy_resources`
  - CivitAI 资源补全：`_fetch_version_meta` / `_lookup_resources_from_version_ids` / `_enrich_resources`
  - URL 解析：`_parse_civitai_image_id`
  - 本地命中判定：`_resource_is_local`
  - 图片下载 + 入库：`_download_and_save_image`（fetch/save/extension-save-image/batch-import 都用）
"""

import asyncio
import os
import re
import aiohttp

from .routes_common import logger
from .image_meta import extract_image_meta
from .civitai import build_image_url
from .preview_cache import _is_safe_external_url


def _fix_mojibake(s: str) -> str:
    """修复 CivitAI 服务端把 UTF-8 字节当 Latin-1 解码再回存 UTF-8 造成的双重编码乱码。

    触发条件（避免误伤正常 Latin-1 字符）：
      1. 字符串包含典型 mojibake 前导字节（ä / å / æ / Ã / Â / é / è）
      2. 重解码后出现 CJK / 假名 / 韩文 等真多字节字符 → 认定原文是这些
    否则原样返回。
    """
    if not s or not isinstance(s, str):
        return s
    # 快筛：无 Latin-1 可疑前导字节直接跳过
    if not any(c in s for c in ("ä", "å", "æ", "Ã", "Â", "é", "è", "ê", "ë")):
        return s
    try:
        candidate = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    # 校验：修复后至少出现一个 CJK / 假名 / 韩文字符才认为确实是乱码
    for ch in candidate:
        if ("一" <= ch <= "鿿"   # CJK
                or "぀" <= ch <= "ゟ"   # 平假名
                or "゠" <= ch <= "ヿ"   # 片假名
                or "가" <= ch <= "힯"):  # 韩文
            return candidate
    return s


def _extract_comfy_resources(comfy):
    """从 ComfyUI 工作流节点图中提取使用的模型资源"""
    if not comfy or not isinstance(comfy, dict):
        return []

    nodes = comfy if not comfy.get("prompt") else comfy.get("prompt", comfy)
    if not isinstance(nodes, dict):
        return []

    resources = []
    seen = set()

    def _add(rtype, name, weight=None):
        if not name or name in seen or name == "None":
            return
        seen.add(name)
        r = {"type": rtype, "name": name}
        if weight is not None:
            r["weight"] = weight
        resources.append(r)

    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        cls = (node.get("class_type") or "").lower()
        inp = node.get("inputs") or {}

        # Checkpoint
        if "checkpointloader" in cls or "checkpoint_loader" in cls or "efficient loader" in cls:
            _add("Checkpoint", inp.get("ckpt_name", ""))

        # LoRA — 单个加载器
        if "loraloadermodel" in cls or cls == "loraloader" or "lora_loader" in cls or "power lora loader" in cls:
            weight = inp.get("strength_model") or inp.get("strength") or 1
            _add("LORA", inp.get("lora_name") or inp.get("lora") or "", weight)

        # LoRA Stacker — 多个编号输入 lora_name_1, lora_name_2, ...
        if "lorastacker" in cls or "lora stacker" in cls or "lora_stacker" in cls:
            for key, val in inp.items():
                if key.startswith("lora_name") and isinstance(val, str) and val and val != "None":
                    idx = key.replace("lora_name_", "").replace("lora_name", "")
                    w_key = f"lora_wt_{idx}" if idx else "lora_wt"
                    weight = inp.get(w_key, 1)
                    _add("LORA", val, weight)

        # VAE
        if "vaeloader" in cls:
            _add("VAE", inp.get("vae_name", ""))
        if "efficient loader" in cls and inp.get("vae_name") and inp["vae_name"] != "Baked VAE":
            _add("VAE", inp["vae_name"])

        # Upscaler
        if "upscale" in cls and ("model" in cls or "loader" in cls):
            _add("Upscaler", inp.get("model_name", ""))

        # ControlNet
        if "controlnet" in cls and "loader" in cls:
            _add("ControlNet", inp.get("control_net_name", ""))

        # Embedding
        if "embedding" in cls:
            _add("TextualInversion", inp.get("embedding_name", ""))

    return resources


async def _fetch_version_meta(mgr, vid):
    """调用 CivitAI model-version API，返回 {type, name, modelId, modelVersionId, versionName}"""
    try:
        data = await mgr.civitai.get_model_version(int(vid))
    except Exception:
        return None
    if not data:
        return None
    model = data.get("model") or {}
    return {
        "type": model.get("type") or "Model",
        "name": model.get("name") or data.get("name") or "",
        "modelId": data.get("modelId"),
        "modelVersionId": data.get("id"),
        "versionName": data.get("name"),
    }


async def _lookup_resources_from_version_ids(mgr, version_ids):
    """meta 完全没资源时，用图片根层 modelVersionIds 建立资源列表。"""
    if not version_ids:
        return []
    results = await asyncio.gather(*[_fetch_version_meta(mgr, v) for v in version_ids])
    return [r for r in results if r and r.get("name")]


async def _enrich_resources(mgr, resources):
    """对缺失 name 但有 modelVersionId 的资源，用 CivitAI API 补全 name/modelId。
    civitaiResources 常只返回 {type, modelVersionId}，需要这一步。
    顺便把已有 name 里的 mojibake 乱码修掉。"""
    if not resources:
        return resources
    tasks = {}
    for i, r in enumerate(resources):
        # 先把已有 name 的乱码修掉（不受后续 API 补全影响）
        if r.get("name"):
            r["name"] = _fix_mojibake(r["name"])
            continue
        vid = r.get("modelVersionId") or r.get("modelVersionID") or r.get("version_id")
        if vid:
            tasks[i] = _fetch_version_meta(mgr, vid)
    if not tasks:
        return resources
    keys = list(tasks.keys())
    fetched = await asyncio.gather(*[tasks[k] for k in keys])
    for k, info in zip(keys, fetched):
        if not info:
            continue
        r = resources[k]
        if not r.get("name"):
            r["name"] = _fix_mojibake(info.get("name") or "")
        if not r.get("modelId"):
            r["modelId"] = info.get("modelId")
        if not r.get("type") or r.get("type", "").lower() == "model":
            r["type"] = info.get("type") or r.get("type") or "Model"
        if not r.get("versionName"):
            r["versionName"] = _fix_mojibake(info.get("versionName") or "")
    return resources


def _parse_civitai_image_id(url: str):
    """从 CivitAI URL 中提取 image ID"""
    m = re.search(r'civitai\.\w+/images/(\d+)', url)
    if m:
        return int(m.group(1))
    try:
        return int(url.strip())
    except (ValueError, TypeError):
        return None


def _build_local_index(mgr, images) -> dict:
    """从一批图库项的 resources 里收集所有 version_id / model_id，两次批量查库得到
    本地存在的子集，给 _compute_resource_status 批量判断用，避免逐资源 N+1。"""
    vids, mids = set(), set()
    for img in (images or []):
        for r in (img.get("resources") or []):
            if not isinstance(r, dict):
                continue
            v = r.get("modelVersionId") or r.get("versionId")
            if v is not None:
                vids.add(v)
            m = r.get("modelId")
            if m is not None:
                mids.add(m)
    return {
        "version_ids": mgr.db.filter_existing_version_ids(vids) if vids else set(),
        "model_ids": mgr.db.filter_existing_model_ids(mids) if mids else set(),
    }


def _compute_resource_status(mgr, resources, local_index=None) -> dict:
    """为一张图的 resources 列表计算"本地命中情况"。

    返回 {"total": N, "missing": M, "complete": bool}。
    复用 _resource_is_local，按 versionId → modelId → name 的顺序匹配。
    local_index（_build_local_index 产物）存在时走 O(1) 集合判断，免 N+1。
    """
    resources = resources or []
    total = len(resources)
    if total == 0:
        return {"total": 0, "missing": 0, "complete": True}
    missing = 0
    for r in resources:
        if not isinstance(r, dict):
            continue
        if not _resource_is_local(mgr, r, local_index=local_index):
            missing += 1
    return {"total": total, "missing": missing, "complete": missing == 0}


def _resource_is_local(mgr, res: dict, local_index=None):
    """判断资源在本地库是否命中。
    local_index 为 None 时返回命中记录（dict）或 None（详情/check_resources 用，逐查）；
    提供 local_index 时只判断真假（返回 True/None），用 O(1) 集合命中代替逐资源查库，
    顺序仍为 versionId → modelId → name，与 api_wf_check_resources 一致。"""
    version_id = res.get("modelVersionId") or res.get("versionId")
    model_id = res.get("modelId")
    name = res.get("name", "")

    if local_index is not None:
        try:
            if version_id is not None and int(version_id) in local_index["version_ids"]:
                return True
        except (TypeError, ValueError):
            pass
        try:
            if model_id is not None and int(model_id) in local_index["model_ids"]:
                return True
        except (TypeError, ValueError):
            pass
        # 前面 id 都没命中 → 回退名字模糊搜索（与原逻辑同：version/model 查空后才走 name）
        if name:
            base_name = name.split(".")[0].strip()
            if base_name:
                rows, _ = mgr.db.get_all(
                    filters={"search": base_name}, sort_by="file_name", page=1, page_size=1
                )
                if rows:
                    return True
        return None

    if version_id:
        try:
            rows = mgr.db.query_by_version_id(int(version_id))
        except (TypeError, ValueError):
            rows = []
        if rows:
            return rows[0]

    if model_id:
        try:
            rows = mgr.db.query_by_model_id(int(model_id))
        except (TypeError, ValueError):
            rows = []
        if rows:
            return rows[0]

    if name:
        base_name = name.split(".")[0].strip()
        if base_name:
            all_models, _ = mgr.db.get_all(
                filters={"search": base_name},
                sort_by="file_name", page=1, page_size=5
            )
            if all_models:
                return all_models[0]

    return None


async def _download_media_to(mgr, image_url: str, save_path: str, is_video: bool,
                             attempts: int = 3) -> tuple:
    """下载媒体到 save_path，带完整性校验 + 重试。返回 (ok, error)。

    之前只把字节写盘、不校验 Content-Length，连接中途被截断时会落一个残缺文件并入库
    （表现为图库/预览裂图，要点"重新获取"才补全）。这里下完比对长度，残缺即删并重试。
    """
    timeout = aiohttp.ClientTimeout(total=300 if is_video else 120, connect=15)
    last_err = "下载失败"

    def _rm():
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                pass

    for attempt in range(1, attempts + 1):
        written = 0
        expected = 0
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(image_url, proxy=mgr.civitai._proxy()) as resp:
                    if resp.status != 200:
                        last_err = f"下载失败 HTTP {resp.status}"
                        _rm()
                        if resp.status in (401, 403, 404, 410):
                            break  # 永久错误，不重试
                        await asyncio.sleep(1)
                        continue
                    expected = int(resp.headers.get("Content-Length", 0) or 0)
                    with open(save_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            f.write(chunk)
                            written += len(chunk)
        except Exception as e:
            last_err = str(e)
            logger.warning("[Noctyra-WF] 媒体下载第 %d 次异常: %s", attempt, e)
            _rm()
            await asyncio.sleep(1)
            continue
        # 完整性校验：空文件 / 不足 Content-Length → 残缺，删掉重试
        if written == 0 or (expected and written < expected):
            last_err = f"下载不完整（{written}/{expected or '?'} 字节）"
            logger.warning("[Noctyra-WF] 媒体下载第 %d 次不完整: %s", attempt, last_err)
            _rm()
            await asyncio.sleep(1)
            continue
        return True, ""

    _rm()
    return False, last_err


async def _download_and_save_image(mgr, image_url: str, image_info: dict,
                                    force_update: bool = False) -> dict:
    """下载 CivitAI 原图 + 解析内嵌元数据 + 写入图库。

    供 `api_wf_save_civitai_image`（两步式：前端先 fetch 再 save）/
    `api_extension_save_image`（扩展一步式）/ `api_recipe_batch_import`（批量）共用。

    返回 dict 匹配 aiohttp 路由的 json_response 结构：
      { success, id?, file_path?, already_exists?, error? }
    """
    civitai_id = image_info.get("id")
    if civitai_id and not force_update:
        existing = mgr.db.get_workflow_image_by_civitai_id(civitai_id)
        if existing:
            return {
                "success": True, "already_exists": True,
                "id": existing["id"], "file_path": existing["file_path"],
            }

    save_dir = mgr.config.workflow_gallery_dir
    os.makedirs(save_dir, exist_ok=True)

    # CivitAI 的 image_info.type：'image' / 'video'
    is_video = (image_info.get("type") or "").lower() == "video"

    # 从 URL 中提取文件名
    url_path = image_url.split("?")[0]
    segments = [s for s in url_path.split("/") if s]
    raw_name = segments[-1] if segments else ("video.mp4" if is_video else "image.png")
    if raw_name.startswith("width="):
        raw_name = segments[-2] if len(segments) >= 2 else ("video.mp4" if is_video else "image.png")
    _, ext = os.path.splitext(raw_name)
    if not ext:
        raw_name += ".mp4" if is_video else ".jpeg"
    # 扩展名兜底：来源没标 type、但文件其实是视频（.mp4/.webm 等）时纠正为视频。否则会存成
    # media_type=image，详情页用 <img> 加载视频 → 裂图、不自动播放（只信来源 type 会漏判）
    if os.path.splitext(raw_name)[1].lower() in (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"):
        is_video = True

    save_path = os.path.join(save_dir, raw_name)

    if not os.path.exists(save_path):
        # SSRF 防御：即便 image_url 通常来自 CivitAI API 响应，也复查一遍防越界
        if not await _is_safe_external_url(image_url):
            logger.warning("[Noctyra-WF] 拒绝不安全的图片 URL (SSRF 防御): %s", image_url[:100])
            return {"success": False, "error": "unsafe image url"}
        ok, err = await _download_media_to(mgr, image_url, save_path, is_video)
        if not ok:
            return {"success": False, "error": err}

    # 始终尝试解析文件内嵌：extract_image_meta 内部按扩展名自守 —— mp4/webm 直接返回空，
    # 只有 webp/png/jpeg 才真解析。这样 ComfyUI 动图 webp 里内嵌的 workflow 也能读出来
    # （它把 workflow/prompt 写在 EXIF），从而支持"发送到画布"。
    loop = asyncio.get_running_loop()
    embed = await loop.run_in_executor(None, extract_image_meta, save_path)

    has_workflow = bool(embed.get("workflow")) or image_info.get("has_workflow", False)
    meta = image_info.get("meta") or {}

    record_id = mgr.db.save_workflow_image({
        "file_path": save_path,
        "file_name": raw_name,
        "source": "civitai",
        "source_url": build_image_url(civitai_id) if civitai_id else "",
        "civitai_image_id": civitai_id,
        "width": image_info.get("width"),
        "height": image_info.get("height"),
        "nsfw_level": image_info.get("nsfw_level", 0),
        "meta": meta,
        "resources": meta.get("resources") or image_info.get("resources") or [],
        "has_workflow": has_workflow,
        "workflow_json": embed.get("workflow"),
        "api_prompt_json": embed.get("api_prompt"),
        "parameters_text": embed.get("parameters", ""),
        "parsed_params": embed.get("parsed", {}),
        "embed_source": embed.get("source_type", "none"),
        "media_type": "video" if is_video else "image",
    })

    logger.info("[Noctyra-WF] %s已保存到图库: %s (id=%s)",
                "视频" if is_video else "图片", raw_name, record_id)
    return {"success": True, "id": record_id, "file_path": save_path}
