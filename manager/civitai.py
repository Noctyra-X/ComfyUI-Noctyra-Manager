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
CivitAI API 客户端

通过 SHA256 哈希匹配本地模型到 CivitAI 上的模型信息。
支持：哈希查询、模型/版本详情、文件下载（含断点续传）。
API 文档: https://civitai.com/api/v1
"""

import asyncio
import logging
import os
import time
import aiohttp
from typing import Optional, Dict, Tuple

logger = logging.getLogger("noctyra.civitai")

# API 始终走 civitai.com：.com / .red / .green 三站共用同一后端数据库
BASE_URL = "https://civitai.com/api/v1"

# CivitAI 前门域名集合（判断 URL 属于 CivitAI 域时使用）
_CIVITAI_HOSTS = ("civitai.com", "civitai.red", "civitai.green")
# 允许作为 source 链接域名的白名单（与 config.civitai_source_host 校验）
_ALLOWED_SOURCE_HOSTS = ("civitai.com", "civitai.red", "civitai.green")


def is_civitai_host(url: str) -> bool:
    """判断 URL 是否指向任何一个 CivitAI 前门站（.com / .red / .green）。

    用主机名**边界**匹配，而非子串：否则 `civitai.com.evil.net` / `xcivitai.com`
    会被误判为 CivitAI，导致 download_file 把 API key 发给伪造域名（凭据泄露）。
    """
    if not url:
        return False
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, TypeError):
        return False
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in _CIVITAI_HOSTS)


def get_source_host() -> str:
    """读取 config.civitai_source_host，校验后返回；非法值回退到 civitai.com"""
    try:
        from .config import get_config
        host = (get_config().get("civitai_source_host") or "civitai.com").strip()
    except Exception:
        host = "civitai.com"
    if host not in _ALLOWED_SOURCE_HOSTS:
        host = "civitai.com"
    return host


def build_model_url(model_id, version_id=None) -> str:
    """构造 `https://<host>/models/{model_id}[?modelVersionId={version_id}]`；host 来自设置"""
    if not model_id:
        return ""
    host = get_source_host()
    url = f"https://{host}/models/{model_id}"
    if version_id:
        url += f"?modelVersionId={version_id}"
    return url


def build_image_url(image_id) -> str:
    """构造 `https://<host>/images/{image_id}`；host 来自设置"""
    if not image_id:
        return ""
    return f"https://{get_source_host()}/images/{image_id}"


def _silent_remove(path: str):
    """尝试删除文件，失败时静默忽略"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.debug("[Noctyra-MM] 清理 tmp 文件失败 %s: %s", path, e)


def _get_proxy(config=None) -> Optional[str]:
    """代理地址（给 aiohttp 的 proxy= 用）：统一走 proxy_util（含 type/认证；socks 返回 None，
    由 session 的 ProxyConnector 处理）。config 参数保留兼容旧签名，proxy_util 本就 config 优先。"""
    from .proxy_util import get_proxy
    return get_proxy()


class _PermanentDownloadError(Exception):
    """下载遇到不可重试的错误（401/403/404/410），上层应直接放弃而非退避重试。"""


