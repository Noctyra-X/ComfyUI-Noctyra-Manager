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
本地模型扫描器

功能：
- 递归扫描指定目录下的模型文件
- 计算文件 SHA256 哈希（取前 10MB 快速哈希 + 完整哈希）
- 读取 safetensors 文件的元数据（训练信息、触发词等）
- 构建本地模型索引
"""

import os
import hashlib
import json
import logging
import asyncio
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor


logger = logging.getLogger("noctyra.scanner")

# 线程池用于文件 IO（扫描、哈希）。manager 计算哈希时也复用此池以统一并发度。
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="noctyra-io")


def get_io_executor():
    """返回共享的 IO 线程池，供 manager/其它模块跑 CPU/IO 混合任务（如 sha256）"""
    return _executor


@dataclass
class ModelInfo:
    """本地模型信息"""
    file_path: str
    file_name: str
    file_size: int  # bytes
    file_ext: str
    sha256: str = ""
    # 文件系统信息
    folder: str = ""  # 相对扫描根目录的子文件夹路径
    file_modified: float = 0.0  # os.path.getmtime
    model_type: str = ""  # "lora" / "checkpoint" / "embedding" / "other"
    lora_subtype: str = ""  # 仅 lora：'lora'/'lycoris'/'dora'（筛选用，不影响 model_type）
    file_corrupt: int = 0  # 扫描时判定的损坏标志：1=头截断/数据区未铺满等(safetensors 会拒绝加载)
    # safetensors 元数据
    base_model: str = "Unknown"
    trained_words: List[str] = field(default_factory=list)
    metadata_raw: Dict = field(default_factory=dict)
    # 在线匹配信息
    source: str = ""  # "civitai" / "huggingface" / "unknown"
    source_url: str = ""
    model_name: str = ""
    model_description: str = ""
    preview_url: str = ""
    tags: List[str] = field(default_factory=list)
    # 匹配状态
    matched: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ModelInfo":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def compute_sha256(file_path: str) -> str:
    """计算文件完整 SHA256"""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        logger.error("[Noctyra-MM] SHA256 计算失败 %s: %s", file_path, e)
        return ""


def compute_sha256_partial(file_path: str, size: int = 10 * 1024 * 1024) -> str:
    """计算文件前 N 字节的 SHA256（用于快速预匹配）"""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            data = f.read(size)
            sha256.update(data)
        return sha256.hexdigest()
    except Exception as e:
        logger.error("[Noctyra-MM] 部分 SHA256 计算失败 %s: %s", file_path, e)
        return ""


def read_safetensors_metadata(file_path: str) -> Dict:
    """读取 safetensors 文件的元数据"""
    metadata = {}
    if not file_path.endswith(".safetensors"):
        return metadata

    try:
        from safetensors import safe_open
        with safe_open(file_path, framework="pt", device="cpu") as f:
            raw = f.metadata()
            if raw:
                metadata = dict(raw)
    except Exception as e:
        logger.warning("[Noctyra-MM] 读取 safetensors 元数据失败 %s: %s", file_path, e)

    return metadata


def read_safetensors_header(file_path: str, max_tensors: int = 50000) -> Dict:
    """读 safetensors 文件头：返回 {metadata, tensors:[{name,dtype,shape,n_bytes}]}。

    safetensors 头部 = 前 8 字节小端 u64（头 JSON 长度）+ 该长度的 JSON。
    JSON 里每个张量名 -> {dtype, shape, data_offsets}，外加可选 __metadata__。
    只读头部（几 KB ~ 几 MB），不加载张量数据，6GB 文件也瞬间返回；无需 torch。
    """
    import struct
    if not file_path.endswith(".safetensors"):
        return {"metadata": {}, "tensors": [], "error": "仅支持 .safetensors"}
    try:
        with open(file_path, "rb") as f:
            n_bytes = f.read(8)
            if len(n_bytes) < 8:
                return {"metadata": {}, "tensors": [], "error": "文件过小"}
            n = struct.unpack("<Q", n_bytes)[0]
            if n <= 0 or n > 200 * 1024 * 1024:   # 头不该超 200MB，异常即拒
                return {"metadata": {}, "tensors": [], "error": "文件头长度异常"}
            header = json.loads(f.read(n).decode("utf-8"))
    except Exception as e:
        logger.warning("[Noctyra-MM] 读取 safetensors 头失败 %s: %s", file_path, e)
        return {"metadata": {}, "tensors": [], "error": str(e)}

    metadata = header.pop("__metadata__", None) or {}
    tensors = []
    truncated = False
    for name, info in header.items():
        if not isinstance(info, dict):
            continue
        if len(tensors) >= max_tensors:
            truncated = True
            break
        offs = info.get("data_offsets") or [0, 0]
        n_b = (offs[1] - offs[0]) if isinstance(offs, list) and len(offs) == 2 else 0
        tensors.append({
            "name": name,
            "dtype": info.get("dtype", ""),
            "shape": info.get("shape", []),
            "n_bytes": n_b,
        })
    return {
        "metadata": metadata if isinstance(metadata, dict) else {},
        "tensors": tensors,
        "tensor_count": len(tensors),
        "truncated": truncated,
    }


def _classify_safetensors_keys(keys: list) -> str:
    """纯函数：从 tensor key 列表推断类型（便于单测，不涉及 IO）。"""
    if not keys:
        return ""

    sample = keys[:300]  # 采样足够代表结构，避免大模型全量遍历

    # 1. LoRA / LyCORIS / DoRA：任一 key 含 "lora" 即判定。
    #    覆盖全部格式：kohya（lora_unet_*/lora_te_*）、diffusers 旧（.lora_up./.lora_down.）、
    #    PEFT/diffusers 新（.lora_A./.lora_B.，常见于 Flux/SD3/Qwen LoRA）。
    #    正经的 UNet/Checkpoint 张量名里绝不会出现 "lora"，故"含 lora 子串"安全且不漏判
    #    ——之前只认 kohya 前缀/后缀，新格式 Flux/Qwen LoRA 落到了 has_unet 被误判成 unet。
    #    另加 LyCORIS（lokr_/loha_）、DoRA（.dora_scale）、以及 LyCORIS 的 .alpha 标识。
    for k in sample:
        kl = k.lower()
        if ("lora" in kl or kl.startswith(("lokr_", "loha_"))
                or ".dora_scale" in kl or kl.endswith(".alpha")):
            return "lora"

    # 2. Embedding：只有少量 embedding 专用 key
    _EMB_KEYS = {"emb_params", "string_to_param", "clip_g", "clip_l", "clip_h", "vector"}
    if len(keys) <= 8 and all(k in _EMB_KEYS or k.startswith("string_to_param") for k in keys):
        return "embedding"

    # 3. ControlNet：在判定 UNet/Checkpoint 前先识别，避免 input_blocks 导致误判
    #    特征 key（任一命中即可）：
    #      - zero_convs / zero_conv：原始 ControlNet 的零卷积标识
    #      - input_hint_block / hint_block：控制图预处理模块
    #      - control_model 前缀：ControlNet 打包格式
    #      - controlnet_ / controlnet. 前缀：Flux / XLabs ControlNet
    _CONTROLNET_SIGNALS = ("zero_convs", "zero_conv", "input_hint_block", "hint_block",
                           "control_model.", "controlnet_", "controlnet.")
    for k in sample:
        kl = k.lower()
        for sig in _CONTROLNET_SIGNALS:
            if sig in kl:
                return "controlnet"

    # 4. 组件探测
    has_vae = any(
        k.startswith(("first_stage_model.", "vae.", "vae_"))
        or ".vae." in k
        for k in sample
    )
    has_text_encoder = any(
        k.startswith((
            "cond_stage_model.", "text_encoders.", "text_encoder.",
            "text_encoder_2.", "text_model.", "t5xxl.", "clip_l.", "clip_g.",
        ))
        for k in sample
    )
    has_unet = any(
        k.startswith((
            "model.diffusion_model.",
            "diffusion_model.",
            "transformer.",
            "double_blocks.", "single_blocks.",   # Flux / SD3
            "model.model.",                        # SDXL 变体
        ))
        for k in sample
    )

    # standalone 文本编码器（裸 key，不带上面的组件前缀）：T5/UMT5、CLIP、LLM/VLM 编码器
    # （如 umt5_xxl、qwen_2.5_vl）。这类只有编码器结构、无 unet/vae，之前全落到 ""→unknown。
    #   T5/UMT5: shared.* + encoder.block.*    LLM/VLM: model.embed_tokens. / lm_head / language_model.
    standalone_te = (
        (any(k.startswith("shared.") for k in sample)
         and any(k.startswith("encoder.block.") or "encoder.final_layer_norm" in k for k in sample))
        or any(k.startswith(("model.embed_tokens.", "language_model.", "text_projection")) for k in sample)
        or any(k.endswith("lm_head.weight") or ".lm_head." in k for k in sample)
    )
    has_clip_vision = any(k.startswith(("vision_model.", "visual.")) for k in sample)
    has_motion = any("motion_module" in k or "temporal_transformer" in k for k in sample)

    if has_motion:
        return "motion"
    if has_vae and not has_unet and not has_text_encoder:
        return "vae"
    # 文本编码器（T5/UMT5、CLIP 文本侧、LLM/VLM 编码器如 qwen_2.5_vl）：只有编码器结构、无 unet/vae
    if (has_text_encoder or standalone_te) and not has_unet and not has_vae:
        return "text_encoder"
    # CLIP 视觉（图像编码器）：有 vision 结构、不是上面的文本编码器、无 unet/vae
    if has_clip_vision and not has_unet and not has_vae:
        return "clip_vision"
    if has_unet and (has_vae or has_text_encoder):
        return "checkpoint"
    if has_unet:
        return "unet"
    return ""


def classify_lora_subtype(keys: list) -> str:
    """区分 LoRA 家族细分（仅对已判定为 lora 的模型有意义）。返回 'lora'/'lycoris'/'dora'。

      - DoRA：含 `dora_scale`
      - LyCORIS：含 LoHa(`hada_`) / LoKr(`lokr_`) / `loha_` 标识
      - 其余普通 LoRA → 'lora'

    注意：CivitAI 的 'LoCon'（卷积 LoRA）结构上就是普通 lora_up/lora_down，无法从结构区分，
    这里会判成 'lora'；线上匹配到 CivitAI 时由其 type 权威覆盖为 'lycoris'。"""
    sample = keys[:400]
    for k in sample:
        if "dora_scale" in k.lower():
            return "dora"
    for k in sample:
        kl = k.lower()
        if "lokr_" in kl or "hada_" in kl or "loha_" in kl:
            return "lycoris"
    return "lora"


def infer_type_from_safetensors(file_path: str) -> str:
    """通过 safetensors 的 tensor key 结构判定模型类型，不依赖文件夹名。

    返回 'lora' / 'checkpoint' / 'unet' / 'vae' / 'embedding' / 'controlnet' 或
    空串（无法判定，调用方应回退到目录名）。

    只读 safetensors 文件头（几 KB），不加载张量数据，速度很快。
    """
    return classify_safetensors_file(file_path)[0]


def classify_safetensors_file(file_path: str):
    """读一次 safetensors 文件头，返回 (model_type, lora_subtype)。

    lora_subtype 仅当 model_type=='lora' 时有值（'lora'/'lycoris'/'dora'），否则 ''。
    比分别调用省一次头读取。"""
    if not file_path.endswith(".safetensors"):
        return "", ""
    try:
        from safetensors import safe_open
        with safe_open(file_path, framework="pt", device="cpu") as f:
            keys = list(f.keys())
    except Exception as e:
        logger.debug("[Noctyra-MM] safetensors key 读取失败 %s: %s", file_path, e)
        return "", ""
    mtype = _classify_safetensors_keys(keys)
    subtype = classify_lora_subtype(keys) if mtype == "lora" else ""
    return mtype, subtype


def check_safetensors_integrity(file_path: str, deep: bool = True) -> dict:
    """检测模型文件是否损坏（截断 / 头错乱 / safetensors 无法加载）。
    返回 {ok: bool, error: str, detail: str}。非 safetensors 只确认文件存在（无法深度校验）。
    deep=False：只做"头 + 数据区偏移"轻量校验(够抓截断/未铺满这类常见下载损坏)，跳过 safe_open，
    供扫描批量用；deep=True(默认)额外用 safetensors 实际打开，供右键单个深度检测用。"""
    import struct
    if not os.path.isfile(file_path):
        return {"ok": False, "error": "文件不存在于磁盘", "detail": ""}
    if not file_path.endswith(".safetensors"):
        return {"ok": True, "error": "", "detail": "非 safetensors（gguf/ckpt 等），仅确认文件存在、无法做结构校验"}
    try:
        sz = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return {"ok": False, "error": "文件过小，连 8 字节头长都不完整", "detail": ""}
            n = struct.unpack("<Q", head)[0]
            if n <= 0 or 8 + n > sz:
                return {"ok": False, "error": "头长非法/超过文件大小（头被截断）", "detail": f"声明头长 {n}，文件 {sz}"}
            try:
                hdr = json.loads(f.read(n))
            except Exception as e:
                return {"ok": False, "error": "JSON 头无法解析（损坏）", "detail": str(e)[:120]}
    except OSError as e:
        return {"ok": False, "error": "读取文件出错", "detail": str(e)[:120]}

    tensors = {k: v for k, v in hdr.items() if k != "__metadata__"}
    data_region = sz - 8 - n
    max_end = max((v.get("data_offsets", [0, 0])[1] for v in tensors.values()), default=0)
    if max_end > data_region:
        return {"ok": False, "error": f"文件被截断：张量需 {max_end} 字节，文件只有 {data_region}（缺 {max_end - data_region}）", "detail": ""}
    if max_end < data_region:
        return {"ok": False, "error": f"数据区未被张量铺满（多 {data_region - max_end} 字节无主），safetensors 会拒绝加载", "detail": ""}

    if not deep:
        return {"ok": True, "error": "", "detail": f"{len(tensors)} 个张量，头与数据区一致（轻量校验）"}

    # safetensors 实际打开（最权威，等同 ComfyUI 加载路径）。torch 不在时跳过，前面的结构校验已够用
    try:
        from safetensors import safe_open
        with safe_open(file_path, framework="pt", device="cpu") as sf:
            ks = list(sf.keys())
            if ks:
                sf.get_tensor(ks[0])  # 读一个张量，触发完整反序列化校验
    except ImportError:
        pass
    except Exception as e:
        return {"ok": False, "error": "safetensors 无法加载（损坏，ComfyUI 也会加载失败）", "detail": str(e)[:160]}

    return {"ok": True, "error": "", "detail": f"{len(tensors)} 个张量，头与数据区一致，可正常加载"}


def extract_trained_words(metadata: Dict) -> List[str]:
    """从 safetensors 元数据提取触发词。

    只取 ss_datasets 的 class_tokens（训练时显式设置的激活词）。
    **不再取 ss_tag_frequency** —— 那是数据集打标频率表（一个风格 LoRA 动辄成百上千个
    booru tag，如 blush/1girl/breasts），根本不是触发词；旧逻辑全取它，导致触发词被
    数据集标签污染（实测有模型 556 个）。没有 class_tokens 就留空，在线匹配会用
    CivitAI trainedWords 覆盖/补充；都没有就是真没触发词，不显示即可。"""
    words = []
    datasets_str = metadata.get("ss_datasets", "")
    if datasets_str:
        try:
            datasets = json.loads(datasets_str)
            if isinstance(datasets, list):
                for ds in datasets:
                    if not isinstance(ds, dict):
                        continue
                    for sub in (ds.get("subsets") or []):
                        if not isinstance(sub, dict):
                            continue
                        ct = (sub.get("class_tokens") or "").strip()
                        if ct and ct not in words:
                            words.append(ct)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

    return words


# 注：原 base_model 推断机制（determine_base_model 读 safetensors 头、refine_base_model +
# _FINE_GRAINED_PATTERNS 按文件名关键词细化）已整体移除——base_model 改为只用 CivitAI 匹配
# 的权威值，未匹配留 Unknown（学 Lora-Manager，不猜）。扫描/导入/整理/下载落点统一用该 verbatim 值。


class ModelScanner:
    """本地模型扫描器"""

    def __init__(self, scan_extensions: List[str] = None):
        self.scan_extensions = scan_extensions or [".safetensors"]

    def scan_directory(self, root_dir: str, known_stats: Optional[Dict[str, tuple]] = None) -> List[ModelInfo]:
        """同步扫描单个目录。

        Args:
            known_stats: 可选，{file_path: (mtime, file_size)} DB 中已知文件的快照。
                         mtime 与 size 都匹配时才跳过 safetensors 元数据解析；只比 mtime
                         会被"原地替换但保留/未变 mtime"骗过而留旧 hash，故一并比 size。
                         第一次扫描（known_stats=None 或空）时走全量路径。
        """
        results = []
        skipped = 0
        root_dir = os.path.normpath(root_dir)
        if not os.path.isdir(root_dir):
            logger.warning("[Noctyra-MM] 目录不存在: %s", root_dir)
            return results

        logger.debug("[Noctyra-MM] 开始扫描目录: %s", root_dir)
        known_stats = known_stats or {}

        for dirpath, dirnames, filenames in os.walk(root_dir):
            # 跳过隐藏目录
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            # 计算相对于扫描根目录的子文件夹路径
            folder = os.path.relpath(dirpath, root_dir)
            if folder == ".":
                folder = os.path.basename(root_dir)
            else:
                folder = os.path.basename(root_dir) + "/" + folder.replace("\\", "/")

            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in self.scan_extensions:
                    continue

                file_path = os.path.normpath(os.path.join(dirpath, filename))
                try:
                    stat = os.stat(file_path)
                    file_size = stat.st_size
                    file_modified = stat.st_mtime
                except OSError:
                    continue

                info = ModelInfo(
                    file_path=file_path,
                    file_name=filename,
                    file_size=file_size,
                    file_ext=ext,
                    folder=folder,
                    file_modified=file_modified,
                )

                # 增量跳过：文件在 DB 中且 mtime（1 秒容差）与 size 都未变 → 跳过 metadata 解析
                # mtime 容差应对 FAT32 精度 2s / 跨平台网络盘波动；size 防"原地替换保留 mtime"
                known = known_stats.get(file_path)
                unchanged = (
                    known is not None
                    and abs(known[0] - file_modified) < 1.0
                    and known[1] == file_size
                )

                if unchanged:
                    skipped += 1
                    # 标记为增量跳过，manager._do_scan 据此跳过 upsert
                    info.metadata_raw = {"_noctyra_incremental_skip": True}
                else:
                    # 全量解析：safetensors 元数据
                    if ext == ".safetensors":
                        metadata = read_safetensors_metadata(file_path)
                        if metadata:
                            info.metadata_raw = metadata
                            # base_model 不再从 safetensors 头/文件名推断（学 Lora-Manager）：
                            # 统一等 CivitAI 匹配给权威值，未匹配就留 Unknown，宁可 Unknown 也不猜。
                            # safetensors 头仍读，用于 trigger words 和类型分类（与 base 无关）。
                            info.trained_words = extract_trained_words(metadata)
                        # 按 tensor key 结构推断类型 + LoRA 家族细分（一次读头）
                        detected_type, lora_subtype = classify_safetensors_file(file_path)
                        if detected_type:
                            info.model_type = detected_type
                        if lora_subtype:
                            info.lora_subtype = lora_subtype
                        # 顺手判损坏(头截断/数据区未铺满等常见下载损坏)；轻量校验，不做 safe_open
                        info.file_corrupt = 0 if check_safetensors_integrity(file_path, deep=False)["ok"] else 1

                results.append(info)
                logger.debug("[Noctyra-MM] 发现模型: %s (%s)%s",
                             filename, self._format_size(file_size),
                             " [增量跳过]" if unchanged else "")

        if known_stats and skipped:
            logger.info("[Noctyra-MM] 增量扫描 %s: %d 个未变文件跳过元数据解析（共 %d）",
                        root_dir, skipped, len(results))
        return results

    async def scan_directory_async(self, root_dir: str, known_stats: Optional[Dict[str, tuple]] = None) -> List[ModelInfo]:
        """异步扫描目录，透传 known_stats 给增量加速"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self.scan_directory, root_dir, known_stats)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"
