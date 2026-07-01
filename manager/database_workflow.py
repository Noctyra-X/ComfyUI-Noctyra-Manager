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
ModelDatabase 的"辅助表"模块（mixin） —— 从 database.py 拆出。

包含以下非核心 models 表的 CRUD：
  - workflow_images（工作流图库 + 指纹回填）
  - ignored_model_versions（CivitAI 版本忽略表）
  - filter_presets（筛选预设）

被 ModelDatabase 继承。依赖 self._lock / self._connect()；
`from .recipes import compute_fingerprint` 延迟导入防循环。
"""

import json
import logging
import math
import os
import time
from typing import Optional

logger = logging.getLogger("noctyra.database")


class _WorkflowMixin:
    """工作流图库 / 版本忽略 / 筛选预设 相关方法集合。"""

    # ==================== 工作流图库 ====================

    def save_workflow_image(self, data: dict) -> int:
        """保存工作流图片记录，返回 id。已有记录时保留用户数据（custom_name/tags/notes/favorite）

        入库时自动计算配方指纹（fingerprint + recipe_version + base_model），
        便于跨图去重和"同配方查找"。"""
        from .recipes import compute_fingerprint, extract_base_model_from_image_info

        resources = data.get("resources") or []
        meta = data.get("meta") or {}
        # base_model 来源优先级：调用方传入 > 从 image_info/meta 推断
        base_model = (data.get("base_model") or "").strip()
        if not base_model:
            base_model = extract_base_model_from_image_info(data, meta=meta)
        fp_info = compute_fingerprint(base_model, resources)

        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT id, custom_name, tags, notes, favorite FROM workflow_images WHERE file_path = ?",
                    (data["file_path"],)
                ).fetchone()

                if existing:
                    conn.execute("""
                        UPDATE workflow_images SET
                            file_name=?, source=?, source_url=?, civitai_image_id=?,
                            width=?, height=?, nsfw_level=?, meta=?, resources=?,
                            has_workflow=?, workflow_json=?, api_prompt_json=?,
                            parameters_text=?, parsed_params=?, embed_source=?,
                            fingerprint=?, recipe_version=?, base_model=?,
                            media_type=?, source_root=?, saved_at=?
                        WHERE id=?
                    """, (
                        data.get("file_name", ""),
                        data.get("source", "civitai"),
                        data.get("source_url", ""),
                        data.get("civitai_image_id"),
                        data.get("width"),
                        data.get("height"),
                        data.get("nsfw_level", 0),
                        json.dumps(meta, ensure_ascii=False),
                        json.dumps(resources, ensure_ascii=False),
                        1 if data.get("has_workflow") else 0,
                        json.dumps(data.get("workflow_json")) if data.get("workflow_json") else "",
                        json.dumps(data.get("api_prompt_json")) if data.get("api_prompt_json") else "",
                        data.get("parameters_text", ""),
                        json.dumps(data.get("parsed_params", {}), ensure_ascii=False),
                        data.get("embed_source", "none"),
                        fp_info["fingerprint"],
                        fp_info["recipe_version"],
                        base_model,
                        data.get("media_type", "image"),
                        data.get("source_root", ""),
                        time.time(),
                        existing["id"],
                    ))
                    conn.commit()
                    return existing["id"]
                else:
                    conn.execute("""
                        INSERT INTO workflow_images
                        (file_path, file_name, source, source_url, civitai_image_id,
                         width, height, nsfw_level, meta, resources, has_workflow,
                         workflow_json, api_prompt_json, parameters_text, parsed_params,
                         embed_source, fingerprint, recipe_version, base_model,
                         media_type, source_root, saved_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        data["file_path"],
                        data.get("file_name", ""),
                        data.get("source", "civitai"),
                        data.get("source_url", ""),
                        data.get("civitai_image_id"),
                        data.get("width"),
                        data.get("height"),
                        data.get("nsfw_level", 0),
                        json.dumps(meta, ensure_ascii=False),
                        json.dumps(resources, ensure_ascii=False),
                        1 if data.get("has_workflow") else 0,
                        json.dumps(data.get("workflow_json")) if data.get("workflow_json") else "",
                        json.dumps(data.get("api_prompt_json")) if data.get("api_prompt_json") else "",
                        data.get("parameters_text", ""),
                        json.dumps(data.get("parsed_params", {}), ensure_ascii=False),
                        data.get("embed_source", "none"),
                        fp_info["fingerprint"],
                        fp_info["recipe_version"],
                        base_model,
                        data.get("media_type", "image"),
                        data.get("source_root", ""),
                        time.time(),
                    ))
                    conn.commit()
                    row_id = conn.execute(
                        "SELECT id FROM workflow_images WHERE file_path = ?",
                        (data["file_path"],)
                    ).fetchone()
                    return row_id["id"] if row_id else 0
            finally:
                conn.close()

    @staticmethod
    def _folder_like_clause(folder: str):
        """构造「该目录及其所有子目录」的 file_path 前缀匹配子句。

        返回 (sql_fragment, params)。用控制符 \\x01 当 LIKE ESCAPE（路径不含它），
        转义 % _ 及转义符本身，避免文件夹名里的下划线/百分号被当通配符。
        两种分隔符都匹配，兼容历史上以 / 或 \\ 存的 file_path。"""
        esc = "\x01"
        pref = folder.rstrip("\\/")
        safe = pref.replace(esc, esc + esc).replace("%", esc + "%").replace("_", esc + "_")
        frag = "(file_path LIKE ? ESCAPE ? OR file_path LIKE ? ESCAPE ?)"
        return frag, [safe + "\\%", esc, safe + "/%", esc]

    def list_workflow_images(self, page: int = 1, page_size: int = 40,
                             search: str = "", tag: str = "",
                             favorite_only: bool = False,
                             sfw_only: bool = False,
                             nsfw_threshold: int = 4,
                             has_workflow_only: bool = False,
                             fmt: str = "", media: str = "",
                             folder: str = "") -> dict:
        """分页查询工作流图库（搜索 / 标签 / 收藏 / 仅 SFW / 仅含可发送到画布的工作流 / 文件格式 / 媒体类型 / 文件夹）"""
        conn = self._connect(readonly=True)
        try:
            where = "1=1"
            params: list = []
            if folder:
                # Billfish 文件夹过滤：选中目录 = 该目录及子目录下所有文件
                frag, fp = self._folder_like_clause(folder)
                where += " AND " + frag
                params += fp
            if media == "video":
                where += " AND media_type = 'video'"
            elif media == "image":
                # 旧记录 media_type 可能为 NULL/空，一律按图片
                where += " AND (media_type IS NULL OR media_type != 'video')"
            if search:
                where += " AND (file_name LIKE ? OR custom_name LIKE ? OR meta LIKE ?)"
                like = f"%{search}%"
                params += [like, like, like]
            if tag:
                # tags 字段是 JSON 数组文本，用 LIKE 模糊包住引号确保精确匹配标签名
                where += " AND tags LIKE ?"
                params.append(f'%"{tag}"%')
            if fmt:
                # 文件格式：按扩展名过滤，jpg/jpeg 视为同一类。
                # 限定纯字母数字（扩展名本就如此）→ 既参数化又挡掉 LIKE 通配符 %/_ 的模糊匹配。
                f = fmt.lower().strip().lstrip(".")
                if f in ("jpg", "jpeg"):
                    where += " AND (LOWER(file_name) LIKE ? OR LOWER(file_name) LIKE ?)"
                    params += ["%.jpg", "%.jpeg"]
                elif f and f.isalnum():
                    where += " AND LOWER(file_name) LIKE ?"
                    params.append(f"%.{f}")
            if favorite_only:
                where += " AND favorite = 1"
            if has_workflow_only:
                # "可发送到画布" = 有 editor 格式的内嵌工作流（loadGraphData 用的就是它）
                where += " AND workflow_json IS NOT NULL AND workflow_json != ''"
            if sfw_only:
                # 手动标 NSFW 或者 nsfw_level 达阈值都算 NSFW，一并藏起来
                where += " AND (user_nsfw = 0 OR user_nsfw IS NULL) AND nsfw_level < ?"
                params.append(int(nsfw_threshold))

            total = conn.execute(
                f"SELECT COUNT(*) as c FROM workflow_images WHERE {where}", params
            ).fetchone()["c"]

            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""SELECT * FROM workflow_images WHERE {where}
                    ORDER BY saved_at DESC LIMIT ? OFFSET ?""",
                params + [page_size, offset]
            ).fetchall()

            images = []
            for r in rows:
                d = dict(r)
                d["meta"] = json.loads(d.get("meta") or "{}")
                d["resources"] = json.loads(d.get("resources") or "[]")
                d["tags"] = json.loads(d.get("tags") or "[]")
                d["parsed_params"] = json.loads(d.get("parsed_params") or "{}")
                images.append(d)

            return {
                "images": images,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": max(1, math.ceil(total / page_size)),
            }
        finally:
            conn.close()

    def gallery_dir_counts(self) -> dict:
        """返回 {目录绝对路径: 该目录(不含子目录)的图片数}，供上层聚合成文件夹树。

        只取 file_path 一列，几千条也只有几百 KB；目录拆分在 Python 侧做
        （SQLite 无 dirname）。"""
        from collections import Counter
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute("SELECT file_path FROM workflow_images").fetchall()
        finally:
            conn.close()
        c: Counter = Counter()
        for r in rows:
            fp = r["file_path"] or ""
            if fp:
                c[os.path.dirname(fp)] += 1
        return dict(c)

    def gallery_existing_paths(self) -> set:
        """返回图库里已有记录的全部 file_path 集合，供扫描器做增量跳过。"""
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute("SELECT file_path FROM workflow_images").fetchall()
            return {r["file_path"] for r in rows if r["file_path"]}
        finally:
            conn.close()

    def prune_missing_under(self, roots: list) -> int:
        """删除 file_path 落在给定根目录下、但磁盘上已不存在的图库记录，返回删除数。

        只清理位于注册根之内的记录；其余（如手动管理的）不动。"""
        if not roots:
            return 0
        norm_roots = []
        for r in roots:
            try:
                norm_roots.append(os.path.normcase(os.path.normpath(os.path.abspath(r))))
            except Exception:
                continue
        with self._lock:  # 先取锁再连接，与全库一致，避免锁顺序倒置导致的死锁
            conn = self._connect()
            try:
                rows = conn.execute("SELECT id, file_path FROM workflow_images").fetchall()
                to_del = []
                for r in rows:
                    fp = r["file_path"] or ""
                    if not fp:
                        continue
                    nfp = os.path.normcase(os.path.normpath(os.path.abspath(fp)))
                    if any(nfp == rt or nfp.startswith(rt + os.sep) for rt in norm_roots):
                        if not os.path.exists(fp):
                            to_del.append(r["id"])
                for _id in to_del:
                    conn.execute("DELETE FROM workflow_images WHERE id = ?", (_id,))
                if to_del:
                    conn.commit()
                return len(to_del)
            finally:
                conn.close()

    def delete_gallery_under(self, root: str) -> int:
        """删除 file_path 落在 root 目录（含子目录）下的所有图库记录，返回删除数。

        用于取消注册某文件夹时把它的索引记录清掉（磁盘文件不动）。"""
        if not root:
            return 0
        frag, fp = self._folder_like_clause(root)
        with self._lock:  # 先取锁再连接，与全库一致，避免锁顺序倒置导致的死锁
            conn = self._connect()
            try:
                cur = conn.execute(
                    f"DELETE FROM workflow_images WHERE {frag}", fp
                )
                conn.commit()
                return cur.rowcount or 0
            finally:
                conn.close()

    def get_workflow_image(self, image_id: int) -> Optional[dict]:
        """根据 id 获取单条工作流图片"""
        conn = self._connect(readonly=True)
        try:
            row = conn.execute(
                "SELECT * FROM workflow_images WHERE id = ?", (image_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["meta"] = json.loads(d.get("meta") or "{}")
            d["resources"] = json.loads(d.get("resources") or "[]")
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["parsed_params"] = json.loads(d.get("parsed_params") or "{}")
            return d
        finally:
            conn.close()

    def get_workflow_images_for_model(self, version_id=None, model_id=None, limit: int = 24):
        """反查：哪些图库配方(workflow_images)的资源用到了给定模型（按 modelVersionId /
        modelId）。给模型详情页"被哪些配方用过"用。返回 [{id, file_name, custom_name, media_type}]。"""
        conds, params = [], []
        if version_id:
            try:
                params.append(int(version_id))
                conds.append("CAST(json_extract(je.value, '$.modelVersionId') AS INTEGER) = ?")
            except (TypeError, ValueError):
                pass
        if model_id:
            try:
                params.append(int(model_id))
                conds.append("CAST(json_extract(je.value, '$.modelId') AS INTEGER) = ?")
            except (TypeError, ValueError):
                pass
        if not conds:
            return []
        params.append(int(limit))
        where = " OR ".join(conds)
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    f"""SELECT DISTINCT w.id, w.file_name, w.custom_name, w.media_type
                        FROM workflow_images w, json_each(w.resources) je
                        WHERE {where}
                        ORDER BY w.id DESC LIMIT ?""",
                    params,
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                logger.warning("[Noctyra-MM] 反查配方失败: %s", e)
                return []
            finally:
                conn.close()

    def update_workflow_image(self, image_id: int, updates: dict) -> bool:
        """更新工作流图片记录的指定字段。
        resources 变更时自动重算 fingerprint（保持 base_model 不变）。"""
        allowed = {"custom_name", "tags", "notes", "favorite", "resources", "user_nsfw"}
        sets = []
        params = []

        resources_changed = "resources" in updates
        new_resources = updates.get("resources") if resources_changed else None

        for k, v in updates.items():
            if k not in allowed:
                continue
            if k == "tags" and isinstance(v, list):
                v = json.dumps(v, ensure_ascii=False)
            if k == "resources" and isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k} = ?")
            params.append(v)
        if not sets:
            return False

        # resources 更新时一并重算指纹（读当前 base_model 作输入，避免外部传入遗漏）
        if resources_changed:
            from .recipes import compute_fingerprint
            with self._lock:
                conn_ro = self._connect(readonly=True)
                try:
                    row = conn_ro.execute(
                        "SELECT base_model FROM workflow_images WHERE id = ?",
                        (image_id,),
                    ).fetchone()
                    base_model = (row["base_model"] if row else "") or ""
                finally:
                    conn_ro.close()
            resources_list = new_resources if isinstance(new_resources, list) else []
            fp_info = compute_fingerprint(base_model, resources_list)
            sets.append("fingerprint = ?")
            params.append(fp_info["fingerprint"])
            sets.append("recipe_version = ?")
            params.append(fp_info["recipe_version"])

        params.append(image_id)
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    f"UPDATE workflow_images SET {', '.join(sets)} WHERE id = ?",
                    params
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def list_workflow_tags(self) -> list:
        """聚合图库里所有出现过的 tag，返回 [(tag, count), ...] 按计数降序。"""
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute(
                "SELECT tags FROM workflow_images WHERE tags IS NOT NULL AND tags != '[]'"
            ).fetchall()
        finally:
            conn.close()
        counter = {}
        for r in rows:
            try:
                for t in json.loads(r["tags"] or "[]"):
                    if t and isinstance(t, str):
                        counter[t] = counter.get(t, 0) + 1
            except (ValueError, TypeError):
                continue
        return sorted(counter.items(), key=lambda x: (-x[1], x[0]))

    def list_workflow_image_formats(self) -> list:
        """聚合图库里出现过的文件格式（扩展名），jpeg 归入 jpg。
        返回 [(fmt, count), ...] 按计数降序，fmt 为小写扩展名。"""
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute("SELECT file_name FROM workflow_images").fetchall()
        finally:
            conn.close()
        counter = {}
        for r in rows:
            name = r["file_name"] or ""
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if not ext or len(ext) > 5:
                continue
            if ext == "jpeg":
                ext = "jpg"
            counter[ext] = counter.get(ext, 0) + 1
        return sorted(counter.items(), key=lambda x: (-x[1], x[0]))

    def backfill_workflow_fingerprint(self, image_id: int) -> Optional[str]:
        """按需给 recipe_version=0 的老记录回填指纹。调用方在打开详情时用。
        返回新算的 fingerprint，若记录不存在或已回填则返回 None。"""
        from .recipes import compute_fingerprint
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT resources, base_model, recipe_version FROM workflow_images WHERE id = ?",
                    (image_id,),
                ).fetchone()
                if not row:
                    return None
                if (row["recipe_version"] or 0) > 0:
                    return None  # 已回填
                try:
                    resources = json.loads(row["resources"] or "[]")
                except Exception:
                    resources = []
                base_model = row["base_model"] or ""
                fp_info = compute_fingerprint(base_model, resources)
                conn.execute(
                    "UPDATE workflow_images SET fingerprint = ?, recipe_version = ? WHERE id = ?",
                    (fp_info["fingerprint"], fp_info["recipe_version"], image_id),
                )
                conn.commit()
                return fp_info["fingerprint"]
            finally:
                conn.close()

    def delete_workflow_image(self, image_id: int) -> bool:
        """删除工作流图片记录"""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM workflow_images WHERE id = ?", (image_id,)
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def cleanup_missing_workflow_images(self) -> int:
        """清理 file_path 已不存在于磁盘的图库记录，返回删除数量。

        用于"插件被整体拷贝到新位置"等场景：老记录里的绝对路径全部失效，
        继续保留只会在图库里显示一堆 404 缩略图。
        """
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, file_path FROM workflow_images"
                ).fetchall()
                missing_ids = [
                    r["id"] for r in rows
                    if not r["file_path"] or not os.path.isfile(r["file_path"])
                ]
                if not missing_ids:
                    return 0
                batch = 500
                for i in range(0, len(missing_ids), batch):
                    chunk = missing_ids[i:i + batch]
                    placeholders = ",".join("?" * len(chunk))
                    conn.execute(
                        f"DELETE FROM workflow_images WHERE id IN ({placeholders})",
                        chunk,
                    )
                conn.commit()
                logger.info("[Noctyra-WF] 清理 %d 个失效的图库记录（file_path 不存在）",
                            len(missing_ids))
                return len(missing_ids)
            finally:
                conn.close()

    def get_workflow_image_by_civitai_id(self, civitai_image_id: int) -> Optional[dict]:
        """根据 CivitAI image ID 查找是否已保存"""
        conn = self._connect(readonly=True)
        try:
            row = conn.execute(
                "SELECT * FROM workflow_images WHERE civitai_image_id = ?",
                (civitai_image_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["meta"] = json.loads(d.get("meta") or "{}")
            d["resources"] = json.loads(d.get("resources") or "[]")
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["parsed_params"] = json.loads(d.get("parsed_params") or "{}")
            return d
        finally:
            conn.close()

    def list_workflow_images_by_fingerprint(self, fingerprint: str,
                                             exclude_id: int = 0) -> list:
        """按 fingerprint 查所有同配方图。exclude_id 用于排除当前图，避免列表里出现自己。"""
        if not fingerprint:
            return []
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute(
                "SELECT id, file_path, file_name, custom_name, width, height, "
                "civitai_image_id, nsfw_level, base_model, favorite, saved_at "
                "FROM workflow_images WHERE fingerprint = ? AND id != ? "
                "ORDER BY saved_at DESC LIMIT 200",
                (fingerprint, int(exclude_id) if exclude_id else 0),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ========== Download Queue（下载队列持久化） ==========

    def save_download_record(self, record: dict) -> None:
        """upsert 一个下载记录。record 字段对应 download_queue 表列。"""
        import time as _t
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("""
                    INSERT INTO download_queue
                        (id, download_url, save_dir, file_name, version_id, preview_url,
                         status, downloaded, total, progress, error, started_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        downloaded = excluded.downloaded,
                        total = excluded.total,
                        progress = excluded.progress,
                        error = excluded.error,
                        updated_at = excluded.updated_at
                """, (
                    record["id"], record["download_url"],
                    record["save_dir"], record["file_name"],
                    record.get("version_id"),
                    record.get("preview_url", ""),
                    record.get("status", "queued"),
                    int(record.get("downloaded", 0) or 0),
                    int(record.get("total", 0) or 0),
                    float(record.get("progress", 0) or 0),
                    record.get("error", ""),
                    float(record.get("started_at", _t.time())),
                    _t.time(),
                ))
                conn.commit()
            finally:
                conn.close()

    def delete_download_record(self, download_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM download_queue WHERE id = ?", (download_id,))
                conn.commit()
            finally:
                conn.close()

    def list_download_records(self, pending_only: bool = False) -> list:
        """
        pending_only=True 只返回未到终态的（queued / downloading / interrupted / paused）
        """
        where = ""
        params: list = []
        if pending_only:
            where = "WHERE status IN ('queued', 'downloading', 'interrupted', 'paused')"
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute(
                f"SELECT * FROM download_queue {where} ORDER BY started_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_downloads_interrupted(self) -> int:
        """启动时把所有进行中的任务标成 interrupted。返回更新行数。"""
        with self._lock:
            conn = self._connect()
            try:
                import time as _t
                cur = conn.execute(
                    "UPDATE download_queue SET status = 'interrupted', updated_at = ? "
                    "WHERE status IN ('queued', 'downloading')",
                    (_t.time(),),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    # ========== Ignored Model Versions（版本忽略表） ==========

    def add_ignored_version(self, model_id: int, version_id: int):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO ignored_model_versions "
                    "(civitai_model_id, civitai_version_id, ignored_at) VALUES (?, ?, ?)",
                    (int(model_id), int(version_id), time.time()),
                )
                conn.commit()
            finally:
                conn.close()

    def remove_ignored_version(self, model_id: int, version_id: int):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM ignored_model_versions "
                    "WHERE civitai_model_id = ? AND civitai_version_id = ?",
                    (int(model_id), int(version_id)),
                )
                conn.commit()
            finally:
                conn.close()

    def list_ignored_versions(self, model_id: int) -> list:
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute(
                "SELECT civitai_version_id FROM ignored_model_versions WHERE civitai_model_id = ?",
                (int(model_id),),
            ).fetchall()
            return [r["civitai_version_id"] for r in rows]
        finally:
            conn.close()

    def is_version_ignored(self, model_id: int, version_id: int) -> bool:
        conn = self._connect(readonly=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM ignored_model_versions "
                "WHERE civitai_model_id = ? AND civitai_version_id = ?",
                (int(model_id), int(version_id)),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # ========== Filter Presets（筛选预设） ==========

    def list_filter_presets(self) -> list:
        """列出所有筛选预设，按创建时间升序"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT id, name, filters, created_at, updated_at "
                    "FROM filter_presets ORDER BY created_at ASC, id ASC"
                ).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    try:
                        d["filters"] = json.loads(d.get("filters") or "{}")
                    except Exception:
                        d["filters"] = {}
                    result.append(d)
                return result
            finally:
                conn.close()

    def save_filter_preset(self, name: str, filters: dict) -> dict:
        """按 name upsert 一个预设；返回 {"id", "name", "filters", "created_at", "updated_at"}"""
        name = (name or "").strip()
        if not name:
            raise ValueError("name 不能为空")
        filters_json = json.dumps(filters or {}, ensure_ascii=False)
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT id, created_at FROM filter_presets WHERE name = ?", (name,)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE filter_presets SET filters = ?, updated_at = ? WHERE id = ?",
                        (filters_json, now, existing["id"]),
                    )
                    conn.commit()
                    return {
                        "id": existing["id"],
                        "name": name,
                        "filters": filters or {},
                        "created_at": existing["created_at"],
                        "updated_at": now,
                    }
                cur = conn.execute(
                    "INSERT INTO filter_presets (name, filters, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (name, filters_json, now, now),
                )
                conn.commit()
                return {
                    "id": cur.lastrowid,
                    "name": name,
                    "filters": filters or {},
                    "created_at": now,
                    "updated_at": now,
                }
            finally:
                conn.close()

    def delete_filter_preset(self, identifier) -> bool:
        """按 id（int）或 name（str）删除预设"""
        with self._lock:
            conn = self._connect()
            try:
                if isinstance(identifier, int):
                    cur = conn.execute(
                        "DELETE FROM filter_presets WHERE id = ?", (identifier,)
                    )
                else:
                    cur = conn.execute(
                        "DELETE FROM filter_presets WHERE name = ?", (str(identifier),)
                    )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()
