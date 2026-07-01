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
Recipe 系统 — 把 workflow_images 表里的一行升级为"可复用配方"。

一个"配方"= 一张图的 LoRA 组合 + checkpoint + base_model。指纹（fingerprint）
是这个组合的确定性 SHA256，用于跨机器 / 跨导入去重。

算法版本化：fingerprint_v1 是当前默认；未来改算法时新字段存 recipe_version=2，
老数据按 recipe_version 字段选用对应算法重算。
"""

import hashlib
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("noctyra.recipes")

CURRENT_RECIPE_VERSION = 1

# 算作 LoRA 类型的别名（CivitAI 有 LoCon / DoRA / LyCORIS 等变体）
_LORA_TYPES = {"lora", "locon", "dora", "lycoris", "lycori"}
_CHECKPOINT_TYPES = {"checkpoint", "checkpointmerge"}


def _normalize_base_model(bm: Optional[str]) -> str:
    """归一化 base_model：去空格、小写。'Flux.1 D' → 'flux.1 d'"""
    if not bm:
        return ""
    s = str(bm).strip().lower()
    # 合并连续空白
    s = re.sub(r"\s+", " ", s)
    return s


def _extract_weight(resource: Dict[str, Any]) -> float:
    """
    从资源 dict 提取权重（strength）。
    CivitAI meta.resources 里字段名不统一：weight / strength / strengthModel 都见过。
    缺省返回 1.0。
    """
    for key in ("weight", "strength", "strengthModel"):
        if key in resource and resource[key] is not None:
            try:
                return float(resource[key])
            except (TypeError, ValueError):
                continue
    return 1.0


def _resource_type(resource: Dict[str, Any]) -> str:
    t = resource.get("type") or resource.get("modelType") or ""
    return str(t).strip().lower()


def _version_id(resource: Dict[str, Any]):
    """优先 modelVersionId，退回 versionId / vid"""
    for key in ("modelVersionId", "versionId", "version_id", "vid"):
        v = resource.get(key)
        if v is None:
            continue
        try:
            iv = int(v)
            if iv > 0:
                return iv
        except (TypeError, ValueError):
            continue
    return None


def _resource_name(resource: Dict[str, Any]) -> str:
    for key in ("name", "versionName", "modelName", "fileName", "file_name"):
        v = resource.get(key)
        if v:
            return str(v).strip()
    return ""


def fingerprint_v1(base_model: Optional[str], resources: Optional[List[Dict[str, Any]]]) -> str:
    """
    计算配方指纹 v1。确定性算法：同样 base_model + 同样 LoRA 组合（权重保留 4 位小数）
    → 同一个 SHA256，无论来源 / 顺序。

    规则：
      1. base_model 归一化小写
      2. 所有 type=checkpoint 的资源取 version_id（或 name），排序
      3. 所有 type=lora/locon/dora/lycoris 的资源取 (version_id 或 name, round(weight, 4))，排序
      4. 拼成 `bm|cp:v1|cp:v2|lora:v3:0.8000|lora:v4:1.0000` 之类
      5. SHA256(utf-8) → hex

    没有 version_id 的资源用 `name:xxx` 作为 key，容错但不保证跨平台唯一。
    """
    parts: List[str] = [_normalize_base_model(base_model)]

    resources = resources or []

    # checkpoints
    ckpts: List[str] = []
    for r in resources:
        if _resource_type(r) in _CHECKPOINT_TYPES:
            vid = _version_id(r)
            key = str(vid) if vid else f"name:{_resource_name(r).lower()}"
            if key and key != "name:":
                ckpts.append(f"cp:{key}")
    ckpts.sort()
    parts.extend(ckpts)

    # loras
    loras: List[str] = []
    for r in resources:
        if _resource_type(r) not in _LORA_TYPES:
            continue
        vid = _version_id(r)
        key = str(vid) if vid else f"name:{_resource_name(r).lower()}"
        if not key or key == "name:":
            continue
        weight = round(_extract_weight(r), 4)
        loras.append(f"lora:{key}:{weight:.4f}")
    loras.sort()
    parts.extend(loras)

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_fingerprint(base_model: Optional[str],
                        resources: Optional[List[Dict[str, Any]]],
                        version: int = CURRENT_RECIPE_VERSION) -> Dict[str, Any]:
    """
    入库时调用。返回 {fingerprint, recipe_version}。未来新算法版本在此 dispatch。
    """
    if version == 1:
        return {
            "fingerprint": fingerprint_v1(base_model, resources),
            "recipe_version": 1,
        }
    # 未知版本：fallback 到 v1
    logger.warning("[Noctyra-WF] 未知 recipe_version=%s，回退 v1", version)
    return {
        "fingerprint": fingerprint_v1(base_model, resources),
        "recipe_version": 1,
    }


def extract_base_model_from_image_info(image_info: Dict[str, Any],
                                        meta: Optional[Dict[str, Any]] = None) -> str:
    """
    尝试从 CivitAI 图片 info / meta 中推断 base_model。
    CivitAI 没直接给，只能从 checkpoint 资源或 meta.Model 字段猜。
    """
    meta = meta or image_info.get("meta") or {}

    # 1. meta.baseModel（罕见但最准）
    bm = meta.get("baseModel") or meta.get("base_model")
    if bm:
        return str(bm).strip()

    # 2. 从第一个 checkpoint 资源的 baseModel / modelName
    for r in (meta.get("resources") or []):
        if _resource_type(r) in _CHECKPOINT_TYPES:
            bm = r.get("baseModel") or r.get("base_model")
            if bm:
                return str(bm).strip()

    # 3. meta.Model（A1111 风格）—— 通常是 checkpoint 文件名，不是严格的 base_model，
    #    但聊胜于无
    return str(meta.get("Model") or "").strip()


def short_fingerprint(fp: str, length: int = 8) -> str:
    """截断指纹显示（前 N 位）"""
    if not fp:
        return ""
    return fp[:length]
