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
下载任务管理器 — 并发下载 + 下载后自动入库与匹配。

持有对 ModelManager 的弱引用（构造时传入），以便：
  - 下载完成后调用 manager._try_civitai_match / _try_hf_match
  - 共用 manager.db / manager.civitai / manager._infer_model_type
被 manager.py 组合持有，并通过 manager.start_download 等代理方法对外暴露。
"""

import asyncio
import hashlib
import logging
import os
import time
from typing import Dict, Optional

from .civitai import CivitaiClient
from .scanner import compute_sha256, read_safetensors_metadata, extract_trained_words

logger = logging.getLogger("noctyra.downloader")


def _compute_folder_label(model_roots, save_path: str, save_dir: str) -> str:
    """计算入库的 folder 字段，与 scanner 的输出格式保持一致。

    优先按"文件落在哪个 model_root 里"反推前缀（root basename + 相对路径），
    保证下载入库产生的 folder 与后续扫描结果完全一致，避免侧栏分叉。
    文件不在任何配置根内时（比如临时目录）回退到 save_dir 的 basename 逻辑。
    """
    sp_nc = os.path.normcase(os.path.normpath(save_path))
    current_root = None
    for r in model_roots or []:
        r_nc = os.path.normcase(os.path.normpath(r))
        if sp_nc.startswith(r_nc + os.sep):
            current_root = r
            break

    if current_root:
        folder_rel = os.path.relpath(os.path.dirname(save_path), current_root)
        if folder_rel == ".":
            return os.path.basename(current_root)
        return os.path.basename(current_root) + "/" + folder_rel.replace("\\", "/")

    # 兜底：和旧逻辑一样
    folder_rel = os.path.relpath(os.path.dirname(save_path), save_dir)
    if folder_rel == ".":
        return os.path.basename(save_dir)
    return os.path.basename(save_dir) + "/" + folder_rel.replace("\\", "/")


def _resolve_filename_conflict(save_dir: str, file_name: str,
                               download_url: str = "",
                               version_id: Optional[int] = None) -> str:
    """检测目标目录下是否已有同名文件，冲突时追加短后缀返回新文件名。

    后缀来源优先级：
      1. `_v{version_id}`（CivitAI 版本号，稳定可读）
      2. `_{sha1(url)[:8]}`（用下载 URL 的 SHA1 前 8 字符）
      3. `_1` / `_2` / …（前两种都拿不到时退化为数字计数）
    """
    target = os.path.join(save_dir, file_name)
    if not os.path.exists(target):
        return file_name

    stem, ext = os.path.splitext(file_name)

    candidates = []
    if version_id:
        candidates.append(f"_v{version_id}")
    if download_url:
        url_hash = hashlib.sha1(download_url.encode("utf-8")).hexdigest()[:8]
        candidates.append(f"_{url_hash}")

    for suffix in candidates:
        candidate = f"{stem}{suffix}{ext}"
        if not os.path.exists(os.path.join(save_dir, candidate)):
            logger.info("[Noctyra-MM] 文件名冲突，已重命名: %s → %s", file_name, candidate)
            return candidate

    # 两种稳定后缀都已占用，落到数字计数
    i = 1
    while True:
        candidate = f"{stem}_{i}{ext}"
        if not os.path.exists(os.path.join(save_dir, candidate)):
            logger.info("[Noctyra-MM] 文件名冲突，已重命名: %s → %s", file_name, candidate)
            return candidate
        i += 1

# 单次下载之间留一点 API 间隔，避免被限流
_API_DELAY = 0.5


class DownloadManager:
    """下载任务管理器 — 绑定到某个 ModelManager 实例"""

    def __init__(self, manager, max_concurrent: int = 3):
        self._manager = manager
        self._downloads: Dict[str, dict] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # 启动时从 DB 恢复未完成的任务条目（状态 → interrupted），方便用户看到并手动续传
        self._restore_from_db()

    def _restore_from_db(self):
        """从 DB 读取 pending_only 的任务，写入内存列表（状态为 interrupted），
        让用户能在下载抽屉里看到"上次被打断的任务"。
        不会自动重启下载——.tmp 文件已有则下次用户点重试时自动续传。"""
        try:
            # 把所有进行中的先标成 interrupted（如果进程上次是干脆 kill 掉没走 finally）
            self._manager.db.mark_downloads_interrupted()
            rows = self._manager.db.list_download_records(pending_only=True)
        except Exception as e:
            logger.debug("[Noctyra-MM] 下载队列恢复失败: %s", e)
            return
        for row in rows:
            self._downloads[row["id"]] = {
                "id": row["id"],
                "file_name": row["file_name"],
                "save_dir": row["save_dir"],
                "save_path": os.path.join(row["save_dir"], row["file_name"]),
                "download_url": row["download_url"],
                "status": row.get("status", "interrupted"),
                "downloaded": row.get("downloaded", 0),
                "total": row.get("total", 0),
                "speed": 0,
                "eta": 0,
                "progress": row.get("progress", 0),
                "error": row.get("error", ""),
                "started_at": row.get("started_at", 0),
                "task": None,
                "on_complete": None,
                "version_id": row.get("version_id"),
                "preview_url": row.get("preview_url", ""),
                "source_url": row.get("source_url", ""),
            }
        if rows:
            logger.info("[Noctyra-MM] 恢复了 %d 个中断的下载任务（标记为 interrupted）", len(rows))

    def _persist(self, dl: dict):
        """把当前 dl 状态持久化到 DB。调用方传的是内存 _downloads[id] 的 dict。"""
        try:
            self._manager.db.save_download_record({
                "id": dl["id"],
                "download_url": dl["download_url"],
                "save_dir": dl["save_dir"],
                "file_name": dl["file_name"],
                "version_id": dl.get("version_id"),
                "preview_url": dl.get("preview_url", ""),
                "status": dl["status"],
                "downloaded": dl.get("downloaded", 0),
                "total": dl.get("total", 0),
                "progress": dl.get("progress", 0),
                "error": dl.get("error", ""),
                "started_at": dl.get("started_at", time.time()),
            })
        except Exception as e:
            # 持久化失败不影响主流程（只是重启后恢复不了）
            logger.debug("[Noctyra-MM] 下载状态持久化失败 %s: %s", dl.get("id"), e)

    # ========== 对外 API ==========

    def start(self, download_id: str, download_url: str, save_dir: str,
              file_name: str, progress_callback=None,
              on_complete=None, version_id=None, preview_url: str = "",
              source_url: str = "", expected_sha256: str = "", overwrite: bool = False) -> str:
        """启动异步下载任务

        Args:
            progress_callback: async def callback(downloaded, total)
            on_complete:       async def callback(dl_dict) — 下载结束时回调
            version_id:        CivitAI version ID，下载完成后自动匹配用
            overwrite:         True=覆盖同名文件（用于"重新下载"修损坏文件），不改名、不跳过；
                               下载仍走 .tmp→os.replace，完成前不破坏旧文件
        """
        if save_dir and file_name:
            try:
                os.makedirs(save_dir, exist_ok=True)
            except OSError:
                pass
            # 目标已有同名文件时自动改名避免覆盖；但 overwrite 时保持原名以覆盖（重下损坏文件）
            if not overwrite:
                file_name = _resolve_filename_conflict(
                    save_dir, file_name, download_url=download_url, version_id=version_id
                )

        save_path = os.path.join(save_dir, file_name)
        self._downloads[download_id] = {
            "id": download_id,
            "file_name": file_name,
            "save_dir": save_dir,
            "save_path": save_path,
            "download_url": download_url,
            "status": "queued",
            "downloaded": 0,
            "total": 0,
            "speed": 0,
            "eta": 0,
            "progress": 0,
            "error": "",
            "started_at": time.time(),
            "task": None,
            "on_complete": on_complete,
            "version_id": version_id,
            "preview_url": preview_url,
            "source_url": source_url,
            "expected_sha256": expected_sha256,  # 下载后比对完整性（内存态，不持久化）
        }
        self._persist(self._downloads[download_id])

        task = asyncio.create_task(
            self._run(download_id, download_url, save_dir, file_name, progress_callback, overwrite=overwrite)
        )
        self._downloads[download_id]["task"] = task
        logger.info("[Noctyra-MM] 下载任务已创建: %s -> %s%s", download_id, file_name, "（覆盖）" if overwrite else "")
        return download_id

    def find_active_download(self, save_dir: str, file_name: str):
        """是否已有指向同一目标路径的进行中下载（防连点发起重复下载）。返回其 id，无则 None。"""
        target = os.path.normcase(os.path.normpath(os.path.join(save_dir or "", file_name or "")))
        for dl in self._downloads.values():
            if dl.get("status") in ("queued", "downloading", "paused", "interrupted"):
                if os.path.normcase(os.path.normpath(dl.get("save_path", ""))) == target:
                    return dl.get("id")
        return None

    def cancel(self, download_id: str) -> bool:
        """取消下载任务，并清理残留的 .tmp 文件"""
        dl = self._downloads.get(download_id)
        if not dl:
            return False
        task = dl.get("task")
        if task and not task.done():
            task.cancel()
            dl["status"] = "cancelled"
            tmp_path = dl.get("save_path", "") + ".tmp"
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    logger.info("[Noctyra-MM] 已清理取消下载的临时文件: %s", tmp_path)
                except OSError as e:
                    logger.warning("[Noctyra-MM] 清理 .tmp 文件失败: %s", e)
            self._persist(dl)
            logger.info("[Noctyra-MM] 已取消下载: %s", download_id)
            return True
        return False

    def retry(self, download_id: str, progress_callback=None) -> bool:
        """重试已经失败/取消/中断的下载任务（复用同一 download_id，列表稳定）"""
        dl = self._downloads.get(download_id)
        if not dl:
            return False
        # 仅允许终态重试，避免误踢正在跑的任务
        if dl["status"] not in ("error", "cancelled", "interrupted"):
            return False

        # 旧 task 刚进终态时收尾协程（finally / on_complete）可能还没结束，
        # task.done() 仍为 False。此时重建会让两个 _run 协程争用同一 save_path，
        # 让用户毫秒级后再点更安全（下一次 poll 状态已稳定）
        old_task = dl.get("task")
        if old_task is not None and not old_task.done():
            return False

        dl["status"] = "queued"
        dl["downloaded"] = 0
        dl["total"] = 0
        dl["speed"] = 0
        dl["eta"] = 0
        dl["progress"] = 0
        dl["error"] = ""
        dl["started_at"] = time.time()
        self._persist(dl)

        task = asyncio.create_task(
            self._run(download_id, dl["download_url"], dl["save_dir"],
                      dl["file_name"], progress_callback)
        )
        dl["task"] = task
        logger.info("[Noctyra-MM] 下载任务重试: %s -> %s", download_id, dl["file_name"])
        return True

    def pause(self, download_id: str) -> bool:
        """暂停下载：中止当前任务但保留 .tmp 分片，之后可断点续传。
        与 cancel 的区别：cancel 删 .tmp（彻底放弃），pause 保留。"""
        dl = self._downloads.get(download_id)
        if not dl:
            return False
        if dl["status"] not in ("downloading", "queued"):
            return False
        # 先置 paused 再 cancel：_run 的 CancelledError 处理据此避免覆盖回 cancelled
        dl["status"] = "paused"
        dl["speed"] = 0
        dl["eta"] = 0
        task = dl.get("task")
        if task and not task.done():
            task.cancel()
        self._persist(dl)
        logger.info("[Noctyra-MM] 已暂停下载（保留 .tmp 续传）: %s", download_id)
        return True

    def resume(self, download_id: str, progress_callback=None) -> bool:
        """恢复已暂停的下载：从 .tmp 断点续传（不重置已下载进度显示）。"""
        dl = self._downloads.get(download_id)
        if not dl:
            return False
        if dl["status"] != "paused":
            return False
        # 旧 task 收尾未完时不重建，避免两个 _run 争用同一 .tmp（与 retry 一致）
        old_task = dl.get("task")
        if old_task is not None and not old_task.done():
            return False
        dl["status"] = "queued"
        dl["error"] = ""
        dl["started_at"] = time.time()
        self._persist(dl)
        task = asyncio.create_task(
            self._run(download_id, dl["download_url"], dl["save_dir"],
                      dl["file_name"], progress_callback)
        )
        dl["task"] = task
        logger.info("[Noctyra-MM] 恢复下载（断点续传）: %s -> %s", download_id, dl["file_name"])
        return True

    def redownload(self, download_id: str, progress_callback=None) -> bool:
        """重新下载（从头，不续传）。常用于成品被删/损坏想再抓一次。
        非破坏式：不预删旧成品，下到 .tmp 完成后 os.replace 原子替换；只清残留 .tmp。
        仅在没有活跃任务时允许（避免和正在跑的任务争用同一路径）。"""
        dl = self._downloads.get(download_id)
        if not dl:
            return False
        old_task = dl.get("task")
        if old_task is not None and not old_task.done():
            return False
        tmp_path = dl.get("save_path", "") + ".tmp"
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)  # 丢弃任何旧分片，强制从头下
            except OSError:
                pass
        dl["status"] = "queued"
        dl["downloaded"] = 0
        dl["total"] = 0
        dl["speed"] = 0
        dl["eta"] = 0
        dl["progress"] = 0
        dl["error"] = ""
        dl["started_at"] = time.time()
        self._persist(dl)
        task = asyncio.create_task(
            self._run(download_id, dl["download_url"], dl["save_dir"],
                      dl["file_name"], progress_callback, overwrite=True)
        )
        dl["task"] = task
        logger.info("[Noctyra-MM] 重新下载: %s -> %s", download_id, dl["file_name"])
        return True

    def list(self) -> list:
        """获取所有下载任务状态（按开始时间倒序）"""
        result = []
        for dl in self._downloads.values():
            result.append({
                "id": dl["id"],
                "file_name": dl["file_name"],
                "status": dl["status"],
                "downloaded": dl["downloaded"],
                "total": dl["total"],
                "speed": dl["speed"],
                "eta": dl["eta"],
                "progress": dl["progress"],
                "error": dl.get("error", ""),
                "started_at": dl.get("started_at", 0),
                "save_dir": dl.get("save_dir", ""),
                "preview_url": dl.get("preview_url", ""),
                "source_url": dl.get("source_url", ""),
            })
        result.sort(key=lambda d: d.get("started_at", 0), reverse=True)
        return result

    def remove(self, download_id: str) -> bool:
        """移除单个下载记录（终态 + interrupted + paused）"""
        dl = self._downloads.get(download_id)
        if not dl:
            return False
        if dl["status"] not in ("complete", "error", "cancelled", "interrupted", "paused"):
            return False
        # 暂停/中断/失败可能留着 .tmp 分片，移除记录时一并清掉，避免孤儿文件
        if dl["status"] in ("paused", "interrupted", "error", "cancelled"):
            tmp_path = dl.get("save_path", "") + ".tmp"
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        del self._downloads[download_id]
        try:
            self._manager.db.delete_download_record(download_id)
        except Exception:
            pass
        return True

    def cleanup(self, statuses=None):
        """清理终态下载记录。
        statuses: 可选状态集合（如 {'complete'} 或 {'error','cancelled','interrupted'}）
                  None 表示清所有终态。返回清掉的条数。"""
        if statuses is None:
            statuses = ("complete", "error", "cancelled", "interrupted")
        statuses = set(statuses)
        to_remove = [did for did, dl in self._downloads.items()
                     if dl["status"] in statuses]
        for did in to_remove:
            del self._downloads[did]
            try:
                self._manager.db.delete_download_record(did)
            except Exception:
                pass
        return len(to_remove)

    # ========== 内部：下载循环 + 下载后入库 ==========

    async def _run(self, download_id: str, download_url: str,
                   save_dir: str, file_name: str, progress_callback=None,
                   overwrite: bool = False):
        """执行单个下载任务（受信号量控制并发数）。
        overwrite=True 时跳过"文件已存在"检查（重下载用，下到 .tmp 完成后原子替换旧文件）。"""
        dl = self._downloads.get(download_id)
        if not dl:
            return

        on_complete = dl.get("on_complete")
        download_success = False

        async with self._semaphore:
            dl["status"] = "downloading"
            self._persist(dl)
            if not os.path.isdir(save_dir):
                os.makedirs(save_dir, exist_ok=True)

            save_path = dl["save_path"]
            if not overwrite and os.path.exists(save_path):
                dl["status"] = "error"
                dl["error"] = "文件已存在"
                self._persist(dl)
                logger.warning("[Noctyra-MM] 下载跳过，文件已存在: %s", save_path)
                if on_complete:
                    await on_complete(dl)
                return

            async def combined_callback(downloaded, total):
                dl["downloaded"] = downloaded
                dl["total"] = total
                dl["progress"] = round(downloaded / total * 100, 1) if total > 0 else 0
                if progress_callback:
                    await progress_callback(downloaded, total)

            try:
                success = await self._manager.civitai.download_file(download_url, save_path, combined_callback)
                if success:
                    dl["status"] = "complete"
                    dl["progress"] = 100
                    download_success = True
                    logger.info("[Noctyra-MM] 下载完成: %s -> %s", download_id, file_name)
                else:
                    dl["status"] = "error"
                    dl["error"] = "下载失败"
                    logger.error("[Noctyra-MM] 下载失败: %s", download_id)
            except asyncio.CancelledError:
                # pause() 在 cancel() 之前已把状态置为 paused，这里不要覆盖回 cancelled
                # （pause 保留 .tmp 以便续传；cancel 才会删 .tmp 并标 cancelled）
                if dl.get("status") != "paused":
                    dl["status"] = "cancelled"
                    logger.info("[Noctyra-MM] 下载已取消: %s", download_id)
                else:
                    logger.info("[Noctyra-MM] 下载已暂停（保留 .tmp 续传）: %s", download_id)
            except Exception as e:
                dl["status"] = "error"
                dl["error"] = str(e)
                logger.error("[Noctyra-MM] 下载异常: %s — %s", download_id, e)
            finally:
                self._persist(dl)

        sha_for_match = await self._post_index(dl) if download_success else None

        if on_complete:
            await on_complete(dl)

        # 入库完成即通知前端"下载完成"；CivitAI/HF 联网匹配后台异步跑，
        # 不再让慢查询把前端卡在"下载中 100%"（暂停也因 status 已是 complete 而无效）。
        # 存引用到 dl，防止 task 被 GC（asyncio 只持弱引用）
        if sha_for_match and dl.get("status") == "complete":
            dl["match_task"] = asyncio.create_task(self._post_match(dl, sha_for_match))

    async def _post_index(self, dl: dict):
        """下载完成后本地入库（SHA256 + 完整性校验 + 元数据 + 分类 + 入库）。
        返回 sha256；校验失败/异常返回 None。联网匹配交给 _post_match 后台跑。"""
        save_path = dl["save_path"]
        file_name = dl["file_name"]
        save_dir = dl["save_dir"]
        mgr = self._manager

        try:
            loop = asyncio.get_running_loop()

            # 1. 计算 SHA256
            sha256 = await loop.run_in_executor(None, compute_sha256, save_path)
            if not sha256:
                logger.warning("[Noctyra-MM] 下载后哈希计算失败: %s", file_name)
                return None

            # 1b. 完整性校验：与 CivitAI 提供的 SHA256 比对。不符=下载被截断/损坏/篡改，
            # 删掉成品、标错误、不入库；on_complete 随后会把 error 状态播给前端任务条。
            expected = (dl.get("expected_sha256") or "").lower()
            if expected and sha256.lower() != expected:
                logger.error("[Noctyra-MM] 下载完整性校验失败 %s：期望 %s… 实得 %s…，已删除成品",
                             file_name, expected[:10], sha256[:10])
                try:
                    os.remove(save_path)
                except OSError:
                    pass
                dl["status"] = "error"
                dl["error"] = "完整性校验失败（SHA256 不符），文件已删除，请重试"
                self._persist(dl)
                return None

            # 2. 读取 safetensors 元数据
            ext = os.path.splitext(file_name)[1].lower()
            base_model = "Unknown"
            trained_words = []
            metadata_raw = {}
            if ext == ".safetensors":
                metadata_raw = await loop.run_in_executor(None, read_safetensors_metadata, save_path)
                if metadata_raw:
                    # base_model 不从 safetensors 头推断（学 Lora-Manager）：留 Unknown，
                    # 下面的 version_id 匹配会用 CivitAI 权威值覆盖；trained_words 仍读。
                    trained_words = extract_trained_words(metadata_raw)

            # 3. 推断 model_type 和 folder
            # model_type 优先按【文件结构】判定（和 scanner 一致）：有 VAE/CLIP=checkpoint、
            # 只有扩散块=unet。结构判不出(非 safetensors 等)再按保存目录兜底。这样 Krea/Flux
            # 这类 CivitAI 标 Checkpoint、实为 UNet-only 扩散模型的，也能正确归为 unet 而非 checkpoint。
            from .scanner import classify_safetensors_file
            struct_type, _ = classify_safetensors_file(save_path)
            model_type = struct_type or mgr._infer_model_type(save_dir)
            # folder 口径与 scanner 一致：相对"已配置的 model_root"计算前缀（不是相对 save_dir，
            # 否则 loras 下的 Anima 会变成 folder="Anima"，和扫描产生的 "loras/Anima" 分叉）
            folder = _compute_folder_label(mgr.config.model_roots, save_path, save_dir)

            stat = os.stat(save_path)

            # 4. 入库
            mgr.db.upsert_model({
                "file_path": save_path,
                "file_name": file_name,
                "file_ext": ext,
                "file_size": stat.st_size,
                "modified": stat.st_mtime,
                "sha256": sha256,
                "base_model": base_model,
                "trained_words": trained_words,
                "metadata_raw": metadata_raw,
                "model_type": model_type,
                "folder": folder,
            })
            logger.info("[Noctyra-MM] 下载文件已入库: %s (SHA256: %s...)", file_name, sha256[:10])
            # 刷新损坏标志：刚下完(且已过 SHA256 校验)正常应为 0，重下修复后清掉旧的"损坏"标记
            from .scanner import check_safetensors_integrity
            mgr.db.set_corrupt_flags({save_path: 0 if check_safetensors_integrity(save_path, deep=False)["ok"] else 1})

            # 联网匹配（CivitAI version_id + HF 补充）交给 _post_match 后台跑，不在此阻塞：
            # 否则 CivitAI/HF 的慢查询会拖住"下载完成"通知，前端一直停在"下载中 100%"
            return sha256

        except Exception:
            logger.error("[Noctyra-MM] 下载后入库失败: %s", file_name, exc_info=True)
            return None

    async def _post_match(self, dl: dict, sha256: str):
        """后台联网匹配：version_id 直取 CivitAI 信息，未命中再补 HuggingFace。
        失败/超时只记日志，不影响已完成入库的下载（与下载完成通知解耦）。"""
        mgr = self._manager
        save_path = dl["save_path"]
        file_name = dl["file_name"]
        version_id = dl.get("version_id")
        try:
            if version_id:
                version_data = await mgr.civitai.get_model_version(version_id)
                if version_data:
                    info = CivitaiClient.parse_version_info(version_data)
                    model_id = info.get("civitai_model_id")
                    if model_id:
                        await asyncio.sleep(_API_DELAY)
                        model_data = await mgr.civitai.get_model_info(model_id)
                        if model_data:
                            info = CivitaiClient.enrich_with_model_info(info, model_data)
                    mgr.db.update_online_info(sha256, info)
                    logger.info("[Noctyra-MM] 下载后自动匹配成功: %s (by %s)", info.get("model_name", ""), info.get("creator", ""))
                else:
                    logger.warning("[Noctyra-MM] 下载后通过 version_id=%s 获取信息失败，回退 hash 匹配", version_id)
                    await mgr._try_civitai_match(sha256)
            else:
                await mgr._try_civitai_match(sha256)

            # 仅当 CivitAI 没匹配上时才查 HuggingFace：已匹配 CivitAI 的查 HF 基本查不到，
            # 还会因 HF 联网慢拖长匹配（之前同步执行时，它正是卡住下载完成的元凶）
            civitai_ok = mgr.db.get_by_path(save_path)
            if not (civitai_ok and civitai_ok.get("source") == "civitai"):
                await asyncio.sleep(_API_DELAY)
                await mgr._try_hf_match(sha256, file_name, supplement=False)
        except Exception:
            logger.error("[Noctyra-MM] 下载后联网匹配失败: %s", file_name, exc_info=True)
