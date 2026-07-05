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
预览图本地缓存

将远程预览图下载到本地 .cache/previews/ 目录，
通过 /api/noctyra/preview 代理访问，避免前端直接请求外部 URL。
文件名使用 URL 的 SHA256 去重，保存原始字节（不做格式转换，保留 PNG 内嵌的工作流数据）。
"""

import asyncio
import hashlib
import ipaddress
import logging
import os
import shutil
import threading
import time
import uuid
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp

logger = logging.getLogger("noctyra.preview_cache")


async def _is_safe_external_url(url: str) -> bool:
    """SSRF 防御（轻量版，兼容 Clash fake-ip / TUN 模式）。

    检查策略：
      1. scheme 必须是 http / https（拒绝 file:// / ftp:// / data: 等）
      2. hostname 字符串黑名单：localhost / *.local / *.internal
      3. 如果 hostname 是 IP 字面值，检查是否私网/回环 → 拒绝
      4. 如果 hostname 是域名，不做 DNS 解析（避开 Clash fake-ip 把所有代理域名
         解析为 198.18.x.x 保留段，Python 把这个段判为 is_private 导致全部误拦）

    残留风险：DNS rebinding 攻击（攻击者注册公网域名并解析到 127.0.0.1）。
    对本地 ComfyUI 插件场景威胁极低 —— 利用需要攻击者控制公网域名 + 诱导
    特定 URL 进入 preview cache + 响应不会回给攻击者。
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    # 域名字面值黑名单
    if host == "localhost" or host.endswith(".localhost"):
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    # IP 字面值才检查私网（域名跳过 DNS 解析）
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # 不是 IP 字面值（是域名）→ 通过
    # IPv4-mapped IPv6（如 ::ffff:127.0.0.1 / ::ffff:10.0.0.1）的 is_loopback/is_private
    # 不生效，取出映射的 IPv4 再判，堵掉这个绕过
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified or ip.is_reserved):
        return False
    return True


