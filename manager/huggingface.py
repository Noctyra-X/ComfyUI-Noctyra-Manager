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
HuggingFace API 客户端

HuggingFace 不支持通过 SHA256 反查模型，采用多种策略匹配：
1. safetensors 元数据中可能包含来源信息
2. 文件名关键词搜索 quicksearch API
3. 用户手动绑定 repo URL
"""

import os
import asyncio
import logging
import aiohttp
from urllib.parse import urlparse
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger("noctyra.huggingface")

BASE_URL = "https://huggingface.co"
API_URL = f"{BASE_URL}/api"


# 代理解析统一到 proxy_util，避免与 civarchive / preview_cache 各自一份漂移
from .proxy_util import get_proxy as _get_proxy, get_proxy_url, make_connector


class HuggingFaceClient:
    """HuggingFace API 客户端"""

    def __init__(self, token: str = ""):
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_token: str = ""
        self._session_proxy = None

    async def _get_session(self) -> aiohttp.ClientSession:
        # token 或代理变更（用户在设置里改了）→ 重建 session：让 Authorization 头生效，
        # 且 connector 是 session 级（socks 代理尤其依赖它），代理一变必须重建
        cur_proxy = get_proxy_url()
        if (self._session is None or self._session.closed
                or self._session_token != self.token or self._session_proxy != cur_proxy):
            if self._session and not self._session.closed:
                try:
                    await self._session.close()
                except Exception:
                    pass
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60, connect=15),
                connector=make_connector(),
            )
            self._session_token = self.token
            self._session_proxy = cur_proxy
        return self._session

    def _auth_headers(self) -> Dict[str, str]:
        """显式返回 Authorization 头，供 per-request 使用（兜底，避免 session 缓存问题）"""
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _proxy(self):
        return _get_proxy()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def search_models(self, query: str, limit: int = 5) -> List[Dict]:
        """通过关键词搜索模型

        使用 quicksearch API 进行模糊搜索
        """
        try:
            session = await self._get_session()
            url = f"{API_URL}/quicksearch"
            params = {"q": query, "limit": limit}

            async with session.get(url, params=params, proxy=self._proxy()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # quicksearch 返回 {"models": [...], "datasets": [...], ...}
                    return data.get("models", [])
                else:
                    logger.warning("[Noctyra-MM] HF 搜索失败 %d", resp.status)
                    return []
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] HF 搜索请求失败: %s", e)
            return []

    async def get_model_info(self, repo_id: str) -> Optional[Dict]:
        """获取指定 repo 的模型信息

        Args:
            repo_id: 如 "stabilityai/stable-diffusion-xl-base-1.0"
        """
        try:
            session = await self._get_session()
            url = f"{API_URL}/models/{repo_id}"

            async with session.get(url, proxy=self._proxy()) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 404:
                    return None
                else:
                    logger.warning("[Noctyra-MM] HF 获取模型信息失败 %d", resp.status)
                    return None
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] HF 请求失败: %s", e)
            return None

    async def get_readme(self, repo_id: str, max_chars: int = 3000) -> str:
        """获取 repo 的 README.md 内容（截断到 max_chars）。受限/无权限时返回空串，由调用方兜底。

        使用 /resolve/main/README.md 端点——它是 HF 的规范下载入口，正确处理
        gated 仓库的鉴权。每次请求都显式带上 Authorization 头，避免 aiohttp
        session 级头缓存问题。
        """
        url = f"{BASE_URL}/{repo_id}/resolve/main/README.md"
        headers = self._auth_headers()
        token_state = "有 token" if self.token else "无 token"
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers, proxy=self._proxy(), allow_redirects=True) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if text.startswith("---"):
                        end = text.find("---", 3)
                        if end != -1:
                            text = text[end + 3:].strip()
                    return text[:max_chars] if len(text) > max_chars else text
                if resp.status in (401, 403):
                    if self.token:
                        logger.warning(
                            "[Noctyra-MM] HF README 受限 %d: %s（%s，请确认 HF 账户已接受协议且 token 对应同一账户、具 Read 权限）",
                            resp.status, repo_id, token_state,
                        )
                    else:
                        logger.info(
                            "[Noctyra-MM] HF README 受限 %d: %s（未配置 HF token，gated 仓库需要登录）",
                            resp.status, repo_id,
                        )
                elif resp.status != 404:
                    logger.warning("[Noctyra-MM] HF README HTTP %d: %s (%s)", resp.status, repo_id, token_state)
                return ""
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] HF 获取 README 失败: %s", e)
            return ""


    async def list_repo_files(self, repo_id: str) -> List[Dict]:
        """列出 repo 中的所有文件"""
        try:
            session = await self._get_session()
            url = f"{API_URL}/models/{repo_id}/tree/main"
            params = {"recursive": "true"}

            async with session.get(url, params=params, proxy=self._proxy()) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] HF 列出文件失败: %s", e)
            return []

    async def match_by_filename(self, filename: str, sha256: str = "") -> List[Dict]:
        """通过文件名（+ 可选 SHA256）搜索可能的来源 repo

        匹配级别（match_type 字段区分）：
        - ``hash``：LFS SHA256 精确比对命中，100% 可信
        - ``filename``：文件名子串命中
        - ``fuzzy``：仓库名归一化相似，量化变体兜底，低置信度

        策略：
        1. 原始文件名搜索（可能命中量化专用仓库）
        2. 清理后缀名搜索（兜底命中父仓库）
        3. 对每个候选：LFS SHA256 精确比对 > 文件名子串 > 仓库名模糊
        """
        name_no_ext = os.path.splitext(filename)[0]

        # 清理文件名中的常见后缀（量化/精度等），以便回退搜父仓库
        clean_name = name_no_ext
        suffixes = (
            "_fp16", "-fp16", "_fp32", "-fp32", "_fp8", "-fp8", "_fp8_e4m3fn", "-fp8_e4m3fn",
            "_bf16", "-bf16", "_pruned", "-pruned", "_ema", "-ema", "_nf4", "-nf4",
            "_q8", "-q8", "_q6", "-q6", "_q5", "-q5", "_q4", "-q4", "_q3", "-q3", "_q2", "-q2",
            "_q8_0", "-q8_0", "_q6_k", "-q6_k", "_q5_k_m", "-q5_k_m", "_q4_k_m", "-q4_k_m",
            "_gguf", "-gguf", "_safetensors", "-safetensors",
        )
        for suffix in sorted(suffixes, key=len, reverse=True):
            clean_name = clean_name.replace(suffix, "")
        clean_name = clean_name.strip("-_. ")

        def _norm(s: str) -> str:
            return "".join(c for c in s.lower() if c.isalnum())

        def _hex_only(s: str) -> str:
            """只保留合法十六进制字符，兼容 HF 可能附带的 'sha256:' 前缀"""
            return "".join(c for c in (s or "").lower() if c in "0123456789abcdef")

        def _file_sha256(f: Dict) -> str:
            """从文件条目提取 LFS SHA256（非 LFS 文件返回空）"""
            lfs = f.get("lfs") or {}
            return _hex_only(lfs.get("oid") or lfs.get("sha256") or "")

        sha_lower = _hex_only(sha256)

        async def _rank_candidates(query: str, ref_norm: str):
            """搜索并按 hash/filename/fuzzy 归类"""
            if not query:
                return [], [], []
            cands = await self.search_models(query, limit=10)
            # 只保留有 id 的候选，并**保持搜索返回的原始顺序** —— caller 只用
            # candidates[0]、优先级 hash>filename>fuzzy，顺序即选中依据，绝不能打乱。
            valid = [c for c in cands if c.get("id")]
            if not valid:
                return [], [], []

            # 并发拉取所有候选的文件树（原为串行，单模型兜底可能几十秒阻塞）。
            # Semaphore(4) 限流避免打爆 HF；gather 保序返回，与 valid 一一对应，
            # 分类仍严格按原候选顺序进行 —— 只并发 I/O，不改变命中的 repo。
            sem = asyncio.Semaphore(4)

            async def _fetch_files(rid: str) -> List[Dict]:
                async with sem:
                    return await self.list_repo_files(rid)

            files_by_candidate = await asyncio.gather(
                *(_fetch_files(c["id"]) for c in valid)
            )

            hashed, exacts, fuzzies = [], [], []
            for candidate, files in zip(valid, files_by_candidate):
                repo_id = candidate["id"]

                # 最高优先级：LFS SHA256 精确比对
                hash_file = None
                if sha_lower:
                    hash_file = next((f for f in files if _file_sha256(f) == sha_lower), None)
                if hash_file:
                    hashed.append({
                        "repo_id": repo_id,
                        "matched_file": hash_file.get("path", ""),
                        "file_size": hash_file.get("size", 0),
                        "match_type": "hash",
                    })
                    continue

                # 中优：文件名子串命中
                exact_file = next(
                    (f for f in files if filename.lower() in (f.get("path", "").lower())),
                    None,
                )
                if exact_file:
                    exacts.append({
                        "repo_id": repo_id,
                        "matched_file": exact_file.get("path", ""),
                        "file_size": exact_file.get("size", 0),
                        "match_type": "filename",
                    })
                    continue

                # 低优：仓库名归一化相似
                repo_name_norm = _norm(repo_id.split("/")[-1])
                if ref_norm and (ref_norm in repo_name_norm or repo_name_norm in ref_norm):
                    fuzzies.append({
                        "repo_id": repo_id,
                        "matched_file": "",
                        "file_size": 0,
                        "match_type": "fuzzy",
                    })
            return hashed, exacts, fuzzies

        # Phase 1: 原始文件名搜
        hashed1, exact1, fuzzy1 = await _rank_candidates(name_no_ext, _norm(name_no_ext))
        if hashed1:
            return hashed1
        if exact1:
            return exact1

        # Phase 2: 清理名搜（量化变体 → 父仓库场景）
        hashed2, exact2, fuzzy2 = [], [], []
        if clean_name and clean_name != name_no_ext:
            hashed2, exact2, fuzzy2 = await _rank_candidates(clean_name, _norm(clean_name))
            if hashed2:
                return hashed2
            if exact2:
                return exact2

        # 兜底：仅返回模糊匹配（低置信度）
        return fuzzy1 + fuzzy2

    async def get_repo_by_url(self, url: str) -> Optional[Dict]:
        """从 HuggingFace URL 提取 repo 信息（用户手动绑定）

        支持的 URL 格式:
        - https://huggingface.co/user/repo
        - https://huggingface.co/user/repo/blob/main/file.safetensors
        """
        # 提取 repo_id
        repo_id = self._parse_repo_id(url)
        if not repo_id:
            return None

        return await self.get_model_info(repo_id)

    @staticmethod
    def _parse_repo_id(url: str) -> Optional[str]:
        """从 URL 中提取 repo_id

        先校验 host 必须是 huggingface.co（或其子域）。否则例如粘进
        civitai.com/models/123 会被静默拼成畸形 repo_id，误导后续请求 404。
        """
        url = url.strip().rstrip("/")
        if not url:
            return None

        # 补 scheme 让 urlparse 能解析出 hostname（用户常粘无 scheme 的 huggingface.co/...）
        parse_target = url if "://" in url else f"https://{url}"
        host = (urlparse(parse_target).hostname or "").lower()
        # 前导点保证 evilhuggingface.co 之类不被误判为子域
        if host != "huggingface.co" and not host.endswith(".huggingface.co"):
            logger.warning("[Noctyra-MM] 非 HuggingFace URL，无法解析 repo: %s", url)
            return None

        # 去掉 base URL
        for prefix in (f"{BASE_URL}/", "huggingface.co/"):
            if url.startswith(prefix) or url.startswith(f"https://{prefix}"):
                url = url.split(prefix, 1)[-1]
                break

        # 取前两段作为 user/repo；两段都需非空且不含 '..'（防畸形/路径穿越）
        parts = url.split("/")
        if (len(parts) >= 2 and parts[0] and parts[1]
                and ".." not in parts[0] and ".." not in parts[1]):
            return f"{parts[0]}/{parts[1]}"
        return None

    # 不再从 tags / repo 名关键词推断 base_model：HF 没有可靠的 base 字段，关键词猜测易出错。
    # 与"只信权威 base 字段、宁可 Unknown 也不硬猜"的策略一致 —— 只匹配到 HF 的模型一律留 Unknown。

    @staticmethod
    def parse_model_info(model_data: Dict, repo_id: str = "") -> Dict:
        """从 API 返回数据中提取关键信息"""
        if not model_data:
            return {}

        repo_id = repo_id or model_data.get("id", "")
        tags = model_data.get("tags", [])

        # 只匹配到 HuggingFace 的模型不猜 base_model，一律 Unknown（HF 无可靠 base 字段）
        base_model = "Unknown"

        # 尝试获取 README 中的描述（cardData）
        card_data = model_data.get("cardData", {}) or {}
        description = card_data.get("description", "")

        return {
            "source": "huggingface",
            "repo_id": repo_id,
            "model_name": repo_id.split("/")[-1] if "/" in repo_id else repo_id,
            "model_description": description,
            "base_model": base_model,
            "tags": tags,
            "source_url": f"{BASE_URL}/{repo_id}",
            "downloads": model_data.get("downloads", 0),
            "likes": model_data.get("likes", 0),
            "author": model_data.get("author", ""),
            "last_modified": model_data.get("lastModified", ""),
        }
