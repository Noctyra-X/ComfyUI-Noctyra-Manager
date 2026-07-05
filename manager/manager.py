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
模型管理器 — 核心调度

串联扫描器、API 客户端和数据库，提供统一的管理接口。
主要流程：scan → hash → match (CivitAI/HF) → 存入 SQLite。
使用 asyncio.Lock 保证同时只运行一个扫描或匹配任务。
"""

import asyncio
import html
import logging
import os
import re
import uuid
import time
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional, Tuple

from .config import Config, get_config
from .scanner import ModelScanner, ModelInfo, compute_sha256, read_safetensors_metadata, extract_trained_words, get_io_executor
from .civitai import CivitaiClient, build_model_url as _civitai_model_url
from .huggingface import HuggingFaceClient
from .database import ModelDatabase
from .downloader import DownloadManager
from .manager_organize import _OrganizeMixin, _safe_move

logger = logging.getLogger("noctyra.manager")

# 匹配 API 请求间隔（秒），避免被限流
API_DELAY = 0.5
# 单个模型撞 CivitAI 限流时，最多"等冷却→重试"几轮，确保一次批量匹配尽量跑完整批，
# 而不是被一次 429 连累放弃。超过仍限流则留作未匹配，下次重跑会自动接着跑。
_MATCH_RL_MAX_WAITS = 4


class ModelManager(_OrganizeMixin):
    """模型管理器"""

    def __init__(self, config: Config = None):
        self.config = config or get_config()
        self.scanner = ModelScanner(self.config.scan_extensions)
        self.civitai = CivitaiClient(self.config.civitai_api_key)
        self.huggingface = HuggingFaceClient(self.config.huggingface_token)

        db_path = os.path.join(self.config.cache_dir, "model_cache.sqlite")
        self.db = ModelDatabase(db_path)
        # base_model 规范集对齐 CivitAI 官方枚举：先从磁盘缓存载入上次拉取的官方列表，
        # 再把库里历史名（含我们以前自造的 Qwen Image/Flux 1/SDXL …）归一到官方名。
        from . import base_models as _bm
        self._bm_cache_path = os.path.join(self.config.cache_dir, "civitai_base_models.json")
        self._bm_refreshed = False
        _bm.load_official_cache(self._bm_cache_path)
        try:
            self.db.normalize_base_models()
        except Exception as e:
            logger.warning("[Noctyra-MM] base_model 归一化失败: %s", e)

        self._is_scanning = False
        self._is_matching = False
        self._progress = None   # 当前扫描/匹配进度 {op, stage, current, total, detail}；供刷新后恢复 UI
        self._is_checking_updates = False
        self._mutating_depth = 0  # 整理/移动/删除/单匹配/导入等"会改文件或元数据"的互斥计数
        self._scan_lock = asyncio.Lock()
        self._match_lock = asyncio.Lock()

        # 下载管理（委托给 DownloadManager）
        self.downloader = DownloadManager(self, max_concurrent=3)

        # 插件被整体拷到新位置：旧 DB 仍存留但图库记录里的 file_path 全部失效，
        # 顺手清理，避免 UI 满屏 404 缩略图。models 表依赖扫描时 remove_missing
        # 的 SHA256 合并逻辑，不在此处清理。
        if getattr(self.config, "stale_install_reset", False):
            try:
                removed = self.db.cleanup_missing_workflow_images()
                if removed:
                    logger.warning(
                        "[Noctyra-MM] 因检测到插件位置变更，已清理 %d 个失效图库记录",
                        removed,
                    )
            except Exception as e:
                logger.warning("[Noctyra-MM] 失效图库记录清理失败: %s", e)

        logger.info("[Noctyra-MM] 管理器已初始化，配置了 %d 个模型目录", len(self.config.model_roots))

    async def close(self):
        """关闭所有客户端连接"""
        await self.civitai.close()
        await self.huggingface.close()

    # ========== 扫描 ==========

    @staticmethod
    def _infer_model_type(root_dir: str) -> str:
        """从扫描根目录名推断模型类型"""
        name = os.path.basename(root_dir).lower()
        if "lora" in name:
            return "lora"
        if "checkpoint" in name:
            return "checkpoint"
        if "embedding" in name or "textual_inversion" in name:
            return "embedding"
        if "vae" in name:
            return "vae"
        if "controlnet" in name:
            return "controlnet"
        if "upscale" in name or "esrgan" in name:
            return "upscale"
        if "text_encoder" in name:
            return "text_encoder"
        if "clip_vision" in name or "clipvision" in name:
            return "clip_vision"
        if "clip" in name:
            return "clip"
        if "motion" in name or "animatediff" in name:
            return "motion"
        if "ultralytics" in name or "detection" in name or "yolo" in name:
            return "detection"
        if "unet" in name or "diffusion_model" in name:
            return "unet"
        return "other"

    @property
    def runtime_status(self) -> Dict:
        """供前端刷新后恢复 UI：是否在扫描/匹配 + 当前进度。"""
        return {
            "is_scanning": self._is_scanning,
            "is_matching": self._is_matching,
            "progress": self._progress,
        }

    def _track_progress(self, op: str, downstream: Callable = None) -> Callable:
        """包一层进度回调：除转发给 WS，还把最新进度存进 self._progress（刷新后可查）。"""
        async def cb(stage, current, total, detail):
            self._progress = {"op": op, "stage": stage, "current": current,
                              "total": total, "detail": detail}
            if downstream:
                await downstream(stage, current, total, detail)
        return cb

    async def scan(self, progress_callback: Callable = None, force: bool = False) -> int:
        """扫描所有配置的模型目录

        Args:
            progress_callback: async def callback(stage, current, total, detail)
            force: True = 全量重扫，忽略增量跳过，对每个文件重新解析+重判类型
                   （用于修正历史误分类，如新格式 LoRA 曾被判成 unet）。保留用户数据。

        Returns:
            发现的模型总数
        """
        # 用 bool flag 而非 lock.locked() 检查：asyncio 单线程中，
        # "读 flag → 写 flag" 之间无 await 所以原子，避免两个协程同时通过 .locked() 检查
        if self._is_scanning:
            logger.warning("[Noctyra-MM] 扫描已在进行中")
            return 0
        self._is_scanning = True
        self._progress = {"op": "scan", "stage": "", "current": 0, "total": 0, "detail": "准备中…"}
        try:
            async with self._scan_lock:
                return await self._do_scan(self._track_progress("scan", progress_callback), force=force)
        finally:
            self._is_scanning = False
            self._progress = None

    async def _maybe_refresh_base_models(self):
        """后台拉取 CivitAI 官方 base model 枚举（缓存 >7 天才拉），拉到新表后重新归一库。
        失败/慢都不影响扫描——内存里始终有缓存/兜底列表。"""
        try:
            path = getattr(self, "_bm_cache_path", "")
            stale = (not path) or (not os.path.isfile(path)) or (time.time() - os.path.getmtime(path) > 7 * 86400)
            if not stale:
                return
            from . import base_models as _bm
            n = await _bm.fetch_official_base_models(path)  # 网络拉取，不持锁
            if n:
                # 归一是全表 UPDATE，必须排到扫描写库之后再跑：取 _scan_lock，
                # 若扫描仍在进行则等其释放，避免与 upsert 并发写库（database is locked / 互相覆盖）
                async with self._scan_lock:
                    try:
                        self.db.normalize_base_models()
                    except Exception:
                        pass
        except Exception:
            pass

    async def _do_scan(self, progress_callback: Callable = None, force: bool = False) -> int:
        scan_start = time.time()
        # 同步 DB 写各自卸载到线程池，避免大批量入库/清理阻塞事件循环卡住 UI。
        # DB 层用 threading.Lock + 每调用独立连接，线程安全。
        loop = asyncio.get_running_loop()
        # 首次扫描时后台刷新官方 base model 列表（不阻塞扫描）
        if not getattr(self, "_bm_refreshed", False):
            self._bm_refreshed = True
            try:
                asyncio.create_task(self._maybe_refresh_base_models())
            except Exception:
                pass
        # 启动后台自动查更新（只调度一次，方法内有 config 开关 + 延迟 + TTL 防护）
        if not getattr(self, "_auto_upd_scheduled", False):
            self._auto_upd_scheduled = True
            try:
                asyncio.create_task(self._maybe_auto_check_updates())
            except Exception:
                pass
        all_roots = list(self.config.model_roots)
        if not all_roots:
            logger.warning("[Noctyra-MM] 没有配置任何模型目录")
            return 0

        logger.info("[Noctyra-MM] 扫描任务启动，配置 %d 个根目录: %s", len(all_roots), all_roots)

        # ========== 阶段1：扫描文件系统 ==========
        phase1_start = time.time()
        if progress_callback:
            await progress_callback("scan", 0, 0, "阶段 1/3 扫描文件...")

        # 先修正历史脏数据里的 folder 字段（增量扫描会跳过 mtime 未变的文件，
        # 导致早期下载时 basename(save_dir) bug 残留的 folder="Anima"/"Illustrious"
        # 等非规范值永远得不到覆盖；这里按 file_path + model_root 反推重写）
        try:
            await loop.run_in_executor(None, self.db.repair_folders, list(self.config.model_roots))
        except Exception as e:
            logger.warning("[Noctyra-MM] folder 修复失败: %s", e)

        # 增量扫描加速：先读 DB 已知 mtime，scanner 对未变文件跳过 metadata 解析。
        # force=True 时传空表 → 不跳过任何文件，全部重解析+重判类型（upsert 保留收藏/标签等）。
        try:
            known_stats = {} if force else self.db.get_existing_file_stats()
        except Exception:
            known_stats = {}

        all_models = []
        scan_failed = False
        for root_dir in all_roots:
            try:
                models = await self.scanner.scan_directory_async(root_dir, known_stats=known_stats)
                folder_type = self._infer_model_type(root_dir)
                fallback_count = 0
                for m in models:
                    # scanner 已通过 safetensors key 结构设置 m.model_type（更准），此处不覆盖；
                    # 只有 safetensors 判定失败（非 .safetensors / 解析异常 / 增量跳过）才用目录名兜底
                    if not m.model_type:
                        m.model_type = folder_type
                        fallback_count += 1
                all_models.extend(models)
                logger.info(
                    "[Noctyra-MM] 扫描 %s: 发现 %d 个模型 (目录推断类型: %s, 回退 %d 个)",
                    root_dir, len(models), folder_type, fallback_count,
                )
            except Exception:
                scan_failed = True
                logger.error("[Noctyra-MM] 扫描根目录 %s 失败", root_dir, exc_info=True)

        phase1_elapsed = time.time() - phase1_start
        logger.info(
            "[Noctyra-MM] 阶段 1/3 完成: 共 %d 个模型文件，耗时 %.2fs%s",
            len(all_models), phase1_elapsed,
            "（部分根目录扫描失败，跳过清理阶段以保护数据）" if scan_failed else "",
        )

        # 只有当所有根目录扫描都成功时才清理缺失记录，避免因为临时 IO 错误误删好数据
        if not scan_failed:
            await loop.run_in_executor(None, self.db.remove_missing, {m.file_path for m in all_models})
        else:
            logger.warning("[Noctyra-MM] 跳过 remove_missing（扫描失败保护）")

        # ========== 阶段2：批量入库 ==========
        phase2_start = time.time()
        if progress_callback:
            await progress_callback("scan", 0, 0, "阶段 2/3 入库...")

        try:
            existing_hashes = self.db.get_existing_hashes()
            need_hash = []
            batch = []
            corrupt_flags = {}   # 只对本轮解析过的文件写损坏标志（增量跳过的不动）
            skipped_incremental = 0
            for model in all_models:
                if model.file_path in existing_hashes:
                    model.sha256 = existing_hashes[model.file_path]

                # 增量优化：文件 mtime 未变（scanner 标记了 _noctyra_incremental_skip）
                # 且 DB 已有 sha256 → 完全跳过 upsert，不浪费 SQLite 写
                if (isinstance(model.metadata_raw, dict)
                        and model.metadata_raw.get("_noctyra_incremental_skip")
                        and model.sha256):
                    skipped_incremental += 1
                    continue

                batch.append({
                    "file_path": model.file_path,
                    "file_name": model.file_name,
                    "file_ext": model.file_ext,
                    "file_size": model.file_size,
                    "modified": model.file_modified,
                    "sha256": model.sha256,
                    "base_model": model.base_model,
                    "trained_words": model.trained_words,
                    "model_type": model.model_type,
                    "lora_subtype": model.lora_subtype,
                    "folder": model.folder,
                })
                corrupt_flags[model.file_path] = getattr(model, "file_corrupt", 0)

                if not model.sha256:
                    need_hash.append(model)

            if batch:
                await loop.run_in_executor(None, self.db.upsert_models_batch, batch)
            if corrupt_flags:
                await loop.run_in_executor(None, self.db.set_corrupt_flags, corrupt_flags)   # 入库后再写损坏标志（不动复杂 upsert）
            if skipped_incremental:
                logger.info("[Noctyra-MM] 增量扫描节省 %d 个文件的入库（未变）", skipped_incremental)
        except Exception:
            logger.error("[Noctyra-MM] 阶段 2/3 入库失败", exc_info=True)
            return len(all_models)

        phase2_elapsed = time.time() - phase2_start
        logger.info(
            "[Noctyra-MM] 阶段 2/3 完成: 入库 %d 条，复用哈希 %d 条，待计算 %d 条，耗时 %.2fs",
            len(all_models), len(all_models) - len(need_hash), len(need_hash), phase2_elapsed,
        )

        # ========== 阶段3：补算 SHA256 ==========
        phase3_start = time.time()
        total_need = len(need_hash)
        hash_ok = 0
        hash_fail = 0

        if total_need > 0:
            if progress_callback:
                await progress_callback("hash", 0, total_need, "阶段 3/3 计算哈希...")

            loop = asyncio.get_running_loop()
            io_pool = get_io_executor()
            for i, model in enumerate(need_hash):
                try:
                    sha256 = await loop.run_in_executor(
                        io_pool, compute_sha256, model.file_path
                    )
                except Exception as e:
                    logger.warning("[Noctyra-MM] 哈希计算异常 %s: %s", model.file_name, e)
                    sha256 = None

                if sha256:
                    self.db.update_hash(model.file_path, sha256)
                    hash_ok += 1
                else:
                    hash_fail += 1

                if progress_callback:
                    await progress_callback("hash", i + 1, total_need, model.file_name)

        phase3_elapsed = time.time() - phase3_start
        total_elapsed = time.time() - scan_start
        logger.info(
            "[Noctyra-MM] 阶段 3/3 完成: 成功 %d，失败 %d，耗时 %.2fs",
            hash_ok, hash_fail, phase3_elapsed,
        )
        logger.info(
            "[Noctyra-MM] 扫描任务结束: 总计 %d 个模型，总耗时 %.2fs",
            len(all_models), total_elapsed,
        )
        return len(all_models)

    # ========== 在线匹配 ==========

    async def match_all(self, progress_callback: Callable = None, rematch: bool = False) -> Dict[str, int]:
        """对所有未匹配的模型进行在线匹配

        Args:
            rematch: 如果为 True，也重新匹配已匹配但缺少详细数据的模型

        Returns:
            {"civitai_matched": N, "hf_matched": N, "unmatched": N}
        """
        if self._is_matching:
            logger.warning("[Noctyra-MM] 匹配已在进行中")
            return {}
        self._match_cancel = False
        self._is_matching = True
        self._progress = {"op": "match", "stage": "", "current": 0, "total": 0, "detail": "准备中…"}
        try:
            async with self._match_lock:
                return await self._do_match_all(self._track_progress("match", progress_callback), rematch)
        finally:
            self._is_matching = False
            self._progress = None

    def cancel_match(self):
        """请求中途停止匹配:_do_match_all 循环每个模型前检查此标志,已匹配的保留,
        未处理的下次再匹配。仅置标志,不阻塞。"""
        self._match_cancel = True

    async def _do_match_all(self, progress_callback: Callable = None, rematch: bool = False) -> Dict[str, int]:
        stats = {"civitai_matched": 0, "hf_matched": 0, "unmatched": 0}

        unmatched = self.db.get_unmatched(include_rematch=rematch)
        total = len(unmatched)
        logger.info("[Noctyra-MM] 开始在线匹配，共 %d 个未匹配模型", total)

        for i, model in enumerate(unmatched):
            if getattr(self, "_match_cancel", False):
                logger.info("[Noctyra-MM] 匹配被用户取消，已处理 %d/%d（已匹配的保留）", i, total)
                break
            sha256 = model.get("sha256", "")
            filename = model.get("file_name", "")

            if progress_callback:
                await progress_callback("match", i + 1, total, filename)

            if not sha256:
                logger.debug("[Noctyra-MM] 跳过无哈希模型: %s", filename)
                stats["unmatched"] += 1
                continue

            has_civitai = model.get("source") == "civitai"
            has_hf = bool(model.get("hf_repo_id"))

            civitai_ok = has_civitai
            if not has_civitai or rematch:
                fetched = await self._civitai_match_resilient(sha256, progress_callback, i + 1, total)
                if fetched:
                    civitai_ok = True
                    if not has_civitai:
                        stats["civitai_matched"] += 1
                await asyncio.sleep(API_DELAY)

            if not has_hf or rematch:
                fetched = await self._try_hf_match(sha256, filename, supplement=civitai_ok)
                if fetched:
                    if not has_hf:
                        stats["hf_matched"] += 1
                await asyncio.sleep(API_DELAY)

            if not civitai_ok and not has_hf:
                stats["unmatched"] += 1

        # 把已匹配的在线数据补给同 sha256 的未匹配副本（重复文件一起受益），覆盖历史遗留的重复
        try:
            self.db.backfill_duplicate_matches()
        except Exception as e:
            logger.debug("[Noctyra-MM] 重复回填失败: %s", e)

        logger.info(
            "[Noctyra-MM] 匹配完成: CivitAI %d, HuggingFace %d, 未匹配 %d",
            stats["civitai_matched"], stats["hf_matched"], stats["unmatched"]
        )
        return stats

    def _schedule_preview_prewarm(self, info: dict):
        """匹配成功后把该模型所有预览图扔进后台预热队列（fire-and-forget）。

        避免用户日后首次打开详情页时被一堆 CivitAI CDN 同步下载卡住。
        """
        if not info:
            return
        urls = []
        # 主预览图
        pu = info.get("preview_url")
        if pu:
            urls.append(pu)
        # 作者头像
        avatar = info.get("creator_avatar")
        if avatar:
            urls.append(avatar)
        # preview_images 列表里的每个 URL
        for p in info.get("preview_images") or []:
            if isinstance(p, dict):
                u = p.get("url")
                if u:
                    urls.append(u)
            elif isinstance(p, str):
                urls.append(p)
        if not urls:
            return
        try:
            from .routes import _get_preview_cache
            _get_preview_cache().schedule_prewarm(urls)
        except Exception as e:
            logger.debug("[Noctyra-MM] 预热调度失败: %s", e)

    async def _civitai_match_resilient(self, sha256: str, progress_callback=None,
                                       idx: int = 0, total: int = 0) -> bool:
        """匹配单个模型，撞限流就按 Retry-After 等冷却再重试同一个，确保整批尽量跑完。

        中断（停 ComfyUI）后重跑会接着未匹配的（get_unmatched 只返回未匹配项），所以
        "全跑完 / 能中断再续" 两个目标都满足。"""
        for _ in range(_MATCH_RL_MAX_WAITS):
            # 限流冷却中 → 先等冷却结束再给这个模型一次新机会（而不是直接跳过）
            wait = self.civitai.cooldown_remaining()
            if wait > 0:
                wait = min(wait + 1, self.civitai._RATE_LIMIT_MAX_SEC + 5)
                if progress_callback:
                    await progress_callback("match", idx, total, f"CivitAI 限流，等待 {int(wait)}s 后继续…")
                logger.info("[Noctyra-MM] CivitAI 限流，%ds 后续跑匹配", int(wait))
                await asyncio.sleep(wait)
            if await self._try_civitai_match(sha256):
                return True
            if not self.civitai.is_rate_limited():
                return False   # 真没匹配（404 等），不是限流，无需再等
        return False

    async def _try_civitai_match(self, sha256: str) -> bool:
        """尝试 CivitAI 匹配，仅在 CivitAI 真 404 时回退查归档 / CivArchive。

        临时错误（超时 / 限流 / 网络异常）不回退归档 —— 否则老的缓存 info
        会把新修的字段（如 nsfw=True）覆盖回旧值 False，用户重匹配越改越错。
        """
        # 全局限流冷却中：直接跳过整个模型，不浪费 30s 走单模型 3 次重试
        # 1000+ 模型批量匹配时这一检查决定几小时 vs 几分钟的差别
        if self.civitai.is_rate_limited():
            return False

        version_data, error = await self.civitai.get_model_by_hash(sha256)

        # 限流/超时/网络等临时错误不动 DB（等下次）；批量匹配的"撞限流→等冷却再续跑"
        # 由外层 _civitai_match_resilient 统一处理（按 Retry-After 等），这里不空转重试。
        if error == "rate_limited":
            logger.warning("[Noctyra-MM] CivitAI 限流（等冷却后续跑）: %s", sha256[:10])
        elif error and error.startswith("timeout"):
            logger.info("[Noctyra-MM] CivitAI 超时（不重试）: %s", sha256[:10])
        elif error and error.startswith("network"):
            logger.info("[Noctyra-MM] CivitAI 网络错误（不重试）: %s - %s", sha256[:10], error)

        # error is None ≡ 真 404（CivitAI 查不到），才允许回退老数据；
        # 其他 error 都是临时性问题，保持 DB 原状等下次重试
        is_genuine_404 = error is None and version_data is None

        if version_data:
            info = CivitaiClient.parse_version_info(version_data)

            # 用完整 model 信息补全 creator/description/tags/stats
            model_id = info.get("civitai_model_id")
            model_data = None
            if model_id:
                await asyncio.sleep(API_DELAY)
                model_data = await self.civitai.get_model_info(model_id)
                if model_data:
                    info = CivitaiClient.enrich_with_model_info(info, model_data)

            raw_data = {"version": version_data}
            if model_data:
                raw_data["model"] = model_data
            info["_raw_data"] = raw_data

            self.db.update_online_info(sha256, info)
            self._schedule_preview_prewarm(info)
            logger.info("[Noctyra-MM] CivitAI 匹配成功: %s (by %s)", info.get("model_name", ""), info.get("creator", ""))
            return True

        # 临时错误（超时/限流/网络）不动 DB，等下次重试
        if not is_genuine_404:
            return False

        # by-hash 真 404 —— 先看 DB 里是否已知 civitai_version_id：
        # 这说明文件之前匹配成功过（CivitAI 曾认它），只是现在 hash 对不上
        # （文件被本地修改 / CivitAI 侧改了 hash）。用 version_id 重新拉一次
        # 活数据，让 parse+enrich 按新逻辑跑，比用旧归档更准。
        known_vid = None
        try:
            existing = self.db.get_by_hash(sha256)
            if existing:
                known_vid = existing.get("civitai_version_id")
        except Exception:
            pass

        if known_vid:
            version_data = await self.civitai.get_model_version(int(known_vid))
            if version_data:
                info = CivitaiClient.parse_version_info(version_data)
                model_id = info.get("civitai_model_id")
                model_data = None
                if model_id:
                    await asyncio.sleep(API_DELAY)
                    model_data = await self.civitai.get_model_info(model_id)
                    if model_data:
                        info = CivitaiClient.enrich_with_model_info(info, model_data)
                raw_data = {"version": version_data}
                if model_data:
                    raw_data["model"] = model_data
                info["_raw_data"] = raw_data
                self.db.update_online_info(sha256, info)
                self._schedule_preview_prewarm(info)
                logger.info(
                    "[Noctyra-MM] CivitAI 匹配成功（按 version_id=%s 重取，hash 对不上）: %s",
                    known_vid, info.get("model_name", ""),
                )
                return True

        # 再回退归档：旧记录里 _raw_data.model 保留的话可以再 enrich 一次
        archived = self.db.get_archived(sha256)
        if archived and archived.get("source") == "civitai":
            raw_model = (archived.get("_raw_data") or {}).get("model")
            if isinstance(raw_model, dict):
                archived = CivitaiClient.enrich_with_model_info(archived, raw_model)
            self.db.update_online_info(sha256, archived)
            self._schedule_preview_prewarm(archived)
            logger.info("[Noctyra-MM] 从归档恢复 CivitAI 数据: %s", archived.get("model_name", ""))
            return True

        # CivArchive 兜底：CivitAI 确实没有，但 civarchive.com 可能有镜像
        if self.config.get("enable_civarchive_fallback", True):
            from . import civarchive
            ca_info = await civarchive.get_model_by_hash(sha256)
            if ca_info:
                self.db.update_online_info(sha256, ca_info)
                self._schedule_preview_prewarm(ca_info)
                return True

        return False

    async def _try_hf_match(self, sha256: str, filename: str, supplement: bool = False) -> bool:
        """尝试 HuggingFace 匹配

        Args:
            supplement: True 表示 CivitAI 已匹配，HF 只作为补充来源（不覆盖主字段）
        """
        candidates = await self.huggingface.match_by_filename(filename, sha256=sha256)
        if not candidates:
            # API 无结果，尝试从归档恢复
            if not supplement:
                archived = self.db.get_archived(sha256)
                if archived and archived.get("source") == "huggingface":
                    self.db.update_hf_info(sha256, archived)
                    logger.info("[Noctyra-MM] 从归档恢复 HuggingFace 数据: %s", archived.get("repo_id", ""))
                    return True
            return False

        best = candidates[0]
        repo_id = best["repo_id"]
        match_type = best.get("match_type", "filename")
        model_data = await self.huggingface.get_model_info(repo_id)
        if model_data:
            info = HuggingFaceClient.parse_model_info(model_data, repo_id)
            info["match_type"] = match_type
            # 抓取 README 作为描述；受限仓库读不到时保留空，前端根据 hf_gated 提示去接受协议
            if not info.get("model_description"):
                readme = await self.huggingface.get_readme(repo_id)
                if readme:
                    info["model_description"] = readme
            info["_raw_data"] = model_data
            self.db.update_hf_info(sha256, info)
            self._schedule_preview_prewarm(info)
            type_label = {"hash": "SHA256 精确", "filename": "文件名", "fuzzy": "模糊"}.get(match_type, match_type)
            action = "补充信息" if supplement else "匹配成功"
            logger.info("[Noctyra-MM] HuggingFace %s (%s): %s", action, type_label, repo_id)
            return True

        return False

    async def bind_huggingface(self, sha256: str, repo_url: str) -> bool:
        """手动绑定 HuggingFace repo"""
        model_data = await self.huggingface.get_repo_by_url(repo_url)
        if model_data:
            repo_id = model_data.get("id", "")
            info = HuggingFaceClient.parse_model_info(model_data, repo_id)
            if not info.get("model_description"):
                readme = await self.huggingface.get_readme(repo_id)
                if readme:
                    info["model_description"] = readme
            info["_raw_data"] = model_data
            self.db.update_online_info(sha256, info)
            logger.info("[Noctyra-MM] 手动绑定 HuggingFace 成功: %s -> %s", sha256[:10], repo_id)
            return True
        return False

    async def bind_civitai(self, sha256: str, model_url: str) -> bool:
        """手动绑定 CivitAI 模型"""
        match = re.search(r"models/(\d+)", model_url)
        if not match:
            logger.warning("[Noctyra-MM] 无法从 URL 提取模型 ID: %s", model_url)
            return False

        model_id = int(match.group(1))
        version_id = None
        ver_match = re.search(r"modelVersionId=(\d+)", model_url)
        if ver_match:
            version_id = int(ver_match.group(1))

        if version_id:
            data = await self.civitai.get_model_version(version_id)
        else:
            model_data = await self.civitai.get_model_info(model_id)
            if model_data:
                versions = model_data.get("modelVersions", [])
                data = versions[0] if versions else None
            else:
                data = None

        if data:
            info = CivitaiClient.parse_version_info(data)
            raw_data = {"version": data}
            if not version_id and model_data:
                raw_data["model"] = model_data
            info["_raw_data"] = raw_data
            self.db.update_online_info(sha256, info)
            logger.info("[Noctyra-MM] 手动绑定 CivitAI 成功: %s -> %s", sha256[:10], info.get("model_name"))
            return True
        return False

    # ========== CivitAI 下载 ==========

    async def fetch_civitai_versions(self, model_url: str) -> Optional[Dict]:
        """从 CivitAI URL 解析模型信息和版本列表"""
        match = re.search(r"models/(\d+)", model_url)
        if not match:
            return None

        model_id = int(match.group(1))
        model_data = await self.civitai.get_model_info(model_id)
        if not model_data:
            return None

        versions = []
        for v in model_data.get("modelVersions", []):
            files = v.get("files", [])
            # 优先选 type="Model" 的文件（避免误选 Training Data / Config / VAE）
            primary_file = next((f for f in files if (f.get("type") or "").lower() == "model"), None)
            if primary_file is None:
                primary_file = files[0] if files else {}
            images = v.get("images", [])
            preview = images[0].get("url", "") if images else ""

            versions.append({
                "version_id": v.get("id"),
                "version_name": v.get("name", ""),
                "base_model": v.get("baseModel", ""),
                "published_at": v.get("publishedAt", ""),
                "download_url": primary_file.get("downloadUrl", ""),
                "file_name": primary_file.get("name", ""),
                "file_size": primary_file.get("sizeKB", 0) * 1024,
                "preview_url": preview,
                "sha256": (primary_file.get("hashes") or {}).get("SHA256", ""),
            })

        return {
            "model_id": model_data.get("id"),
            "model_name": model_data.get("name", ""),
            "model_type": model_data.get("type", ""),
            "creator": (model_data.get("creator") or {}).get("username", ""),
            "tags": model_data.get("tags", []),
            "versions": versions,
        }

    async def fetch_hf_files(self, repo_url: str) -> Optional[Dict]:
        """从 HuggingFace repo URL 获取可下载的模型文件列表"""
        model_data = await self.huggingface.get_repo_by_url(repo_url)
        if not model_data:
            return None

        repo_id = model_data.get("id", "")
        files = await self.huggingface.list_repo_files(repo_id)

        model_exts = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")
        file_list = []
        for f in files:
            path = f.get("path", "")
            if not path.lower().endswith(model_exts):
                continue
            file_list.append({
                "file_name": path,
                "file_size": f.get("size", 0),
                "download_url": f"https://huggingface.co/{repo_id}/resolve/main/{path}",
            })

        info = HuggingFaceClient.parse_model_info(model_data, repo_id)
        return {
            "repo_id": repo_id,
            "model_name": info.get("model_name", repo_id),
            "base_model": info.get("base_model", "Unknown"),
            "author": info.get("author", ""),
            "tags": info.get("tags", []),
            "files": file_list,
        }

    async def download_civitai_model(self, download_url: str, save_dir: str,
                                      file_name: str, progress_callback=None) -> Optional[str]:
        """下载 CivitAI 模型文件（同步接口，兼容旧调用）"""
        if not os.path.isdir(save_dir):
            os.makedirs(save_dir, exist_ok=True)

        save_path = os.path.join(save_dir, file_name)
        if os.path.exists(save_path):
            logger.warning("[Noctyra-MM] 文件已存在: %s", save_path)
            return None

        success = await self.civitai.download_file(download_url, save_path, progress_callback)
        return save_path if success else None

    # ========== 下载（代理到 DownloadManager） ==========

    def start_download(self, download_id: str, download_url: str, save_dir: str,
                       file_name: str, progress_callback=None,
                       on_complete=None, version_id=None, preview_url: str = "",
                       source_url: str = "", expected_sha256: str = "", overwrite: bool = False) -> str:
        return self.downloader.start(download_id, download_url, save_dir, file_name,
                                     progress_callback=progress_callback,
                                     on_complete=on_complete,
                                     version_id=version_id,
                                     preview_url=preview_url,
                                     source_url=source_url,
                                     expected_sha256=expected_sha256,
                                     overwrite=overwrite)

    async def check_model_integrity(self, file_path: str) -> dict:
        """检测模型文件是否损坏（在线程池里读文件，不阻塞事件循环）。"""
        from .scanner import check_safetensors_integrity
        model = self.db.get_by_path(file_path)
        if not model:
            return {"success": False, "error": "模型不存在"}
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, check_safetensors_integrity, file_path)
        return {"success": True, "file_name": os.path.basename(file_path), **res}

    async def redownload_model(self, file_path: str) -> dict:
        """重新下载并覆盖损坏/丢失的模型文件（仅 CivitAI 已匹配的，按 version_id 取原文件）。"""
        model = self.db.get_by_path(file_path)
        if not model:
            return {"success": False, "error": "模型不存在"}
        version_id = model.get("civitai_version_id")
        if not version_id:
            return {"success": False, "error": "该模型没有 CivitAI 版本信息，无法自动重下（可手动绑定链接后再试）"}

        v = await self.civitai.get_model_version(int(version_id))
        if not v:
            return {"success": False, "error": "获取 CivitAI 版本信息失败（网络/代理/API key？）"}
        files = v.get("files", []) or []
        primary = next((f for f in files if (f.get("type") or "").lower() == "model"), None) or (files[0] if files else None)
        if not primary or not primary.get("downloadUrl"):
            return {"success": False, "error": "CivitAI 版本无可下载文件"}

        save_dir = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)  # 保持原文件名以覆盖那一份
        expected_sha = (primary.get("hashes") or {}).get("SHA256", "")

        from .websocket import get_progress_ws
        ws = get_progress_ws()
        import uuid as _uuid
        download_id = _uuid.uuid4().hex[:12]
        progress_cb = ws.make_download_progress_callback(download_id)

        async def on_done(dl):
            await ws.broadcast("download_progress", {
                "download_id": download_id, "status": dl["status"],
                "error": dl.get("error", ""), "file_name": file_name, "progress": dl.get("progress", 0),
            })

        self.start_download(download_id, primary["downloadUrl"], save_dir, file_name,
                            progress_cb, on_complete=on_done, version_id=int(version_id),
                            preview_url=model.get("preview_url", ""),
                            expected_sha256=expected_sha, overwrite=True)
        logger.info("[Noctyra-MM] 重新下载（覆盖）: %s -> %s", download_id, file_name)
        return {"success": True, "download_id": download_id, "file_name": file_name}

    def find_active_download(self, save_dir: str, file_name: str):
        return self.downloader.find_active_download(save_dir, file_name)

    def cancel_download(self, download_id: str) -> bool:
        return self.downloader.cancel(download_id)

    def get_downloads(self) -> list:
        return self.downloader.list()

    def remove_download(self, download_id: str) -> bool:
        return self.downloader.remove(download_id)

    def retry_download(self, download_id: str, progress_callback=None) -> bool:
        return self.downloader.retry(download_id, progress_callback=progress_callback)

    def pause_download(self, download_id: str) -> bool:
        return self.downloader.pause(download_id)

    def resume_download(self, download_id: str, progress_callback=None) -> bool:
        return self.downloader.resume(download_id, progress_callback=progress_callback)

    def redownload(self, download_id: str, progress_callback=None) -> bool:
        return self.downloader.redownload(download_id, progress_callback=progress_callback)

    def cleanup_downloads(self, statuses=None):
        return self.downloader.cleanup(statuses=statuses)

    # ========== 模型更新检查 ==========

    # 更新检查 24h TTL：跳过最近查过的模型，省 API（大库重复点"检查更新"时效果明显）
    _UPDATE_CHECK_TTL = 24 * 3600

    async def check_model_updates(self) -> List[Dict]:
        """检查所有已匹配模型（CivitAI + HuggingFace）是否有新版本"""
        # 占用 busy 标志：检查期间扫描/匹配/再次检查更新都会被干净拒绝，避免并发重复打 API、
        # 更新标记竞态。开头若已在检查则直接返回（路由层也已挡 is_busy，这里是二次防护）
        if self._is_checking_updates:
            return []
        self._is_checking_updates = True
        try:
            civ_updates, civ_checked = await self._check_civitai_updates()
            hf_updates, hf_checked = await self._check_hf_updates()
            updates = civ_updates + hf_updates

            updatable_paths = [u["file_path"] for u in updates]
            checked_paths = civ_checked + hf_checked
            # 只重置"本次检查过的"更新标记（24h 内跳过的保留旧标记），再记录检查时间
            self.db.set_update_available(updatable_paths, checked_paths=checked_paths)
            self.db.mark_update_checked(checked_paths, time.time())

            logger.info("[Noctyra-MM] 更新检查完成: %d 个模型有新版本", len(updates))
            return updates
        finally:
            self._is_checking_updates = False

    async def _maybe_auto_check_updates(self):
        """启动后台自动查更新（config.auto_check_updates 控制，默认开）。等首扫/匹配跑完再查，
        错开 API；24h TTL 内不重复、限流自动跳过；完成后广播让前端刷新可更新徽章。"""
        try:
            if not self.config.get("auto_check_updates", True):
                return
            await asyncio.sleep(20)
            # 等空闲：大库首扫可能 >20s，有界轮询避免"看到 busy 就永久放弃"；超时则本轮跳过
            for _ in range(60):  # 最多再等 ~5 分钟
                if not self.is_busy:
                    break
                await asyncio.sleep(5)
            else:
                logger.info("[Noctyra-MM] 启动自动查更新：长时间繁忙，本轮跳过")
                return
            await self.check_model_updates()
        except Exception as e:
            logger.warning("[Noctyra-MM] 启动自动查更新失败: %s", e)
        # 广播最新可更新数，让前端无需刷新即可更新徽章
        try:
            from .websocket import get_progress_ws
            cnt = self.db.get_stats().get("updatable", 0)
            await get_progress_ws().broadcast("updates_checked", {"updatable": cnt})
        except Exception:
            pass

    async def _check_civitai_updates(self):
        """返回 (updates, checked_paths)。checked_paths = 本次实际拿到响应的 file_path
        （24h 内已查过的会被 TTL 跳过，不在内；拿不到响应的也不算已查，下次重试）。"""
        civitai_models = self.db.get_civitai_models()
        if not civitai_models:
            return [], []

        now = time.time()
        model_id_map = {}
        for m in civitai_models:
            model_id_map.setdefault(m["civitai_model_id"], []).append(m)

        # 24h TTL：某 model_id 下所有本地记录都在 24h 内查过 → 跳过
        to_check = {
            mid: locals_ for mid, locals_ in model_id_map.items()
            if not all((m.get("last_update_check_at") or 0) > now - self._UPDATE_CHECK_TTL
                       for m in locals_)
        }
        if not to_check:
            logger.info("[Noctyra-MM] CivitAI 更新检查：全部模型 24h 内已查过，跳过")
            return [], []

        # 批量拉取（N 次 → N/100 次）；整体失败/部分缺失时对缺的逐个回退
        bulk = await self.civitai.get_models_bulk(list(to_check.keys()))
        # 批量整体失败且已限流：逐个回退也只会全部短路返回 None，还白睡 API_DELAY×N，直接早退
        if bulk is None and self.civitai.is_rate_limited():
            logger.warning("[Noctyra-MM] CivitAI 限流，跳过本轮更新检查，下次重试")
            return [], []

        updates = []
        checked_paths = []
        hide_ea = self.config.get("hide_early_access_updates", True)
        for model_id, local_models in to_check.items():
            model_data = bulk.get(model_id) if isinstance(bulk, dict) else None
            if model_data is None:
                model_data = await self.civitai.get_model_info(model_id)
                await asyncio.sleep(API_DELAY)
            if not model_data:
                continue  # 没拿到（网络/限流）→ 不标记已查，下次重试

            checked_paths.extend(m["file_path"] for m in local_models)
            all_versions = model_data.get("modelVersions", [])
            if not all_versions:
                continue

            if hide_ea:
                # 跳过仍处抢先体验期的版本，取最近的公开版本比较；全是抢先期则本轮不提醒
                latest = next((v for v in all_versions
                               if (v.get("availability") or "").lower() != "earlyaccess"), None)
                if latest is None:
                    continue
            else:
                latest = all_versions[0]
            latest_vid = latest.get("id")
            latest_files = latest.get("files", [])
            latest_file = latest_files[0] if latest_files else {}
            ignored_vids = set(self.db.list_ignored_versions(model_id))

            for local in local_models:
                current_vid = local.get("civitai_version_id")
                if latest_vid in ignored_vids:
                    continue
                # 用 > 而非 !=：CivitAI version id 单调递增，仅"更新的版本"才提醒。
                # 隐藏抢先体验时 latest 可能是更旧的公开版，本地若装着抢先版则 latest<current，不应误报
                if current_vid and latest_vid and latest_vid > current_vid:
                    updates.append({
                        "file_path": local["file_path"],
                        "file_name": local["file_name"],
                        "model_name": local.get("model_name", ""),
                        "source": "civitai",
                        "civitai_model_id": model_id,
                        "current_version_id": current_vid,
                        "current_version_name": local.get("version_name", ""),
                        "latest_version_id": latest_vid,
                        "latest_version_name": latest.get("name", ""),
                        "latest_base_model": latest.get("baseModel", ""),
                        "latest_download_url": latest_file.get("downloadUrl", ""),
                        "latest_file_name": latest_file.get("name", ""),
                        "latest_file_size": latest_file.get("sizeKB", 0) * 1024,
                        "source_url": _civitai_model_url(model_id, latest_vid),
                    })

        return updates, checked_paths

    async def _check_hf_updates(self):
        """检查 HuggingFace 匹配模型的 repo 是否有更新（按 lastModified 比较）。
        返回 (updates, checked_paths)。HF 不做 TTL，但回报检查过的 path 供清更新标记。"""
        hf_models = self.db.get_hf_models()
        if not hf_models:
            return [], []

        repo_map = {}
        for m in hf_models:
            rid = m["hf_repo_id"]
            repo_map.setdefault(rid, []).append(m)

        updates = []
        checked_paths = []
        for repo_id, local_models in repo_map.items():
            model_data = await self.huggingface.get_model_info(repo_id)
            if not model_data:
                await asyncio.sleep(API_DELAY)
                continue

            checked_paths.extend(m["file_path"] for m in local_models)
            latest_modified = model_data.get("lastModified", "")
            if not latest_modified:
                await asyncio.sleep(API_DELAY)
                continue

            for local in local_models:
                current = local.get("hf_last_modified", "")
                if current and latest_modified and latest_modified > current:
                    updates.append({
                        "file_path": local["file_path"],
                        "file_name": local["file_name"],
                        "model_name": local.get("model_name", "") or repo_id,
                        "source": "huggingface",
                        "hf_repo_id": repo_id,
                        "current_modified": current,
                        "latest_modified": latest_modified,
                        "latest_download_url": f"https://huggingface.co/{repo_id}/resolve/main/{local['file_name']}",
                        "latest_file_name": local["file_name"],
                        "source_url": f"https://huggingface.co/{repo_id}",
                    })

            await asyncio.sleep(API_DELAY)

        return updates, checked_paths

    # ========== 单个模型匹配 ==========

    async def match_single(self, file_path: str, source: str = "") -> Dict[str, bool]:
        """对单个模型进行在线匹配

        Args:
            source: "" / "both" 同时匹配两个源；"civitai" 只匹配 CivitAI；"huggingface" 只匹配 HF

        Returns:
            {"civitai": bool, "huggingface": bool}
        """
        model = self.db.get_by_path(file_path)
        if not model:
            logger.warning("[Noctyra-MM] 匹配单个模型失败：未找到 %s", file_path)
            return {"civitai": False, "huggingface": False}

        sha256 = model.get("sha256", "")
        filename = model.get("file_name", "")

        if not sha256:
            logger.warning("[Noctyra-MM] 匹配单个模型失败：无 SHA256 %s", filename)
            return {"civitai": False, "huggingface": False}

        source = (source or "").strip().lower()
        want_civitai = source in ("", "both", "civitai")
        want_hf = source in ("", "both", "huggingface")

        civitai_ok = False
        hf_ok = False

        if want_civitai:
            civitai_ok = await self._try_civitai_match(sha256)
            if want_hf:
                await asyncio.sleep(API_DELAY)

        if want_hf:
            # 只匹配 HF 时，以主来源身份写入（不做 supplement 降级）
            supplement = civitai_ok if want_civitai else False
            hf_ok = await self._try_hf_match(sha256, filename, supplement=supplement)

        logger.info("[Noctyra-MM] 单个匹配完成 [%s]: %s -> CivitAI=%s, HF=%s",
                    source or "both", filename, civitai_ok, hf_ok)
        return {"civitai": civitai_ok, "huggingface": hf_ok}

    async def refresh_base_models(self, progress_callback: Callable = None) -> dict:
        """批量刷新所有 CivitAI 已匹配条目的 base_model 字段。
        只动 base_model 列，保留 creator / description / stats 等。
        """
        targets = self.db.get_civitai_refresh_targets()
        total = len(targets)
        logger.info("[Noctyra-MM] 开始刷新 base_model，共 %d 个条目", total)

        updated = 0
        unchanged = 0
        failed = 0

        for i, row in enumerate(targets):
            version_id = row.get("civitai_version_id")
            file_path = row.get("file_path")
            old = row.get("base_model") or ""
            if progress_callback:
                await progress_callback("refresh_base_models", i + 1, total, os.path.basename(file_path))

            if not version_id:
                continue
            try:
                data = await self.civitai.get_model_version(int(version_id))
            except Exception as e:
                logger.warning("[Noctyra-MM] 刷新失败 version_id=%s: %s", version_id, e)
                failed += 1
                await asyncio.sleep(API_DELAY)
                continue

            if not data:
                failed += 1
                await asyncio.sleep(API_DELAY)
                continue

            new_base = (data.get("baseModel") or "").strip()
            if not new_base:
                await asyncio.sleep(API_DELAY)
                continue

            # 严格用 CivitAI 原值（只归一规范名，不再用文件名细化）
            from .base_models import normalize_base_model
            refined = normalize_base_model(new_base)

            if refined == old:
                unchanged += 1
            else:
                self.db.update_base_model_only(file_path, refined)
                updated += 1
                logger.info("[Noctyra-MM] base_model 刷新: %s  %s → %s", os.path.basename(file_path), old, refined)

            await asyncio.sleep(API_DELAY)

        result = {"total": total, "updated": updated, "unchanged": unchanged, "failed": failed}
        logger.info("[Noctyra-MM] base_model 刷新完成: %s", result)
        return result

    def get_base_model_stats(self) -> list:
        """代理到 db，供 UI 只读展示"""
        return self.db.get_base_model_stats()

    # ========== 重建缓存 ==========

    async def rebuild_cache(self, progress_callback: Callable = None) -> int:
        """清空数据库并重新扫描所有模型"""
        if self._is_scanning:
            logger.warning("[Noctyra-MM] 扫描已在进行中，无法重建")
            return 0
        self._is_scanning = True
        try:
            async with self._scan_lock:
                logger.info("[Noctyra-MM] 开始重建缓存...")
                self.db.rebuild()
                return await self._do_scan(progress_callback)
        finally:
            self._is_scanning = False

    # ========== 批量操作 ==========

    def batch_delete(self, file_paths: list, delete_files: bool = False) -> dict:
        """批量删除模型"""
        deleted = 0
        failed = 0
        for fp in file_paths:
            if self.delete_model(fp, delete_file=delete_files):
                deleted += 1
            else:
                failed += 1
        logger.info("[Noctyra-MM] 批量删除完成: 成功 %d, 失败 %d", deleted, failed)
        return {"deleted": deleted, "failed": failed}

    async def batch_refresh(self, file_paths: list) -> dict:
        """批量重新匹配指定模型"""
        refreshed = 0
        for fp in file_paths:
            result = await self.match_single(fp)
            if result["civitai"] or result["huggingface"]:
                refreshed += 1
            await asyncio.sleep(API_DELAY)
        logger.info("[Noctyra-MM] 批量刷新完成: %d/%d 匹配成功", refreshed, len(file_paths))
        return {"refreshed": refreshed, "total": len(file_paths)}

    def batch_add_tags(self, file_paths: list, tags: list) -> dict:
        """批量给模型打标签（并集：保留已有 + 追加）"""
        updated = 0
        failed = 0
        for fp in file_paths:
            try:
                self.db.add_tags(fp, tags)
                updated += 1
            except Exception as e:
                logger.warning("[Noctyra-MM] batch_add_tags 失败 %s: %s", fp, e)
                failed += 1
        logger.info("[Noctyra-MM] 批量打标签完成: %d/%d 成功", updated, len(file_paths))
        return {"updated": updated, "failed": failed, "total": len(file_paths)}

    def batch_set_base_model(self, file_paths: list, base_model: str) -> dict:
        """批量设 base_model（用于 CivitAI 未识别但用户已知的场景）"""
        bm = (base_model or "").strip()
        if not bm:
            return {"updated": 0, "failed": len(file_paths), "error": "base_model 不能为空"}
        updated = 0
        failed = 0
        for fp in file_paths:
            try:
                self.db.update_base_model_only(fp, bm)
                updated += 1
            except Exception as e:
                logger.warning("[Noctyra-MM] batch_set_base_model 失败 %s: %s", fp, e)
                failed += 1
        logger.info("[Noctyra-MM] 批量设 base_model=%s 完成: %d/%d 成功", bm, updated, len(file_paths))
        return {"updated": updated, "failed": failed, "total": len(file_paths)}

    def batch_move(self, file_paths: list, target_folder: str) -> dict:
        """批量移动模型到指定文件夹（各自根目录相对路径）"""
        moved = 0
        failed_details = []
        for fp in file_paths:
            result = self.move_model(fp, target_folder)
            if result.get("success"):
                moved += 1
            else:
                failed_details.append({"file_path": fp, "error": result.get("error", "")})
        logger.info("[Noctyra-MM] 批量移动完成: %d/%d 成功", moved, len(file_paths))
        return {"moved": moved, "failed": len(failed_details), "total": len(file_paths),
                "failed_details": failed_details}

    # ========== 重复检测 ==========

    def get_duplicates(self) -> list:
        return self.db.get_duplicates()

    def resolve_preview_owners(self, urls) -> dict:
        """把一组预览 URL 反查成 {url: 模型名}。反查表缓存 30s（库不常变），
        避免任务中心每 2s 轮询都重建整库映射。"""
        if not urls:
            return {}
        now = time.monotonic()
        cached_map = getattr(self, "_preview_owner_map", None)
        ts = getattr(self, "_preview_owner_ts", 0.0)
        if cached_map is None or (now - ts) > 30:
            cached_map = self.db.get_preview_url_owners()
            self._preview_owner_map = cached_map
            self._preview_owner_ts = now
        return {u: cached_map.get(u) for u in urls}   # 值为 {name, file_path} 或 None

    # ========== 查询 ==========

    def get_models_paginated(self, page: int = 1, page_size: int = 40,
                              sort_by: str = "file_name", sort_dir: str = "",
                              **filters) -> Tuple[List[dict], int]:
        """分页获取模型列表"""
        return self.db.get_all(filters=filters, sort_by=sort_by, sort_dir=sort_dir,
                               page=page, page_size=page_size)

    def get_model(self, identifier: str) -> Optional[dict]:
        """获取单个模型信息（支持 sha256 或 file_path）"""
        # 尝试作为 sha256
        if len(identifier) == 64 and all(c in '0123456789abcdef' for c in identifier.lower()):
            return self.db.get_by_hash(identifier)
        # 否则作为 file_path
        return self.db.get_by_path(identifier)

    def get_local_versions(self, civitai_model_id: int, exclude_path: str = "") -> List[dict]:
        return self.db.get_local_versions(civitai_model_id, exclude_path)

    def get_folders(self) -> List[dict]:
        return self.db.get_folders()

    def get_tags(self, limit: int = 50) -> List[dict]:
        return self.db.get_tags(limit)

    def get_base_models(self) -> List[str]:
        return self.db.get_base_models()

    def get_stats(self, source: str = "") -> dict:
        stats = self.db.get_stats(source)
        stats["is_scanning"] = self._is_scanning
        stats["is_matching"] = self._is_matching
        stats.update(self.db.get_archive_stats())
        return stats

    def toggle_favorite(self, file_path: str, favorite: bool):
        self.db.update_favorite(file_path, favorite)

    def update_notes(self, file_path: str, notes: str):
        self.db.update_notes(file_path, notes)

    def update_custom_info(self, identifier: str, fields: dict) -> dict:
        """用户在"自定义"Tab 手填的信息回写 DB。

        - trained_words 可以是 list 或逗号分隔字符串
        - 当模型原 source 是 '' / 'local' 时，自动改为 'custom'；civitai/huggingface 保留
        """
        model = self.get_model(identifier)
        if not model:
            return {"success": False, "error": "模型不存在"}

        # 字段填了非空 → 写入 + 锁定（匹配/刷新不再覆盖）；留空 → 不写(保留现值)+ 解锁(交回在线值)。
        # 既能"手改后永久生效"，又能"清空=恢复在线值"，还顺带避免把空串存进 base_model 等列。
        data = {}
        lock = []
        unlock = []
        for key in ("model_name", "base_model", "creator", "version_name",
                    "model_description", "preview_url"):
            if key not in fields:
                continue
            value = fields[key]
            if isinstance(value, str):
                value = value.strip()
            if value:
                data[key] = value
                lock.append(key)
            else:
                unlock.append(key)

        if "trained_words" in fields:
            raw = fields["trained_words"]
            if isinstance(raw, list):
                words = [str(w).strip() for w in raw if str(w).strip()]
            else:
                words = [w.strip() for w in str(raw).split(",") if w.strip()]
            if words:
                data["trained_words"] = words
                lock.append("trained_words")
            else:
                unlock.append("trained_words")

        # user_model_type 是独立列、匹配本就不动它，无需锁；空=自动识别，照写（允许清除）
        if "user_model_type" in fields:
            data["user_model_type"] = (fields.get("user_model_type") or "").strip()

        # source 升级：原来是 local / 空时，保存自定义信息后标记为 custom
        current_source = (model.get("source") or "").strip().lower()
        if current_source in ("", "local"):
            data["source"] = "custom"

        self.db.update_custom_info(model["file_path"], data, lock=lock, unlock=unlock)
        return {"success": True}

    def save_uploaded_preview(self, identifier: str, raw_bytes: bytes, ext: str) -> dict:
        """把用户上传的预览图保存为模型的 sidecar 文件（{stem}.preview.{ext}）。"""
        model = self.get_model(identifier)
        if not model:
            return {"success": False, "error": "模型不存在"}

        file_path = model["file_path"]
        if not os.path.isfile(file_path):
            return {"success": False, "error": "模型文件不存在于磁盘"}

        ext = (ext or "").lstrip(".").lower()
        if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
            return {"success": False, "error": f"不支持的图片格式: {ext}"}

        model_dir = os.path.dirname(file_path)
        stem = os.path.splitext(os.path.basename(file_path))[0]

        # 先清理同 stem 下已有的预览 sidecar（避免新旧共存）
        for old_ext in ("png", "jpg", "jpeg", "webp", "gif"):
            for pattern in (f"{stem}.preview.{old_ext}", f"{stem}.{old_ext}"):
                old_path = os.path.join(model_dir, pattern)
                if os.path.isfile(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

        target_path = os.path.join(model_dir, f"{stem}.preview.{ext}")
        try:
            with open(target_path, "wb") as f:
                f.write(raw_bytes)
        except OSError as e:
            return {"success": False, "error": f"保存预览图失败: {e}"}

        # preview_url 用 sidecar://{sha256} 形式；前端通过 /api/noctyra/local-preview?id=... 拉取
        sha256 = (model.get("sha256") or "").lower()
        if sha256:
            self.db.update_custom_info(file_path, {"preview_url": f"sidecar://{sha256}"})
        else:
            # 模型还没算 hash，用 file_path 作为 id（URL 编码）
            import urllib.parse
            self.db.update_custom_info(
                file_path,
                {"preview_url": f"sidecar://path:{urllib.parse.quote(file_path, safe='')}"}
            )

        return {"success": True, "preview_path": target_path}

    def resolve_sidecar_preview(self, identifier: str) -> Optional[str]:
        """根据 sidecar:// URL 里的 identifier 找到对应的预览文件绝对路径。"""
        if identifier.startswith("path:"):
            import urllib.parse
            file_path = urllib.parse.unquote(identifier[5:])
            model = self.db.get_by_path(file_path)
        else:
            model = self.db.get_by_hash(identifier)
        if not model:
            return None

        file_path = model.get("file_path", "")
        if not file_path or not os.path.isfile(file_path):
            return None

        model_dir = os.path.dirname(file_path)
        stem = os.path.splitext(os.path.basename(file_path))[0]
        # 优先 .preview.{ext}，兜底 {stem}.{ext}
        for pattern_ext in ("png", "jpg", "jpeg", "webp", "gif"):
            candidate = os.path.join(model_dir, f"{stem}.preview.{pattern_ext}")
            if os.path.isfile(candidate):
                return candidate
        for pattern_ext in ("png", "jpg", "jpeg", "webp", "gif"):
            candidate = os.path.join(model_dir, f"{stem}.{pattern_ext}")
            if os.path.isfile(candidate):
                return candidate
        return None

    # ==================== 模型导入（外部文件搬入 ComfyUI/models/Unknown/） ====================

    _IMPORT_TARGET_SUBDIR = "Unknown"
    # 写盘分块大小；localhost 多 GB 文件也能平稳走完
    _IMPORT_CHUNK = 1024 * 1024

    def _import_target_dir(self) -> str:
        """<ComfyUI>/models/Unknown/，不存在则创建"""
        target = os.path.join(self.config.comfyui_models_dir, self._IMPORT_TARGET_SUBDIR)
        os.makedirs(target, exist_ok=True)
        return target

    def _ensure_import_root_in_config(self, import_dir: str):
        """Unknown目录不在 model_roots 时自动加入，保证后续扫描覆盖"""
        import_norm = os.path.normpath(import_dir)
        roots = list(self.config.model_roots)
        for r in roots:
            if os.path.normpath(r) == import_norm:
                return
        roots.append(import_dir)
        self.config.set("model_roots", roots)
        self.config.save()
        logger.info("[Noctyra-MM] 自动加入扫描根: %s", import_dir)

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """剥离路径、去掉 Windows 非法字符"""
        name = os.path.basename((name or "").strip())
        for ch in '<>:"|?*':
            name = name.replace(ch, "_")
        return name

    def _resolve_import_target(self, filename: str) -> str:
        """返回最终落盘路径，若重名自动 _1 _2 后缀"""
        target_dir = self._import_target_dir()
        safe = self._sanitize_filename(filename)
        target = os.path.join(target_dir, safe)
        if not os.path.exists(target):
            return target
        stem, ext = os.path.splitext(safe)
        i = 1
        while True:
            candidate = os.path.join(target_dir, f"{stem}_{i}{ext}")
            if not os.path.exists(candidate):
                return candidate
            i += 1

    def _register_imported_file(self, file_path: str) -> bool:
        """单个文件入库：读元数据 → upsert → 计算 hash"""
        from .scanner import read_safetensors_metadata, extract_trained_words
        try:
            stat = os.stat(file_path)
        except OSError:
            return False

        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()

        # folder 取对应 model_root 的 basename；找不到就用 "Unknown"
        folder = self._IMPORT_TARGET_SUBDIR
        file_norm = os.path.normpath(file_path)
        for root in self.config.model_roots:
            root_norm = os.path.normpath(root)
            parent = os.path.dirname(file_norm)
            if parent == root_norm or parent.startswith(root_norm + os.sep):
                rel = os.path.relpath(parent, root_norm)
                if rel == ".":
                    folder = os.path.basename(root_norm)
                else:
                    folder = os.path.basename(root_norm) + "/" + rel.replace("\\", "/")
                break

        base_model = "Unknown"
        trained_words = []
        if ext == ".safetensors":
            metadata = read_safetensors_metadata(file_path)
            if metadata:
                # base_model 不从 safetensors 头/文件名推断（学 Lora-Manager）：留 Unknown，
                # 之后匹配 CivitAI 才给权威值；trained_words 仍读。
                trained_words = extract_trained_words(metadata)

        self.db.upsert_models_batch([{
            "file_path": file_path,
            "file_name": filename,
            "file_ext": ext,
            "file_size": stat.st_size,
            "modified": stat.st_mtime,
            "sha256": "",
            "base_model": base_model,
            "trained_words": trained_words,
            "model_type": "other",
            "folder": folder,
        }])
        return True

    async def import_from_path(self, src_path: str, move: bool = False) -> dict:
        """本地路径导入：shutil.move（同盘秒级）或 copy2（跨盘走字节拷贝）"""
        import shutil
        if not src_path or not os.path.isfile(src_path):
            return {"success": False, "error": f"源文件不存在: {src_path}"}

        ext = os.path.splitext(src_path)[1].lower()
        if ext not in self.config.scan_extensions:
            return {"success": False, "error": f"不支持的文件类型: {ext}"}

        target_path = self._resolve_import_target(os.path.basename(src_path))
        loop = asyncio.get_running_loop()
        try:
            if move:
                await loop.run_in_executor(None, shutil.move, src_path, target_path)
            else:
                await loop.run_in_executor(None, shutil.copy2, src_path, target_path)
        except OSError as e:
            # 中途失败可能在目标留下不完整文件就清理；但 move 跨盘是 copy+unlink，
            # 若 unlink 失败则 target 是完整副本（不能删）——故只删与源大小不一致的残留
            try:
                if os.path.isfile(target_path):
                    src_size = os.path.getsize(src_path) if os.path.isfile(src_path) else -1
                    if os.path.getsize(target_path) != src_size:
                        os.remove(target_path)
            except OSError:
                pass
            return {"success": False, "error": f"{'移动' if move else '复制'}失败: {e}"}

        return await self._finalize_import(target_path)

    async def import_from_multipart(self, filename: str, part) -> dict:
        """aiohttp 上传流式写盘。part 是 MultipartReader 的当前 part"""
        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in self.config.scan_extensions:
            return {"success": False, "error": f"不支持的文件类型: {ext}"}

        target_path = self._resolve_import_target(filename)
        try:
            loop = asyncio.get_running_loop()
            # aiohttp 的 read_chunk 已经是异步；写盘动作用 executor 避免阻塞事件循环
            fh = await loop.run_in_executor(None, open, target_path, "wb")
            try:
                while True:
                    chunk = await part.read_chunk(self._IMPORT_CHUNK)
                    if not chunk:
                        break
                    await loop.run_in_executor(None, fh.write, chunk)
            finally:
                await loop.run_in_executor(None, fh.close)
        except Exception as e:
            if os.path.isfile(target_path):
                try:
                    os.remove(target_path)
                except OSError:
                    pass
            return {"success": False, "error": f"写入失败: {e}"}

        return await self._finalize_import(target_path)

    async def _finalize_import(self, target_path: str) -> dict:
        """落盘完成后的收尾：入库 + 异步 hash + 确保扫描根"""
        self._ensure_import_root_in_config(os.path.dirname(target_path))
        if not self._register_imported_file(target_path):
            return {"success": False, "error": "入库失败"}

        loop = asyncio.get_running_loop()
        sha256 = await loop.run_in_executor(get_io_executor(), compute_sha256, target_path)
        if sha256:
            self.db.update_hash(target_path, sha256)

        return {
            "success": True,
            "file_path": target_path,
            "file_name": os.path.basename(target_path),
            "sha256": sha256,
        }

    def delete_model(self, file_path: str, delete_file: bool = False) -> bool:
        """删除模型记录，可选删除磁盘文件"""
        if delete_file and os.path.isfile(file_path):
            try:
                os.remove(file_path)
                logger.info("[Noctyra-MM] 已删除模型文件: %s", file_path)
                # 删空后顺手清理变空的父目录（复用整理的清理：上溯到模型根、不删根本身）
                self._cleanup_empty_dirs({os.path.dirname(file_path)})
            except OSError as e:
                logger.error("[Noctyra-MM] 删除文件失败: %s", e)
                return False
        return self.db.delete_model(file_path)

    @staticmethod
    def _path_within(path: str, root: str) -> bool:
        """path 是否等于 root 或在其内部（按规范化绝对路径，大小写不敏感）。"""
        try:
            p = os.path.normcase(os.path.abspath(path))
            r = os.path.normcase(os.path.abspath(root))
            return p == r or p.startswith(r + os.sep)
        except Exception:
            return False

    def _archive_path_for(self, file_path: str):
        """模型文件在存档夹里的规范目标路径（镜像 模型根名/相对路径，便于按文件夹上传网盘）。
        存档夹落在任一模型扫描根内 → 返回 None（拒绝软删除，否则扫描会把存档当在库模型重新收录）。"""
        archive_dir = os.path.abspath(self.config.archive_dir)
        for root in self.config.model_roots:
            if self._path_within(archive_dir, root):
                return None
        best = None  # 选最长前缀匹配的模型根，保留 类型/子目录 层级
        for root in self.config.model_roots:
            if self._path_within(file_path, root) and (best is None or len(root) > len(best)):
                best = root
        if best:
            rel = os.path.relpath(file_path, best)
            return os.path.join(archive_dir, os.path.basename(os.path.normpath(best)), rel)
        return os.path.join(archive_dir, os.path.basename(file_path))

    @staticmethod
    def _uniquify_path(dest: str) -> str:
        """目标已存在时追加 _1/_2…，避免覆盖（_safe_move 要求 dst 不存在）。"""
        if not os.path.exists(dest):
            return dest
        base, ext = os.path.splitext(dest)
        i = 1
        while os.path.exists(f"{base}_{i}{ext}"):
            i += 1
        return f"{base}_{i}{ext}"

    def soft_delete_model(self, file_path: str) -> bool:
        """软删除：把模型文件移到存档夹（保留 DB 记录）。存档夹便于你打包上传网盘、用完自行清理；
        下次把文件放回任意模型目录、扫描即按 sha256 自动归位。存档夹若误设在模型根内则拒绝（防扫描回收）。"""
        archived = ""
        if os.path.isfile(file_path):
            canon = self._archive_path_for(file_path)
            if canon is None:
                logger.error("[Noctyra-MM] 存档夹位于模型扫描根内，已取消软删除（请改存档目录）：%s",
                             self.config.archive_dir)
                return False
            dest = self._uniquify_path(canon)
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                _safe_move(file_path, dest)
                logger.info("[Noctyra-MM] 已存档模型文件: %s -> %s", file_path, dest)
                self._cleanup_empty_dirs({os.path.dirname(file_path)})
                archived = dest  # 记下实际存档位置（含撞名 _N），供精确恢复
            except OSError as e:
                logger.error("[Noctyra-MM] 存档移动失败: %s", e)
                return False
        return self.db.soft_delete_model(file_path, archived)

    def restore_model(self, file_path: str) -> bool:
        """恢复存档：① 文件已在原位（你手动放回/下回到同路径）→ 直接取消"已删除"标记；
        ② 文件还在存档夹 → 移回原位再取消标记。两者都不在 → False（请把文件放回任意模型目录，
        扫描会按 sha256 自动归位）。避免把文件仍缺失的记录恢复成正常态（否则扫描 remove_missing 会再清）。"""
        if os.path.isfile(file_path):
            return self.db.restore_model(file_path)
        # 优先用存档时记下的精确位置（含撞名 _N）；旧记录没记则回退到推导路径
        for src in (self.db.get_archived_path(file_path), self._archive_path_for(file_path)):
            if src and os.path.isfile(src) and not os.path.exists(file_path):
                try:
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    _safe_move(src, file_path)
                    logger.info("[Noctyra-MM] 已从存档夹恢复: %s -> %s", src, file_path)
                except OSError as e:
                    logger.error("[Noctyra-MM] 从存档恢复失败: %s", e)
                    return False
                return self.db.restore_model(file_path)
        return False

    @property
    def is_busy(self) -> bool:
        return (self._is_scanning or self._is_matching
                or self._is_checking_updates
                or self._mutating_depth > 0)

    @contextmanager
    def exclusive_op(self):
        """轻量互斥标记：整理/移动/删除/单个匹配/导入等"会改文件或元数据"的操作占用 is_busy，
        与扫描/匹配/检查更新互斥。防止扫描的 remove_missing 误删被并发移动的模型、重复打 API
        或对同一模型并发写元数据。用计数防嵌套误清。调用方须先判 is_busy 再进入。"""
        self._mutating_depth += 1
        try:
            yield
        finally:
            self._mutating_depth = max(0, self._mutating_depth - 1)

    # ==================== 预缓存 ====================

    def _collect_preview_urls(self) -> List[str]:
        """遍历 DB 收集所有需要预缓存的图片 URL（去重，已解析相对路径）"""
        urls = set()
        for row in self.db.get_all_image_sources():
            pu = row.get("preview_url")
            if pu:
                urls.add(pu)
            av = row.get("creator_avatar")
            if av:
                urls.add(av)
            for img in row.get("preview_images") or []:
                if isinstance(img, dict):
                    u = img.get("url") or ""
                    if u:
                        urls.add(u)

            # 刻意不再抓"模型描述 HTML 里嵌的 <img> / markdown 图"——那些多是
            # Discord/imgur/徽章/作者外链，经常过期或限流，预缓存只会刷一堆无意义失败，
            # 也不是真正的预览图。需要看时浏览器打开详情会按需直载，不必预热。
        # html.unescape 兜底（preview_url/preview_images 理论上已是干净 URL，幂等无害）
        return [u for u in (html.unescape(x) for x in urls if isinstance(x, str))
                if u.startswith(("http://", "https://"))]

    async def prewarm_previews(self) -> Dict[str, int]:
        """把所有模型引用的预览图加入【同一个】后台预热队列，立即返回状态。

        实际下载交给 preview_cache 的后台 worker 统一处理（和浏览时按需预热同一套），
        不再单独起一套 gather 下载——避免"两套并行 + 计数打架 + 假完成"。
        返回 {total, cached, dead, queued}：已缓存/死链/本次新入队，如实反映。
        """
        from .routes import _get_preview_cache
        cache = _get_preview_cache()
        urls = self._collect_preview_urls()
        status = cache.check_urls_status(urls)     # {total, cached, dead, missing}
        queued = cache.schedule_prewarm(urls)      # 进同一队列；自动去重已缓存/死链/在队列
        logger.info(
            "[Noctyra-MM] 预缓存入队：共 %d，已缓存 %d，死链 %d，新入队 %d（后台 worker 下载）",
            status["total"], status["cached"], status["dead"], queued,
        )
        return {
            "total": status["total"],
            "cached": status["cached"],
            "dead": status["dead"],
            "queued": queued,
        }