class PreviewCache:
    """预览图缓存管理器"""

    # 后台预热最大并发；太高会被 CivitAI CDN 限流
    _PREWARM_CONCURRENCY = 4

    def __init__(self, cache_dir: str):
        self._cache_dir = os.path.join(cache_dir, "previews")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._thumb_dir = os.path.join(cache_dir, "thumbs")  # 列表卡片缩略图，惰性建目录
        self._downloading: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        # 后台预热队列：匹配时扔 URL 进来，worker 慢慢消费
        # 用 set + Event 去重并唤醒，避免同一 URL 重复入队
        self._prewarm_pending: set[str] = set()
        self._prewarm_signal = asyncio.Event()
        # 永久失败的 URL（404 之类）记住，避免反复重试
        self._prewarm_dead: set[str] = set()
        self._prewarm_worker: Optional[asyncio.Task] = None
        # 撞 429 后的全局限流冷却截止时刻（monotonic 秒）：窗口内的下载直接跳过，
        # 避免真并发继续猛打 CDN；被跳过的图下次 prewarm 会自动重试（磁盘未命中）
        self._rate_limited_until = 0.0
        # 已缓存 key 快照（短 TTL）：批量算预览状态时用，把"每 URL 8 次磁盘 stat"
        # 换成"一次 listdir + 内存查"，避免列表分页时几千次同步 stat 阻塞事件循环
        self._keys_snapshot: Optional[set] = None
        self._keys_snapshot_ts = 0.0
        # 缓存统计结果（短 TTL）：2 万+文件全盘 scandir 约 0.8s，设置页轮询/重复请求时复用，
        # 避免频繁重扫。文件增删由 clear_thumbs / cleanup_orphaned 主动置失效。
        self._cache_stats_cache: Optional[dict] = None
        self._cache_stats_ts = 0.0
        # 缩略图 per-source 串行锁:同图并发请求只生成一次,其余等待复用(在线程池里跑,用 threading.Lock)
        self._thumb_locks: dict = {}
        self._thumb_locks_guard = threading.Lock()
        # ---- 后台预热进度（给前端任务中心展示）----
        self._prewarm_total = 0       # 本轮累计入队数
        self._prewarm_done = 0        # 已成功下载
        self._prewarm_failed = 0      # 失败数
        self._prewarm_failures: list = []     # 最近失败 [{url, reason}]，留最近 ~60 条
        self._prewarm_fail_reason: dict = {}  # url -> 最近失败原因（_download 写，loop 取）
        self._prewarm_started_at = 0.0        # 本轮开始时刻(monotonic),给 ETA 算速度
        self._prewarm_cancelled = False       # 用户取消标志:在途批次尽快跳过、不再起新批

    @staticmethod
    def url_to_key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    @staticmethod
    def _parse_retry_after(value, default: int = 10) -> int:
        """解析 Retry-After 头（秒）。非数字（HTTP-date 格式）用默认值。夹紧到 [1, 120]。"""
        try:
            return max(1, min(int(float(value)), 120))
        except (TypeError, ValueError):
            return default

    _SUPPORTED_EXT = (".webp", ".jpg", ".jpeg", ".png", ".gif", ".avif",
                      ".mp4", ".webm")

    def _ext_from_url(self, url: str) -> str:
        path = url.split("?")[0].split("#")[0]
        for ext in self._SUPPORTED_EXT:
            if path.lower().endswith(ext):
                return ext
        return ".jpg"

    def get_cached_path(self, url: str) -> Optional[str]:
        key = self.url_to_key(url)
        for ext in self._SUPPORTED_EXT:
            path = os.path.join(self._cache_dir, f"{key}{ext}")
            if os.path.isfile(path):
                return path
        return None

    # 列表卡片缩略图：480px WebP，原图照存（保留 PNG 内嵌 workflow）。视频/动图不缩略。
    _THUMB_WIDTH = 480
    _THUMB_SKIP_EXT = (".gif", ".avif")          # 动图：抽首帧意义不大，回退原图
    _VIDEO_EXT = (".mp4", ".webm", ".mov")       # 视频：抽一帧当静态封面缩略图（避免卡片加载整段视频解码）

    def _thumb_path_for(self, source_path: str) -> str:
        key = hashlib.sha256(source_path.encode("utf-8", "surrogatepass")).hexdigest()
        return os.path.join(self._thumb_dir, key + ".webp")

    def _remove_thumb(self, source_path: str):
        try:
            tp = self._thumb_path_for(source_path)
            if os.path.isfile(tp):
                os.remove(tp)
        except OSError:
            pass

    def get_thumb(self, source_path: str) -> Optional[str]:
        """给一个本地图片文件（缓存的预览图或图库本地图），返回 480px WebP 缩略图路径，
        惰性生成并缓存到 .cache/thumbs/。视频/动图/生成失败返回 None（调用方回退原图）。

        CPU 密集（解码+缩放+编码），应在 executor 里调用，勿阻塞事件循环。
        """
        if not source_path or not os.path.isfile(source_path):
            return None
        ext = os.path.splitext(source_path)[1].lower()
        if ext in self._THUMB_SKIP_EXT:
            return None
        try:
            src_mtime = os.path.getmtime(source_path)
        except OSError:
            return None
        thumb_path = self._thumb_path_for(source_path)
        # 命中且不比源旧 → 直接复用
        try:
            if os.path.isfile(thumb_path) and os.path.getmtime(thumb_path) >= src_mtime:
                return thumb_path
        except OSError:
            pass
        # per-source 串行:同一张原图被多个卡片并发请求时只生成一次,其余等待复用,避免
        # 线程池(默认 4 worker)里对同图重复解码/缩放/编码。同步代码,用 threading.Lock。
        with self._thumb_locks_guard:
            if len(self._thumb_locks) > 512:
                self._thumb_locks.clear()   # 防无界增长;清掉最坏只是偶发一次重复生成
            lock = self._thumb_locks.setdefault(thumb_path, threading.Lock())
        with lock:
            # 二次检查:等锁期间可能已被另一线程生成好了
            try:
                if os.path.isfile(thumb_path) and os.path.getmtime(thumb_path) >= src_mtime:
                    return thumb_path
            except OSError:
                pass
            tmp = None
            try:
                from PIL import Image
                os.makedirs(self._thumb_dir, exist_ok=True)
                if ext in self._VIDEO_EXT:
                    im = self._grab_video_frame(source_path)
                    if im is None:
                        return None
                else:
                    im = Image.open(source_path)
                with im:
                    # 动图（多帧 webp/png/apng）缩略只会取首帧，干脆回退原图
                    if getattr(im, "is_animated", False):
                        return None
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGB")
                    w, h = im.size
                    if w > self._THUMB_WIDTH:
                        # thumbnail 原地缩放 + reducing_gap 分级预缩，比 resize+LANCZOS 快很多；
                        # 高度传原始 h（非约束项）等价"只按宽 480 收窄"、保持纵横比。
                        # 注意：thumbnail 就地修改并返回 None，勿写回 im=；也别用 Image.draft()（仅 JPEG 有效）
                        im.thumbnail((self._THUMB_WIDTH, h), Image.LANCZOS, reducing_gap=2.0)
                    tmp = f"{thumb_path}.{uuid.uuid4().hex[:8]}.tmp"
                    # WEBP method 从 4 降到 2：压缩耗时大减，体积略增、quality 保持 82
                    im.save(tmp, "WEBP", quality=82, method=2)
                os.replace(tmp, thumb_path)
                return thumb_path
            except Exception as e:
                logger.debug("[Noctyra-MM] 缩略图生成失败 %s: %s", source_path, e)
                if tmp:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                return None

    @staticmethod
    def _grab_video_frame(video_path: str):
        """用 OpenCV 抽视频一帧（约 10% 处，避开开头黑屏/转场）当封面，返回 PIL.Image；失败 None。
        在 get_thumb 的线程池里调用，cv2 同步解码不阻塞事件循环。"""
        try:
            import cv2
            from PIL import Image
        except Exception:
            return None
        cap = cv2.VideoCapture(video_path)
        try:
            if not cap.isOpened():
                return None
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            target = min(max(total // 10, 0), 30)
            if target > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = cap.read()
            if (not ok or frame is None) and target > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # 回退首帧
                ok, frame = cap.read()
            if not ok or frame is None:
                return None
            return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            cap.release()

    async def ensure_cached(self, url: str, foreground: bool = False) -> Optional[str]:
        """确保图片已缓存，返回本地路径。并发安全。
        foreground=True：前台 /preview 请求，慢则快速放弃（见 _download），避免占着浏览器连接。"""
        cached = self.get_cached_path(url)
        if cached:
            return cached

        key = self.url_to_key(url)

        async with self._lock:
            # 再次检查，可能在等锁期间已被下载
            cached = self.get_cached_path(url)
            if cached:
                return cached

            # is_leader 区分"我是第一个到这里的"还是"有人已经在下了"。
            # 不能靠 event.is_set()：第一个协程还没下完时 event 未 set，
            # 后来者判 `not event.is_set()` 会再次进入下载分支造成并发写。
            if key in self._downloading:
                event = self._downloading[key]
                is_leader = False
            else:
                event = asyncio.Event()
                self._downloading[key] = event
                is_leader = True

        if is_leader:
            try:
                path = await self._download(url, key, foreground=foreground)
                return path
            finally:
                event.set()
                async with self._lock:
                    self._downloading.pop(key, None)
        else:
            # 已有人在下这张：前台请求不死等（可能跟到一个后台慢下载），限时等，超时让卡片先占位
            if foreground:
                try:
                    await asyncio.wait_for(event.wait(), timeout=8)
                except asyncio.TimeoutError:
                    return None
            else:
                await event.wait()
            return self.get_cached_path(url)

    # 磁盘空间低于此阈值 (MB) 时拒绝下载，避免 .tmp 反复写失败并堆积
    _DISK_SPACE_MIN_MB = 500

    async def _download(self, url: str, key: str, foreground: bool = False) -> Optional[str]:
        """下载远程预览媒体到本地缓存。
        foreground=True（用户正在看的 /preview）：短超时 + 不重试，快速失败让卡片先显示占位，
        不占着浏览器有限的连接拖慢"点开详情"；后台预热则长超时 + 重试 3 次慢慢补齐。"""
        # SSRF 防御：拦截 127.0.0.1 / 内网 IP / file:// 等指向本机或私网的 URL，
        # 避免 /api/noctyra/preview 被当作代理探测用户内网服务
        if not await _is_safe_external_url(url):
            logger.warning("[Noctyra-MM] 拒绝不安全的预览 URL (SSRF 防御): %s", url[:100])
            self._prewarm_dead.add(url)
            return None

        # 磁盘空间预检：低于 500MB 直接跳过，避免反复写 .tmp 失败堆积脏数据
        try:
            free_mb = shutil.disk_usage(self._cache_dir).free / (1024 * 1024)
            if free_mb < self._DISK_SPACE_MIN_MB:
                logger.warning(
                    "[Noctyra-MM] 磁盘剩余不足 %.0f MB，跳过预览缓存: %s",
                    free_mb, url[:80],
                )
                return None
        except OSError:
            pass  # 查不到就正常走，磁盘真的满了下面 write 会自己抛

        # 全局限流冷却：之前撞过 429 → 窗口内直接跳过，避免真并发继续猛打 CDN（下次 prewarm 再试）
        if time.monotonic() < self._rate_limited_until:
            return None

        ext = self._ext_from_url(url)
        is_video = ext in (".mp4", ".webm")
        save_path = os.path.join(self._cache_dir, f"{key}{ext}")
        tmp_path = save_path + ".tmp"

        proxy = self._get_proxy()
        from .proxy_util import make_connector
        if foreground:
            timeout = aiohttp.ClientTimeout(total=15, connect=6)
        else:
            timeout = aiohttp.ClientTimeout(total=120 if is_video else 30, connect=10)
        # HuggingFace gated 仓库的图片也走 /resolve/main/...，需要带 token
        headers = self._auth_headers_for(url)

        def _rm_tmp(p):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        # CivitAI 图片常 302 跳到 image-b2.civitai.com（Backblaze B2），该 CDN 在受限网络下
        # 连接/握手很不稳。对"连接类瞬时错误"退避重试几次，能救回大量偶发失败。
        TRANSIENT = (
            aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError,
            aiohttp.ClientOSError, aiohttp.ClientPayloadError, asyncio.TimeoutError,
        )
        ATTEMPTS = 1 if foreground else 3
        REDIRECT_CODES = (301, 302, 303, 307, 308)
        for attempt in range(ATTEMPTS):
            try:
                async with aiohttp.ClientSession(timeout=timeout, connector=make_connector()) as session:
                    # 手动跟重定向:每一跳目标都重新过 SSRF 校验。allow_redirects=True 会在我们能
                    # 检查前就把请求发到跳转目标,恶意图床可 302 到 127.0.0.1/内网绕过初始校验;
                    # 故关掉自动跟随,逐跳校验后再请求。最多初始 1 次 + 3 跳(等同原 max_redirects=3)。
                    cur_url = url
                    for _hop in range(4):
                        if not await _is_safe_external_url(cur_url):
                            logger.warning("[Noctyra-MM] 拒绝不安全的重定向目标 (SSRF 防御): %s", cur_url[:100])
                            self._prewarm_dead.add(url)
                            return None
                        async with session.get(cur_url, proxy=proxy, headers=headers,
                                               allow_redirects=False) as resp:
                            if resp.status in REDIRECT_CODES:
                                loc = resp.headers.get("Location")
                                if not loc:
                                    logger.warning("[Noctyra-MM] 重定向缺少 Location: %s", cur_url[:80])
                                    return None
                                cur_url = urljoin(cur_url, loc)
                                continue   # 回到循环顶,对新目标重新 SSRF 校验
                            if resp.status != 200:
                                if resp.status == 429:
                                    retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
                                    self._rate_limited_until = time.monotonic() + retry_after
                                    logger.warning("[Noctyra-MM] 预览图 429 限流，暂停 %ds: %s", retry_after, url[:80])
                                    if not foreground:
                                        self._prewarm_fail_reason[url] = "限流(429)"
                                    return None
                                logger.warning("[Noctyra-MM] 预览图下载失败 %d: %s", resp.status, url[:80])
                                # 404 / 410 = 资源被删，标死链避免浪费重试
                                if resp.status in (404, 410):
                                    self._prewarm_dead.add(url)
                                if not foreground:
                                    self._prewarm_fail_reason[url] = (
                                        "已下架(404)" if resp.status in (404, 410) else f"HTTP {resp.status}")
                                return None

                            content_type = resp.headers.get("Content-Type", "")
                            if "mp4" in content_type:
                                ext = ".mp4"
                            elif "webm" in content_type:
                                ext = ".webm"
                            elif "webp" in content_type:
                                ext = ".webp"
                            elif "png" in content_type:
                                ext = ".png"
                            elif "gif" in content_type:
                                ext = ".gif"

                            actual_path = os.path.join(self._cache_dir, f"{key}{ext}")
                            tmp_path = actual_path + ".tmp"

                            with open(tmp_path, "wb") as f:
                                async for chunk in resp.content.iter_chunked(65536):
                                    f.write(chunk)

                            # os.replace 在 Windows 上也会原子覆盖已存在文件，避免 WinError 183
                            os.replace(tmp_path, actual_path)
                            # 新文件落盘 → 并入缓存 key 快照(若在 TTL 内),让列表/预览状态接口立即看到,
                            # 消除"后台缓存好了前端还闪几秒占位"的窗口(快照默认 3s TTL)。同一事件循环
                            # 线程内改 set,无并发问题。
                            if self._keys_snapshot is not None:
                                self._keys_snapshot.add(key)
                            logger.debug("[Noctyra-MM] 预览图已缓存: %s", url[:80])
                            return actual_path
                    else:
                        # 4 次循环用尽仍全是重定向 = 重定向过多,放弃
                        logger.warning("[Noctyra-MM] 预览图重定向过多: %s", url[:80])
                        return None

            except TRANSIENT as e:
                _rm_tmp(tmp_path)
                if attempt < ATTEMPTS - 1:
                    await asyncio.sleep(0.6 * (attempt + 1))   # 退避后重试
                    continue
                # 异常无消息时（如 TimeoutError）退回打异常类型，别让日志只剩 "— "
                logger.warning("[Noctyra-MM] 预览图缓存失败(已重试 %d 次): %s — %s",
                               ATTEMPTS, url[:80], str(e) or type(e).__name__)
                if not foreground:
                    self._prewarm_fail_reason[url] = str(e) or type(e).__name__
                return None
            except Exception as e:
                _rm_tmp(tmp_path)
                logger.warning("[Noctyra-MM] 预览图缓存失败: %s — %s", url[:80], str(e) or type(e).__name__)
                if not foreground:
                    self._prewarm_fail_reason[url] = str(e) or type(e).__name__
                return None

    @staticmethod
    def _auth_headers_for(url: str) -> dict:
        """对 huggingface.co 的 URL 附加 HF token（解决 gated 仓库图片 401/403）"""
        try:
            if "huggingface.co" not in url:
                return {}
            from .config import get_config
            token = get_config().get("huggingface_token", "") or ""
            if token:
                return {"Authorization": f"Bearer {token}"}
        except Exception:
            pass
        return {}

    @staticmethod
    def _get_proxy() -> Optional[str]:
        from .proxy_util import get_proxy  # 统一代理解析，见 proxy_util
        return get_proxy()

    def get_content_type(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".avif": "image/avif",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
        }.get(ext, "image/jpeg")

    # ========== 后台预热队列 ==========

    def schedule_prewarm(self, urls, skip_cached_check: bool = False) -> int:
        """把 URL 扔进后台预热队列；已在磁盘 / 已在队列 / 已知死链的跳过。
        返回新入队数量；首次调用会懒启动 worker。
        skip_cached_check=True：调用方（如 nofetch 网格请求）刚确认过未命中，
        跳过这里重复的磁盘 isfile 复查；worker 里 ensure_cached 仍会兜底判缓存，安全。"""
        # 新一轮判定 → 清零进度计数（给任务中心用）。仅靠 worker.done() 不够：worker 空闲 5 分钟
        # 才退出，期间浏览(nofetch)/匹配的零散预热会累加进上一轮旧计数，进度条显示失真。
        # 故再加"上一轮已全部处理完(done+failed 达 total 且队列空)"也算新一轮。
        prev_round_done = (self._prewarm_total > 0
                           and (self._prewarm_done + self._prewarm_failed) >= self._prewarm_total
                           and not self._prewarm_pending)
        if self._prewarm_worker is None or self._prewarm_worker.done() or prev_round_done:
            self._prewarm_total = 0
            self._prewarm_done = 0
            self._prewarm_failed = 0
            self._prewarm_failures = []
            self._prewarm_fail_reason.clear()
            self._prewarm_started_at = time.monotonic()
            self._prewarm_cancelled = False
        added = 0
        for u in urls or ():
            if not u or not isinstance(u, str):
                continue
            # sidecar:// 等本地引用不走网络
            if u.startswith("sidecar://") or u.startswith("/"):
                continue
            if u in self._prewarm_dead:
                continue
            if u in self._prewarm_pending:
                continue
            if not skip_cached_check and self.get_cached_path(u):
                continue
            self._prewarm_pending.add(u)
            added += 1
        self._prewarm_total += added
        if added:
            self._prewarm_signal.set()
            self._ensure_prewarm_worker()
        return added

    def get_prewarm_status(self) -> dict:
        """后台预热进度，给前端任务中心轮询展示。"""
        done = self._prewarm_done
        failed = self._prewarm_failed
        # 取消后 total 收敛到已处理数(显示为"已停于 X/X"),不再卡在大数字上
        total = (done + failed) if self._prewarm_cancelled else self._prewarm_total
        active = (not self._prewarm_cancelled
                  and bool(self._prewarm_worker and not self._prewarm_worker.done())
                  and (done + failed) < total)
        elapsed = (time.monotonic() - self._prewarm_started_at) if self._prewarm_started_at else 0.0
        return {
            "active": active,
            "cancelled": self._prewarm_cancelled,
            "total": total,
            "done": done,
            "failed": failed,
            "pending": max(0, total - done - failed),
            "elapsed": round(elapsed, 1),
            "recent_failures": self._prewarm_failures[-30:],
        }

    def cancel_prewarm(self):
        """取消预热:清空待处理队列 + 置取消标志(在途批次会尽快跳过、不再起新批)。
        已下载的保留;total 在 get_prewarm_status 里收敛到已处理数,显示为'已停'。"""
        self._prewarm_cancelled = True
        self._prewarm_pending.clear()
        self._prewarm_signal.set()   # 唤醒 worker 让它尽快走到空队列分支

    def _ensure_prewarm_worker(self):
        """懒启动后台 worker（在事件循环里只开一个）"""
        if self._prewarm_worker and not self._prewarm_worker.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 没跑在 loop 里，算了
        self._prewarm_worker = loop.create_task(self._prewarm_loop())

    async def _prewarm_loop(self):
        """后台消费 _prewarm_pending：限 _PREWARM_CONCURRENCY 并发下载"""
        sem = asyncio.Semaphore(self._PREWARM_CONCURRENCY)

        async def download_one(url: str):
            async with sem:
                if self._prewarm_cancelled:
                    return   # 已取消:在途批次剩余项直接跳过,不下载、不计数
                try:
                    result = await self.ensure_cached(url)
                except Exception as e:
                    logger.debug("[Noctyra-MM] 后台预热异常 %s: %s", url[:60], e)
                    self._prewarm_fail_reason.setdefault(url, str(e) or type(e).__name__)
                    result = None
                # 计进度：每个 URL 要么成功要么失败，让 done+failed 与 total 对齐
                if result:
                    self._prewarm_done += 1
                else:
                    self._prewarm_failed += 1
                    reason = self._prewarm_fail_reason.pop(url, "下载失败")
                    self._prewarm_failures.append({"url": url, "reason": reason})
                    if len(self._prewarm_failures) > 60:
                        self._prewarm_failures = self._prewarm_failures[-60:]

        # 用 get_nowait 风格的拿法循环，直到队列空
        while True:
            if not self._prewarm_pending:
                self._prewarm_signal.clear()
                # 等一会，有新任务就再循环；5 分钟无任务则退出 worker（省资源）
                try:
                    await asyncio.wait_for(self._prewarm_signal.wait(), timeout=300)
                except asyncio.TimeoutError:
                    logger.debug("[Noctyra-MM] 预热 worker 空闲 5 分钟，退出")
                    return
                continue

            # 一次快照批量处理，避免 set 迭代期间修改
            batch = list(self._prewarm_pending)[:64]
            self._prewarm_pending.difference_update(batch)
            if batch:
                try:
                    await asyncio.gather(*(download_one(u) for u in batch), return_exceptions=True)
                except Exception as e:
                    logger.warning("[Noctyra-MM] 预热批次异常: %s", e)

    def cached_keys_snapshot(self, ttl: float = 3.0) -> set:
        """列一次缓存目录、返回"已缓存 key"集合（短 TTL 复用）。
        给批量状态检查（列表分页 / 预览筛选）用：一次 listdir 顶替成千上万次磁盘 stat。"""
        now = time.monotonic()
        snap = self._keys_snapshot
        if snap is not None and (now - self._keys_snapshot_ts) < ttl:
            return snap
        keys = set()
        try:
            for fn in os.listdir(self._cache_dir):
                if fn.endswith(".tmp"):
                    continue
                # 文件名 = <64位hex key>.<ext>；key 不含点，rsplit 取 key
                keys.add(fn.rsplit(".", 1)[0] if "." in fn else fn)
        except OSError:
            pass
        self._keys_snapshot = keys
        self._keys_snapshot_ts = now
        return keys

    def check_urls_status(self, urls, cached_keys: Optional[set] = None) -> dict:
        """对一组 URL 检查本地缓存状态，返回 {total, cached, dead, missing}。

        - cached：磁盘上有文件
        - dead：已标记死链（404/410），重试无意义
        - missing = total - cached（包含 dead + 尚未下载），前端筛选以此分组
        - cached_keys：传入 cached_keys_snapshot() 则用内存集合判断（O(1)，不碰磁盘）；
          不传则逐个 get_cached_path（每 URL 最多 8 次 stat），仅单条查询用。
        """
        total = 0
        cached = 0
        dead = 0
        for u in urls or ():
            if not u or not isinstance(u, str):
                continue
            # sidecar / 本地路径不算网络预览
            if u.startswith("sidecar://") or u.startswith("/"):
                continue
            total += 1
            is_cached = (self.url_to_key(u) in cached_keys) if cached_keys is not None \
                else bool(self.get_cached_path(u))
            if is_cached:
                cached += 1
            elif u in self._prewarm_dead:
                dead += 1
        return {
            "total": total,
            "cached": cached,
            "dead": dead,
            "missing": total - cached,
            "complete": total == cached and total > 0,
        }

    def cleanup_orphaned(self, valid_urls: set) -> int:
        """清理不再引用的缓存文件，返回清理数量"""
        valid_keys = {self.url_to_key(u) for u in valid_urls}
        removed = 0
        for fname in os.listdir(self._cache_dir):
            # 跳过正在下载的临时文件：必须判原始文件名，splitext("x.webp.tmp")[0]
            # 得到 "x.webp"（不以 .tmp 结尾）会漏判，导致活跃下载被当孤儿误删
            if fname.endswith(".tmp"):
                continue
            key = os.path.splitext(fname)[0]
            if key not in valid_keys:
                full = os.path.join(self._cache_dir, fname)
                try:
                    os.remove(full)
                    removed += 1
                    self._remove_thumb(full)  # 连带删它的缩略图，避免孤儿堆积
                except OSError:
                    pass
        if removed:
            logger.info("[Noctyra-MM] 清理了 %d 个孤立预览图缓存", removed)
        else:
            logger.info("[Noctyra-MM] 没有需要清理的预览图缓存")
        # 缩略图按源路径哈希命名，无法反查源是否还在；本地图/图库图的缩略图不在上面
        # 的预览缓存目录里，删模型后会留孤儿。缩略图是可再生缓存，这里按体积上限淘汰旧的兜底。
        self._prune_thumbs()
        self._cache_stats_ts = 0.0   # 文件已变，置统计缓存失效，下次立即重算
        return removed

    def get_cache_stats(self, ttl: float = 5.0) -> dict:
        """预览图缓存 + 缩略图缓存的文件数与总字节数（设置页"缓存管理"展示用）。
        CPU/IO 密集（2 万+文件全盘扫描），应在 executor 里调用，勿阻塞事件循环。
        结果缓存 ~5s（ttl），避免频繁重扫；文件增删的接口会主动置失效。"""
        now = time.monotonic()
        cached = self._cache_stats_cache
        if cached is not None and (now - self._cache_stats_ts) < ttl:
            return cached

        def _dir_stat(d):
            cnt = total = 0
            try:
                # scandir 一次拿到 dir entry 的类型/大小（Windows 上无需逐文件额外 stat 系统调用），
                # 比 listdir + isfile + getsize 快得多
                with os.scandir(d) as it:
                    for entry in it:
                        try:
                            if not entry.is_file():  # 跳过 thumbs 子目录等
                                continue
                            total += entry.stat().st_size
                        except OSError:
                            continue
                        cnt += 1
            except OSError:
                pass
            return cnt, total
        p_cnt, p_bytes = _dir_stat(self._cache_dir)
        t_cnt, t_bytes = _dir_stat(self._thumb_dir)
        stats = {
            "preview_count": p_cnt, "preview_bytes": p_bytes,
            "thumb_count": t_cnt, "thumb_bytes": t_bytes,
        }
        self._cache_stats_cache = stats
        self._cache_stats_ts = now
        return stats

    def clear_thumbs(self) -> int:
        """清空全部缩略图（可再生缓存，下次浏览自动重建）。返回删除数量。"""
        removed = 0
        try:
            if os.path.isdir(self._thumb_dir):
                for fn in os.listdir(self._thumb_dir):
                    fp = os.path.join(self._thumb_dir, fn)
                    try:
                        if os.path.isfile(fp):
                            os.remove(fp)
                            removed += 1
                    except OSError:
                        pass
        except OSError:
            pass
        if removed:
            logger.info("[Noctyra-MM] 清空缩略图缓存：删除 %d 个", removed)
        self._cache_stats_ts = 0.0   # 文件已变，置统计缓存失效，下次立即重算
        return removed

    def _prune_thumbs(self, max_bytes: int = 512 * 1024 * 1024) -> int:
        """缩略图缓存总体积超上限时，按 mtime 旧→新淘汰，防止孤儿无限堆积。
        缩略图随时可再生，淘汰最坏只是下次浏览重新生成一次。返回淘汰数量。"""
        try:
            if not os.path.isdir(self._thumb_dir):
                return 0
            entries = []
            total = 0
            for fn in os.listdir(self._thumb_dir):
                fp = os.path.join(self._thumb_dir, fn)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, fp))
                total += st.st_size
            if total <= max_bytes:
                return 0
            entries.sort()  # 旧的在前，先淘汰
            removed = 0
            for _mtime, size, fp in entries:
                if total <= max_bytes:
                    break
                try:
                    os.remove(fp)
                    total -= size
                    removed += 1
                except OSError:
                    pass
            if removed:
                logger.info("[Noctyra-MM] 缩略图缓存超上限，淘汰 %d 个", removed)
            return removed
        except OSError:
            return 0