class CivitaiClient:
    """CivitAI API 客户端"""

    # 全局限流冷却窗口：收到一次 429 后，该窗口内所有请求直接返回 rate_limited（不发 HTTP）。
    # 冷却时长优先用 CivitAI 给的 Retry-After（它常只让等 60s），无则用默认；上层匹配会
    # 等这段冷却再续跑，而不是放弃整批。
    _RATE_LIMIT_DEFAULT_SEC = 60   # 无 Retry-After 时的冷却
    _RATE_LIMIT_MAX_SEC = 300      # 冷却上限（防 Retry-After 给超大值）

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_proxy = None
        self._rate_limit_until: float = 0.0

    def is_rate_limited(self) -> bool:
        """是否处于全局限流冷却窗口内"""
        # 用 monotonic：墙钟（time.time）被 NTP/手动改时会误判冷却窗
        return time.monotonic() < self._rate_limit_until

    def cooldown_remaining(self) -> float:
        """距限流冷却结束还有多少秒（供上层 sleep 后续跑）。"""
        return max(0.0, self._rate_limit_until - time.monotonic())

    @staticmethod
    def _parse_retry_after(value) -> Optional[int]:
        """解析 Retry-After 头：优先「秒数」形式；否则按「HTTP-date」解析（Cloudflare/CivitAI
        限流时常用日期形式），返回距现在的秒数。无法解析返回 None。
        修复前只认秒数 → 日期形式解析失败退回默认 60s，可能「等60s→又429」循环。"""
        if not value:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            pass
        try:
            import datetime
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(str(value).strip())
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            delta = (dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            return int(delta) if delta > 0 else None
        except Exception:
            return None

    def _enter_rate_limit_cooldown(self, retry_after=None):
        """收到 429 时进入全局冷却，时长优先用 Retry-After（夹在 [1, MAX] 内）。"""
        was_cold = self.is_rate_limited()
        secs = self._RATE_LIMIT_DEFAULT_SEC
        if retry_after is not None:
            secs = max(1, min(int(retry_after), self._RATE_LIMIT_MAX_SEC))
        self._rate_limit_until = time.monotonic() + secs
        if not was_cold:
            logger.warning("[Noctyra-MM] CivitAI 限流冷却 %d 秒（期间请求跳过，冷却后自动续跑）", secs)

    async def _get_session(self) -> aiohttp.ClientSession:
        from .proxy_util import get_proxy_url, make_connector
        cur_proxy = get_proxy_url()
        # 代理变更 → 重建 session（connector 是 session 级，socks 尤其依赖它）
        if (self._session is not None and not self._session.closed
                and self._session_proxy != cur_proxy):
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60, connect=15),
                connector=make_connector(),
            )
            self._session_proxy = cur_proxy
        return self._session

    def _proxy(self):
        return _get_proxy(config=True)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_model_by_hash(self, sha256: str) -> Tuple[Optional[Dict], Optional[str]]:
        """通过 SHA256 查询模型信息

        Returns:
            (model_version_data, error_message)
        """
        sha_short = sha256[:10] if sha256 else ""
        if self.is_rate_limited():
            return None, "rate_limited"
        try:
            session = await self._get_session()
            url = f"{BASE_URL}/model-versions/by-hash/{sha256}"
            logger.debug("[Noctyra-MM] CivitAI 哈希查询: %s...", sha_short)

            async with session.get(url, proxy=self._proxy()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug("[Noctyra-MM] CivitAI 命中: %s → %s",
                                 sha_short, data.get("model", {}).get("name", "?"))
                    return data, None
                elif resp.status == 404:
                    logger.debug("[Noctyra-MM] CivitAI 无此哈希: %s", sha_short)
                    return None, None  # 没找到不算错误
                elif resp.status == 429:
                    logger.warning("[Noctyra-MM] CivitAI 限流 (429): %s", sha_short)
                    self._enter_rate_limit_cooldown(self._parse_retry_after(resp.headers.get("Retry-After")))
                    return None, "rate_limited"
                else:
                    text = await resp.text()
                    logger.warning("[Noctyra-MM] CivitAI API 错误 %d (%s): %s",
                                   resp.status, sha_short, text[:200])
                    return None, f"HTTP {resp.status}"
        except asyncio.TimeoutError:
            logger.warning("[Noctyra-MM] CivitAI 查询超时: %s", sha_short)
            return None, "timeout"
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] CivitAI 网络错误 (%s): %s", sha_short, e)
            return None, f"network: {e}"

    async def get_model_info(self, model_id: int) -> Optional[Dict]:
        """获取模型完整信息（所有版本）"""
        if self.is_rate_limited():
            return None
        try:
            session = await self._get_session()
            url = f"{BASE_URL}/models/{model_id}"

            async with session.get(url, proxy=self._proxy()) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    self._enter_rate_limit_cooldown(self._parse_retry_after(resp.headers.get("Retry-After")))
                    return None
                body = await resp.text()
                logger.warning("[Noctyra-MM] CivitAI get_model_info(%s) HTTP %s: %s", model_id, resp.status, body[:200])
                return None
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] CivitAI 获取模型信息失败: %s", e)
            return None

    async def get_model_version(self, version_id: int) -> Optional[Dict]:
        """获取特定版本信息"""
        if self.is_rate_limited():
            return None
        try:
            session = await self._get_session()
            url = f"{BASE_URL}/model-versions/{version_id}"

            async with session.get(url, proxy=self._proxy()) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    self._enter_rate_limit_cooldown(self._parse_retry_after(resp.headers.get("Retry-After")))
                else:
                    logger.warning("[Noctyra-MM] CivitAI 获取版本 %s 失败: HTTP %s",
                                   version_id, resp.status)
                return None
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] CivitAI 获取版本信息失败: %s", e)
            return None

    async def get_model_versions(self, model_id: int) -> Optional[list]:
        """获取模型的所有版本列表"""
        data = await self.get_model_info(model_id)
        if data:
            return data.get("modelVersions", [])
        return None

    async def get_image_info(self, image_id: int) -> Optional[Dict]:
        """获取 CivitAI 图片详情（含生成参数 meta、资源列表 resources）

        CivitAI API 的 /images 端点默认会过滤 NSFW 图片（只返回 SFW），
        传 `nsfw=X` 覆盖所有等级（None / Soft / Mature / X），否则 NSFW 图片
        （civitai.red 上大量这类）会查无结果。
        """
        if self.is_rate_limited():
            return None
        try:
            session = await self._get_session()
            url = f"{BASE_URL}/images"
            params = {"imageId": image_id, "nsfw": "X"}
            async with session.get(url, params=params, proxy=self._proxy()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    return items[0] if items else None
                if resp.status == 429:
                    self._enter_rate_limit_cooldown(self._parse_retry_after(resp.headers.get("Retry-After")))
                else:
                    logger.warning("[Noctyra-MM] CivitAI get_image_info(%s) HTTP %s", image_id, resp.status)
                return None
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] CivitAI 获取图片信息失败: %s", e)
            return None

    async def get_models_bulk(self, model_ids) -> Optional[dict]:
        """批量获取多个 model 信息：GET /models?ids=a,b,c（逗号分隔，100/批，格式同 LoRA Manager）。
        给大库的"检查更新"用，把 N 次请求压成 N/100 次。
        返回 {model_id(int): model_data}；整体限流/网络失败返回 None（上层回退逐个查）。"""
        if self.is_rate_limited():
            return None
        seen = set()
        ids = []
        for raw in model_ids:
            try:
                mid = int(raw)
            except (TypeError, ValueError):
                continue
            if mid not in seen:
                seen.add(mid)
                ids.append(mid)
        if not ids:
            return {}
        result = {}
        try:
            session = await self._get_session()
            for i in range(0, len(ids), 100):
                batch = ids[i:i + 100]
                async with session.get(
                    f"{BASE_URL}/models",
                    params={"ids": ",".join(str(b) for b in batch)},
                    proxy=self._proxy(),
                ) as resp:
                    if resp.status == 429:
                        self._enter_rate_limit_cooldown(self._parse_retry_after(resp.headers.get("Retry-After")))
                        return None
                    if resp.status != 200:
                        logger.warning("[Noctyra-MM] CivitAI 批量 /models HTTP %s", resp.status)
                        continue
                    data = await resp.json()
                    for item in (data.get("items") or []):
                        mid = item.get("id")
                        if mid is None:
                            continue
                        try:
                            result[int(mid)] = item
                        except (TypeError, ValueError):
                            pass
                await asyncio.sleep(0.5)
            return result
        except aiohttp.ClientError as e:
            logger.error("[Noctyra-MM] CivitAI 批量获取模型失败: %s", e)
            return None

    # 下载重试：网络抖动/5xx/不完整时退避重试（.tmp 续传），永久错误不重试
    _DOWNLOAD_MAX_RETRIES = 5

    async def download_file(self, url: str, save_path: str,
                            progress_callback=None) -> bool:
        """下载文件到指定路径，支持断点续传、进度回调和网络层自动重试（指数退避）。

        支持 CivitAI 和 HuggingFace 等直链；非 CivitAI URL 使用独立 session 避免带上 API key。
        """
        # SSRF/协议校验：download_url 可能来自前端/扩展，拒绝 file:// 与内网/回环
        from .preview_cache import _is_safe_external_url
        if not await _is_safe_external_url(url):
            logger.warning("[Noctyra-MM] 拒绝不安全的下载 URL（仅允许 http(s) 外部地址）: %s", url)
            return False
        tmp_path = save_path + ".tmp"
        is_civitai = is_civitai_host(url)
        start_time = time.time()
        logger.info("[Noctyra-MM] 开始下载: %s -> %s", url, save_path)

        for attempt in range(self._DOWNLOAD_MAX_RETRIES + 1):
            try:
                if await self._download_attempt(url, save_path, tmp_path, is_civitai,
                                                progress_callback, start_time):
                    return True
                # 返回 False → 可重试（5xx / 不完整等），下面退避后续传重试
            except asyncio.CancelledError:
                logger.info("[Noctyra-MM] 下载取消: %s（tmp 已保留，下次可续传）", save_path)
                raise
            except _PermanentDownloadError:
                return False  # 401/403/404/410 等永久错误，不重试
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("[Noctyra-MM] 下载网络错误（尝试 %d/%d）: %s",
                               attempt + 1, self._DOWNLOAD_MAX_RETRIES + 1, e)
            except Exception as e:
                logger.error("[Noctyra-MM] 下载异常: %s", e, exc_info=True)
                try:
                    if os.path.exists(tmp_path) and os.path.getsize(tmp_path) == 0:
                        _silent_remove(tmp_path)
                except OSError:
                    pass
                return False  # 未知异常不盲目重试
            if attempt < self._DOWNLOAD_MAX_RETRIES:
                delay = 2 * (2 ** attempt)  # 2,4,8,16,32 秒
                logger.info("[Noctyra-MM] %ds 后重试下载（自动续传）...", delay)
                await asyncio.sleep(delay)

        logger.error("[Noctyra-MM] 下载重试 %d 次后仍失败: %s", self._DOWNLOAD_MAX_RETRIES, url)
        return False

    async def _download_attempt(self, url: str, save_path: str, tmp_path: str,
                                is_civitai: bool, progress_callback, start_time: float) -> bool:
        """单次下载尝试。成功 True；5xx/不完整等可重试情况返回 False；
        401/403/404/410 永久错误抛 _PermanentDownloadError（上层不重试）。
        网络异常向上抛由 download_file 的退避循环处理。"""
        headers = {}
        if self.api_key and is_civitai:
            headers["Authorization"] = f"Bearer {self.api_key}"

        resume_offset = 0
        if os.path.exists(tmp_path):
            resume_offset = os.path.getsize(tmp_path)
            if resume_offset > 0:
                headers["Range"] = f"bytes={resume_offset}-"
                logger.info("[Noctyra-MM] 断点续传: 从 %d 字节继续下载", resume_offset)

        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=300)
        if is_civitai:
            session = await self._get_session()
            ephemeral = None
        else:
            # 为非 CivitAI 链接创建独立 session，避免带上 CivitAI API key
            from .proxy_util import make_connector
            ephemeral = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60, connect=15),
                connector=make_connector(),
            )
            session = ephemeral
        try:
            async with session.get(url, proxy=self._proxy(), headers=headers,
                                   timeout=timeout, allow_redirects=True) as resp:
                if resp.status == 416:
                    content_range = resp.headers.get("Content-Range", "")
                    if content_range:
                        try:
                            total_size = int(content_range.split("/")[-1])
                            if resume_offset >= total_size:
                                os.replace(tmp_path, save_path)
                                logger.info("[Noctyra-MM] 文件已完整（续传确认）: %s", save_path)
                                return True
                        except (ValueError, IndexError):
                            pass
                    # 416 说明服务端认为 range 无效，且文件大小不匹配 → tmp 已损坏
                    _silent_remove(tmp_path)
                    headers.pop("Range", None)
                    async with session.get(url, proxy=self._proxy(), headers=headers,
                                           timeout=timeout, allow_redirects=True) as retry_resp:
                        return await self._stream_download(
                            retry_resp, tmp_path, save_path, 0, progress_callback, start_time
                        )

                if resp.status == 200 and resume_offset > 0:
                    logger.info("[Noctyra-MM] 服务器不支持续传，重新下载")
                    _silent_remove(tmp_path)
                    resume_offset = 0

                if resp.status not in (200, 206):
                    logger.error("[Noctyra-MM] 下载失败 HTTP %d: %s", resp.status, url)
                    if resp.status in (401, 403, 404, 410):
                        _silent_remove(tmp_path)
                        raise _PermanentDownloadError()
                    return False  # 其它（5xx 等）可重试

                return await self._stream_download(
                    resp, tmp_path, save_path, resume_offset, progress_callback, start_time
                )
        finally:
            if ephemeral is not None:
                await ephemeral.close()

    async def _stream_download(self, resp, tmp_path: str, save_path: str,
                                resume_offset: int, progress_callback, start_time: float = None) -> bool:
        """流式写入下载内容"""
        # SSRF：初始 URL 在 download_file 已校验，但 allow_redirects=True 会跟随跳转。
        # 这里复核「重定向后的最终地址 + 每一跳」，防 download_url 被 302 到 127.0.0.1/内网/
        # 云元数据端点把响应当模型文件写盘（两条下载路径都经过本方法，一处覆盖）。
        from .preview_cache import _is_safe_external_url
        for hop in list(getattr(resp, "history", []) or []) + [resp]:
            if not await _is_safe_external_url(str(hop.url)):
                logger.warning("[Noctyra-MM] 下载重定向到不安全地址，已中止: %s", hop.url)
                raise _PermanentDownloadError()
        total = int(resp.headers.get("Content-Length", 0))
        if resp.status == 206:
            content_range = resp.headers.get("Content-Range", "")
            if content_range:
                try:
                    total = int(content_range.split("/")[-1])
                except (ValueError, IndexError):
                    total = resume_offset + total
        else:
            resume_offset = 0

        downloaded = resume_offset
        mode = "ab" if resume_offset > 0 else "wb"
        # 每 10% 进度打一条日志，避免刷屏
        next_log_pct = 10 if total > 0 else None

        logger.info(
            "[Noctyra-MM] 下载流开始: 总大小 %s bytes, 续传偏移 %d",
            total or "unknown", resume_offset,
        )

        loop = asyncio.get_running_loop()
        with open(tmp_path, mode) as f:
            async for chunk in resp.content.iter_chunked(1024 * 1024):
                # 写盘卸载到默认线程池（不占用扫描池），别让同步 f.write 阻塞事件循环 →
                # 下载大文件时列表/预览/出图进度不掉帧。顺序 await 保持写入有序。
                await loop.run_in_executor(None, f.write, chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    await progress_callback(downloaded, total)
                if next_log_pct is not None:
                    pct = (downloaded * 100) // total
                    if pct >= next_log_pct:
                        logger.info("[Noctyra-MM] 下载进度: %d%% (%d/%d bytes)", pct, downloaded, total)
                        next_log_pct = ((pct // 10) + 1) * 10

        if total > 0 and downloaded < total:
            logger.error("[Noctyra-MM] 下载不完整: %d/%d bytes", downloaded, total)
            return False

        os.replace(tmp_path, save_path)
        elapsed = (time.time() - start_time) if start_time else 0
        speed_mbs = (downloaded / 1024 / 1024 / elapsed) if elapsed > 0 else 0
        logger.info(
            "[Noctyra-MM] 下载完成: %s (%d bytes, %.2fs, %.2f MB/s)",
            save_path, downloaded, elapsed, speed_mbs,
        )
        return True

    @staticmethod
    def parse_version_info(version_data: Dict) -> Dict:
        """从 API 返回数据中提取关键信息"""
        if not version_data:
            return {}

        model_info = version_data.get("model", {})

        # 提取所有预览图（含元数据和 NSFW 等级）
        raw_images = version_data.get("images", [])
        preview_images = []
        preview_url = ""
        for img in raw_images:
            url = img.get("url", "")
            if not url:
                continue
            image_entry = {
                "url": url,
                "type": img.get("type", "image"),
                "width": img.get("width"),
                "height": img.get("height"),
                "nsfw_level": img.get("nsfwLevel", 0),
            }
            # 提取生成参数
            meta = img.get("meta") or {}
            if meta:
                image_entry["prompt"] = meta.get("prompt", "")
                image_entry["negative_prompt"] = meta.get("negativePrompt", "")
                image_entry["sampler"] = meta.get("sampler", "")
                image_entry["steps"] = meta.get("steps")
                image_entry["cfg_scale"] = meta.get("cfgScale")
                image_entry["seed"] = meta.get("seed")
                image_entry["model"] = meta.get("Model", "")
            preview_images.append(image_entry)

        if preview_images:
            first_img = next((p for p in preview_images if p.get("type") != "video"), None)
            preview_url = (first_img or preview_images[0])["url"]

        # 提取触发词
        trained_words = version_data.get("trainedWords", [])

        # 提取下载信息
        files = version_data.get("files", [])
        download_url = ""
        file_size_kb = 0
        if files:
            download_url = files[0].get("downloadUrl", "")
            file_size_kb = files[0].get("sizeKB", 0)

        # 基础模型：严格用 CivitAI 的 baseModel 原值（学 Lora-Manager，只归一规范名、不再用
        # 文件名/标签/safetensors 细化——那些会误判，作者填得粗就显示粗，宁可粗也不猜）
        from .base_models import normalize_base_model
        base_model = normalize_base_model(version_data.get("baseModel") or "Unknown")

        # 统计信息
        stats = version_data.get("stats", {})

        creator_obj = version_data.get("creator") or {}

        # NSFW 识别 —— 只用 model / version 自身字段，不看预览图：
        #   1. 嵌套 model.nsfw bool（by-hash 端点经常给 False 即便是 NSFW 模型）
        #   2. version-level nsfwLevel（位图整数，更可靠）
        #   3. enrich 阶段还会用 model-level nsfwLevel 再修一遍
        is_nsfw = bool(model_info.get("nsfw", False))
        v_level = version_data.get("nsfwLevel")
        if isinstance(v_level, int) and v_level > 1:
            is_nsfw = True

        return {
            "source": "civitai",
            "civitai_model_id": version_data.get("modelId"),
            "civitai_version_id": version_data.get("id"),
            "model_name": model_info.get("name", ""),
            "version_name": version_data.get("name", ""),
            "model_description": model_info.get("description", ""),
            "civitai_model_type": model_info.get("type", ""),
            "base_model": base_model,
            "trained_words": trained_words,
            "preview_url": preview_url,
            "preview_images": preview_images,
            "download_url": download_url,
            "source_url": build_model_url(version_data.get("modelId"), version_data.get("id")),
            "tags": model_info.get("tags", []),
            "creator": creator_obj.get("username", ""),
            "creator_avatar": creator_obj.get("image", ""),
            "nsfw": is_nsfw,
            "published_at": version_data.get("publishedAt", ""),
            # 额外信息（version 级统计，后续可被 model 级覆盖）
            "downloads": stats.get("downloadCount", 0),
            "rating": stats.get("rating", 0),
            "rating_count": stats.get("ratingCount", 0),
            "thumbs_up": stats.get("thumbsUpCount", 0),
        }

    @staticmethod
    def enrich_with_model_info(info: Dict, model_data: Dict) -> Dict:
        """用完整 model 信息补全 version 级解析结果中缺失的字段"""
        if not model_data:
            return info

        creator = model_data.get("creator") or {}
        stats = model_data.get("stats") or {}

        if not info.get("model_description"):
            info["model_description"] = model_data.get("description", "")
        if not info.get("creator"):
            info["creator"] = creator.get("username", "")
        if not info.get("creator_avatar"):
            info["creator_avatar"] = creator.get("image", "")
        model_tags = model_data.get("tags") or []
        if model_tags:
            info["tags"] = model_tags

        # NSFW：只向上升级，不往下降。version 级或 model 级任何字段显示
        # 是 NSFW 都保留成 True。CivitAI 有两种表达：nsfw (bool) 和
        # nsfwLevel (int 位图，1=None/2=Soft/4=Mature/8=X/16=XXX)
        if model_data.get("nsfw"):
            info["nsfw"] = True
        model_nsfw_level = model_data.get("nsfwLevel")
        if isinstance(model_nsfw_level, int) and model_nsfw_level > 1:
            info["nsfw"] = True

        if stats.get("downloadCount", 0) > info.get("downloads", 0):
            info["downloads"] = stats["downloadCount"]
        if stats.get("thumbsUpCount", 0) > info.get("thumbs_up", 0):
            info["thumbs_up"] = stats["thumbsUpCount"]
        info["comment_count"] = stats.get("commentCount", 0)

        # License：CivitAI 的 allowCommercialUse 是列表，含 "Sell" 表示可商用
        # 映射：1=允许商用, 0=仅个人, -1=未知
        if "allowCommercialUse" in model_data:
            commercial_list = model_data.get("allowCommercialUse") or []
            if isinstance(commercial_list, list):
                info["civitai_allow_commercial"] = 1 if any(
                    str(x).lower() in ("sell", "rentcivit", "rent")
                    for x in commercial_list
                ) else 0
            else:
                info["civitai_allow_commercial"] = -1

        return info
