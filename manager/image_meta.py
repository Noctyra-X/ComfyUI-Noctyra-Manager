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
图片元数据提取 — 从 PNG/WebP/JPEG 中读取 ComfyUI workflow 和生成参数。

返回统一结构：
{
    "workflow": {...} | None,      # ComfyUI editor 格式 graph
    "api_prompt": {...} | None,    # ComfyUI 执行格式 prompt
    "parameters": "..." | None,   # A1111 风格原文
    "parsed": { prompt, negative_prompt, sampler, steps, ... },
    "source_type": "comfyui" | "a1111" | "none",
}
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("noctyra.image_meta")


def extract_image_meta(file_path: str) -> dict:
    """从图片文件中提取所有可用的元数据"""
    result = {
        "workflow": None,
        "api_prompt": None,
        "parameters": None,
        "parsed": {},
        "source_type": "none",
    }

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".png", ".webp", ".jpeg", ".jpg"):
        return result

    try:
        from PIL import Image
    except ImportError as e:
        logger.warning("[Noctyra-WF] PIL 不可用: %s", e)
        return result

    try:
        with Image.open(file_path) as img:
            info = dict(img.info or {})
            # 提前读取 EXIF，避免离开 with 块后文件已关闭
            exif_user_comment = None
            webp_exif = None
            if ext in (".jpeg", ".jpg"):
                try:
                    exif = img.getexif()
                    exif_user_comment = exif.get(0x9286)
                except Exception:
                    exif_user_comment = None
            elif ext == ".webp":
                # ComfyUI 动图 webp 把 workflow/prompt 写在 EXIF 里（SaveAnimatedWEBP）
                try:
                    webp_exif = dict(img.getexif())
                except Exception:
                    webp_exif = None
    except Exception as e:
        logger.warning("[Noctyra-WF] 读取图片元数据失败: %s — %s", file_path, e)
        return result

    # ComfyUI workflow (editor format)
    workflow_str = info.get("workflow")
    if workflow_str:
        try:
            result["workflow"] = json.loads(workflow_str)
            result["source_type"] = "comfyui"
        except (json.JSONDecodeError, TypeError):
            pass

    # ComfyUI prompt (API/execution format)
    prompt_str = info.get("prompt")
    if prompt_str:
        try:
            result["api_prompt"] = json.loads(prompt_str)
            if result["source_type"] == "none":
                result["source_type"] = "comfyui"
        except (json.JSONDecodeError, TypeError):
            pass

    # WebP（ComfyUI 动图 SaveAnimatedWEBP）：workflow/prompt 写在 EXIF 里
    #   tag 0x010F(Make)  = "workflow:<json>"
    #   tag 0x0110(Model) = "prompt:<json>"
    #   tag 0x010E(ImageDescription) = "<其它 key>:<json>"
    if ext == ".webp" and webp_exif:
        for tag in (0x010F, 0x0110, 0x010E):
            val = webp_exif.get(tag)
            if not isinstance(val, str) or ":" not in val:
                continue
            key, _, payload = val.partition(":")
            key = key.strip().lower()
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                continue
            if key == "workflow" and result["workflow"] is None:
                result["workflow"] = data
                result["source_type"] = "comfyui"
            elif key == "prompt" and result["api_prompt"] is None:
                result["api_prompt"] = data
                if result["source_type"] == "none":
                    result["source_type"] = "comfyui"

    # A1111 parameters
    params_str = info.get("parameters")
    if params_str and isinstance(params_str, str):
        result["parameters"] = params_str
        result["parsed"] = _parse_a1111_params(params_str)
        if result["source_type"] == "none":
            result["source_type"] = "a1111"

    # EXIF UserComment (JPEG)
    if ext in (".jpeg", ".jpg") and not result["parameters"] and exif_user_comment is not None:
        try:
            if isinstance(exif_user_comment, (str, bytes)):
                text = exif_user_comment.decode("utf-8", errors="ignore") if isinstance(exif_user_comment, bytes) else exif_user_comment
                if text and "Steps:" in text:
                    result["parameters"] = text
                    result["parsed"] = _parse_a1111_params(text)
                    if result["source_type"] == "none":
                        result["source_type"] = "a1111"
        except Exception:
            pass

    return result


def _parse_a1111_params(text: str) -> dict:
    """解析 A1111 风格的生成参数文本"""
    parsed = {}
    if not text:
        return parsed

    # 分离 prompt / negative / 尾行参数
    neg_split = text.split("Negative prompt:", 1)
    prompt_part = neg_split[0].strip()

    if len(neg_split) > 1:
        rest = neg_split[1]
        # 尾行参数以 "Steps:" 开头
        steps_split = rest.split("\nSteps:", 1)
        negative = steps_split[0].strip()
        tail = "Steps:" + steps_split[1] if len(steps_split) > 1 else ""
    else:
        # 没有 negative，直接在 prompt 后找 Steps:
        steps_split = prompt_part.split("\nSteps:", 1)
        if len(steps_split) > 1:
            prompt_part = steps_split[0].strip()
            tail = "Steps:" + steps_split[1]
        else:
            tail = ""
        negative = ""

    if prompt_part:
        parsed["prompt"] = prompt_part
    if negative:
        parsed["negative_prompt"] = negative

    # 解析 key: value 对（尾行）
    if tail:
        kv_map = {
            "Steps": "steps",
            "Sampler": "sampler",
            "CFG scale": "cfg_scale",
            "Seed": "seed",
            "Size": "size",
            "Model": "model",
            "Model hash": "model_hash",
            "Clip skip": "clip_skip",
            "Denoising strength": "denoising",
        }
        for key, field in kv_map.items():
            m = re.search(rf'{re.escape(key)}:\s*([^,]+)', tail)
            if m:
                val = m.group(1).strip()
                # 数值类型转换
                if field in ("steps", "seed"):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                elif field in ("cfg_scale", "denoising"):
                    try:
                        val = float(val)
                    except ValueError:
                        pass
                parsed[field] = val

    return parsed
