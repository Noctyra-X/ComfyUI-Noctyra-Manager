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
base_model 规范词表 —— 对齐 CivitAI 官方枚举（参考 ComfyUI-Lora-Manager 的成熟做法）。

真相源 = CivitAI 官方 base model 列表（ActiveBaseModel）。本模块：
  1. 内置一份官方快照做兜底（_OFFICIAL_FALLBACK）。
  2. 启动时从 https://civitai.red/api/v1/enums 动态拉取最新列表覆盖（缓存到磁盘）。
     → CivitAI 一上架新底模，我们自动就有，不必改代码。
  3. normalize_base_model()：值若（忽略大小写/分隔符后）命中官方名 → 返回官方拼写；
     否则查一张「元数据杂写法/历史自造名 → 官方名」的小映射表；都不中就原样返回。

注意：CivitAI 的颗粒度是「官方区分的不同底模」——LTXV2 / LTXV 2.3、Flux.2 Klein 9B / 9B-base
是**不同**模型，不合并。我们只把 sd_1.5 / flux1 / ltx2 / 自造的 "Qwen Image"/"Flux 1" 这类
非官方写法映射到官方名。
"""

import json
import logging

logger = logging.getLogger("noctyra.base_models")


def _norm_key(s: str) -> str:
    """归一化匹配键：小写 + 去掉空格/下划线/连字符/点。"""
    return (s or "").lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", "")


# CivitAI 官方 base model 快照（来自 ActiveBaseModel；动态拉取失败时兜底）。
_OFFICIAL_FALLBACK = [
    "SD 1.4", "SD 1.5", "SD 1.5 LCM", "SD 1.5 Hyper", "SD 2.0", "SD 2.1",
    "SD 3", "SD 3.5", "SD 3.5 Medium", "SD 3.5 Large", "SD 3.5 Large Turbo",
    "SDXL 1.0", "SDXL Lightning", "SDXL Hyper", "Stable Cascade",
    "Flux.1 D", "Flux.1 S", "Flux.1 Krea", "Flux.1 Kontext",
    "Flux.2 D", "Flux.2 Klein 9B", "Flux.2 Klein 9B-base", "Flux.2 Klein 4B", "Flux.2 Klein 4B-base",
    "AuraFlow", "Chroma", "PixArt a", "PixArt E", "Hunyuan 1", "Lumina", "Kolors",
    "NoobAI", "Illustrious", "Pony", "Pony V7", "HiDream", "Qwen",
    "ZImageTurbo", "ZImageBase", "SVD", "LTXV", "LTXV2", "LTXV 2.3",
    "CogVideoX", "Mochi",
    "Wan Video", "Wan Video 1.3B t2v", "Wan Video 14B t2v",
    "Wan Video 14B i2v 480p", "Wan Video 14B i2v 720p",
    "Wan Video 2.2 TI2V-5B", "Wan Video 2.2 T2V-A14B", "Wan Video 2.2 I2V-A14B",
    "Wan Video 2.5 T2V", "Wan Video 2.5 I2V",
    "Hunyuan Video", "Anima",
]

# 「非官方写法 → 官方名」映射：safetensors 元数据杂写法、历史自造名、明显笔误。
# 键写人类可读形式，加载时统一 _norm_key 化。
_RAW_ALIASES = {
    # 历史自造名（我们以前发明的）→ 官方
    "Qwen Image": "Qwen", "qwen_image": "Qwen", "qwen-image": "Qwen", "QwenImage": "Qwen",
    "qwen_image_edit": "Qwen", "qwen-image-edit": "Qwen", "QwenImageEdit": "Qwen", "Qwen Image Edit": "Qwen",
    "Flux 1": "Flux.1 D", "Flux1": "Flux.1 D",
    "Flux 2": "Flux.2 D",
    "SDXL": "SDXL 1.0",
    "SD2.x": "SD 2.0", "SD 2.x": "SD 2.0",
    # 注意：不把 "Wan 2.1"/"Wan 2.2" 折叠成泛化 "Wan Video"——CivitAI 官方按变体细分
    # （Wan Video 2.2 T2V-A14B 等），2.1/2.2 是不同架构，合并会丢版本区分（违背「不合并」原则）。
    # 结构/文件名只能粗判到版本、判不出具体变体，故让 "Wan 2.x" 原样保留；线上匹配到时
    # 会被真实官方变体名覆盖（scan upsert 的 matched=1 守卫）。
    "ZImage": "ZImageBase",
    # safetensors / kohya 元数据 ss_base_model_version 杂写法
    "sd_1.5": "SD 1.5", "sd-v1-5": "SD 1.5", "sd1.5": "SD 1.5",
    "stable-diffusion-v1-5": "SD 1.5", "stable-diffusion-v1": "SD 1.5",
    "sd_v2": "SD 2.0", "sd-v2": "SD 2.0", "stable-diffusion-v2": "SD 2.0", "sd2": "SD 2.0",
    "sd-v2-1": "SD 2.1",
    "sd3": "SD 3", "stable-diffusion-3": "SD 3",
    "sdxl": "SDXL 1.0", "sdxl_base": "SDXL 1.0", "stable-diffusion-xl": "SDXL 1.0", "sd-xl": "SDXL 1.0", "sd_xl": "SDXL 1.0",
    "flux": "Flux.1 D", "flux1": "Flux.1 D", "flux.1": "Flux.1 D",
    "flux1-dev": "Flux.1 D", "flux.1-dev": "Flux.1 D", "flux-1-dev": "Flux.1 D",
    "flux1-schnell": "Flux.1 S", "flux-1-schnell": "Flux.1 S",
    "flux.2": "Flux.2 D", "flux2": "Flux.2 D",
    "ltx2": "LTXV2", "ltx-2": "LTXV2", "ltxvideo": "LTXV",
    "illustriousxl": "Illustrious", "illustrious-xl": "Illustrious", "il": "Illustrious",
    "noob-ai": "NoobAI", "noobaixl": "NoobAI",
    "pony-diffusion": "Pony",
    "hunyuanvideo": "Hunyuan Video",
    "cogvideo": "CogVideoX",
    "stable-cascade": "Stable Cascade",
}

# ===== 运行时状态（动态拉取可覆盖官方集）=====
_OFFICIAL = list(_OFFICIAL_FALLBACK)
_OFFICIAL_LOOKUP = {}          # 归一化键 -> 官方拼写
_ALIASES = {_norm_key(k): v for k, v in _RAW_ALIASES.items()}


def _rebuild_lookup():
    global _OFFICIAL_LOOKUP
    lut = {}
    for name in _OFFICIAL:
        lut.setdefault(_norm_key(name), name)
    _OFFICIAL_LOOKUP = lut


_rebuild_lookup()


def official_base_models() -> list:
    """当前官方 base model 列表（动态拉取后会更新）。"""
    return list(_OFFICIAL)


def normalize_base_model(value: str) -> str:
    """把任意来源的 base_model 归一到 CivitAI 官方名。

    1. 命中官方名（忽略大小写/分隔符）→ 官方拼写
    2. 命中「杂写法→官方」映射 → 官方名
    3. 空/Unknown/Other → "Unknown"
    4. 其余 → 原样保留（CivitAI 新底模在动态列表更新前会先这样留着，不丢）
    """
    s = (value or "").strip()
    if not s or s.lower() in ("unknown", "other"):
        return "Unknown"
    key = _norm_key(s)
    if key in _OFFICIAL_LOOKUP:
        return _OFFICIAL_LOOKUP[key]
    if key in _ALIASES:
        return _ALIASES[key]
    return s


def set_official_models(names) -> int:
    """用动态拉取/缓存的官方列表覆盖（与内置兜底求并，绝不因接口少给而丢已知项）。"""
    global _OFFICIAL
    cleaned = [str(n).strip() for n in (names or []) if n and str(n).strip()]
    if not cleaned:
        return 0
    seen, merged = set(), []
    for n in cleaned + _OFFICIAL_FALLBACK:
        k = _norm_key(n)
        if k not in seen:
            seen.add(k)
            merged.append(n)
    _OFFICIAL = merged
    _rebuild_lookup()
    return len(_OFFICIAL)


def load_official_cache(path: str):
    """启动时从磁盘缓存（上次拉取结果）加载官方列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        names = data.get("models") if isinstance(data, dict) else None
        if names:
            set_official_models(names)
            logger.info("[Noctyra-MM] 从缓存载入 %d 个官方 base model", len(_OFFICIAL))
    except Exception:
        pass


async def fetch_official_base_models(cache_path: str = None, timeout: float = 12.0) -> int:
    """从 CivitAI 拉取最新官方 base model 枚举，更新内存 + 写缓存。失败返回 0（保持兜底）。

    走插件配置的代理（proxy_util，含 http/https/socks + 认证），国内也能拉。"""
    import aiohttp
    try:
        from .proxy_util import get_proxy, make_connector
        connector, proxy = make_connector(), get_proxy()
    except Exception:
        connector, proxy = None, None
    url = "https://civitai.red/api/v1/enums"
    try:
        async with aiohttp.ClientSession(connector=connector) as sess:
            async with sess.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return 0
                data = await resp.json()
        names = data.get("ActiveBaseModel") or data.get("BaseModel") or []
        if not names:
            return 0
        n = set_official_models(names)
        if cache_path:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"models": names}, f, ensure_ascii=False)
            except Exception:
                pass
        logger.info("[Noctyra-MM] 已从 CivitAI 拉取 %d 个官方 base model", n)
        return n
    except Exception as e:
        logger.debug("[Noctyra-MM] 拉取官方 base model 失败（用兜底）: %s", e)
        return 0
