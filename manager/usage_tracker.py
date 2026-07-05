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
模型使用统计

通过 ComfyUI 的 on_prompt_handler 钩子拦截工作流提交，
解析 prompt 中的模型文件引用，更新数据库使用计数。
"""

import asyncio
import logging
from typing import Set

logger = logging.getLogger("noctyra.usage_tracker")


def _log_usage_future(fut):
    """消费后台写库 future 的异常。

    fire-and-forget 的 run_in_executor future 不被 await，若 increment_usage
    抛异常，asyncio 会打印 "Future exception was never retrieved"。这里在 done
    回调里显式取一次异常并降级为 debug 日志，避免未处理异常告警。"""
    try:
        exc = fut.exception()
    except Exception:
        # future 被取消（CancelledError）等情况，忽略
        return
    if exc is not None:
        logger.debug("[Noctyra-MM] 使用统计写库失败: %s", exc)

# 已知的模型加载节点及其文件名输入字段
_MODEL_INPUT_FIELDS = {
    "CheckpointLoaderSimple": ["ckpt_name"],
    "CheckpointLoader": ["ckpt_name"],
    "unCLIPCheckpointLoader": ["ckpt_name"],
    "LoraLoader": ["lora_name"],
    "LoraLoaderModelOnly": ["lora_name"],
    "VAELoader": ["vae_name"],
    "ControlNetLoader": ["control_net_name"],
    "CLIPLoader": ["clip_name"],
    "DualCLIPLoader": ["clip_name1", "clip_name2"],
    "TripleCLIPLoader": ["clip_name1", "clip_name2", "clip_name3"],
    "UNETLoader": ["unet_name"],
    "StyleModelLoader": ["style_model_name"],
    "GLIGENLoader": ["gligen_name"],
    "UpscaleModelLoader": ["model_name"],
    "HypernetworkLoader": ["hypernetwork_name"],
    "Embedding": ["embedding_name"],
}


def extract_model_names(prompt_data: dict) -> Set[str]:
    """从 prompt JSON 中提取所有引用的模型文件名"""
    names = set()
    prompt = prompt_data.get("prompt", {})
    if not isinstance(prompt, dict):
        return names

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue

        fields = _MODEL_INPUT_FIELDS.get(class_type, [])
        for field in fields:
            val = inputs.get(field, "")
            if isinstance(val, str) and val:
                # ComfyUI 的文件名可能包含子文件夹路径如 "sd15/model.safetensors"
                # 取最后一段作为文件名
                fname = val.rsplit("/", 1)[-1] if "/" in val else val
                fname = fname.rsplit("\\", 1)[-1] if "\\" in fname else fname
                names.add(fname)

    return names


def setup_usage_tracking():
    """注册 prompt 钩子到 ComfyUI"""
    try:
        from server import PromptServer

        def on_prompt(json_data):
            try:
                names = extract_model_names(json_data)
                if names:
                    from .routes import get_manager
                    mgr = get_manager()
                    name_list = list(names)
                    # 本回调在 ComfyUI 的 async post_prompt 热路径上同步执行。
                    # increment_usage 会持数据库写锁，直接调用会阻塞事件循环，
                    # 拖慢每一次 prompt 提交。改成 fire-and-forget：把同步写库丢到
                    # 线程池执行，不 await 其结果，统计语义（最终照常写库）不变。
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop is not None:
                        fut = loop.run_in_executor(
                            None, mgr.db.increment_usage, name_list
                        )
                        # 不 await，但挂 done 回调消费异常，避免未处理异常告警。
                        fut.add_done_callback(_log_usage_future)
                    else:
                        # 无运行中的 loop（非 async 上下文），退回同步写。
                        mgr.db.increment_usage(name_list)
                    logger.debug("[Noctyra-MM] 记录模型使用: %s", ", ".join(names))
            except Exception as e:
                logger.debug("[Noctyra-MM] 使用统计记录失败: %s", e)
            return json_data

        PromptServer.instance.add_on_prompt_handler(on_prompt)
        logger.info("[Noctyra-MM] 模型使用统计已启用")
    except Exception as e:
        logger.warning("[Noctyra-MM] 无法启用使用统计: %s", e)
