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
SQLite 持久缓存

替换旧的 JSON 缓存，提供模型信息的持久存储、分页查询、过滤和统计。
WAL 模式支持并发读取，threading.Lock 保证写入安全。
首次运行时自动从旧 JSON 缓存迁移数据。
"""

import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("noctyra.database")

# 辅助表方法（workflow_images / ignored_model_versions / filter_presets）拆到独立 mixin
from .database_workflow import _WorkflowMixin


class ModelDatabase(_WorkflowMixin):
    """SQLite 模型数据库"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()
        logger.info("[Noctyra-MM] 数据库已初始化: %s", db_path)
        self._try_migrate_json(os.path.dirname(db_path))

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            # 真·只读连接：WAL 下读取一致快照、不占写锁、也物理无法写坏库；
            # mode=ro 不能也不需要跑 journal_mode=WAL（库的 WAL 属性是持久化的），
            # 顺带省掉读热路径上每次连库都执行的 PRAGMA。
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS models (
                        file_path          TEXT PRIMARY KEY,
                        file_name          TEXT NOT NULL,
                        file_ext           TEXT,
                        file_size          INTEGER,
                        modified           REAL,
                        sha256             TEXT,
                        base_model         TEXT DEFAULT 'Unknown',
                        trained_words      TEXT DEFAULT '[]',
                        metadata_raw       TEXT,
                        source             TEXT DEFAULT '',
                        source_url         TEXT DEFAULT '',
                        model_name         TEXT DEFAULT '',
                        version_name       TEXT DEFAULT '',
                        model_description  TEXT DEFAULT '',
                        preview_url        TEXT DEFAULT '',
                        preview_images     TEXT DEFAULT '[]',
                        matched            INTEGER DEFAULT 0,
                        civitai_model_id   INTEGER,
                        civitai_version_id INTEGER,
                        civitai_model_type TEXT DEFAULT '',
                        creator            TEXT DEFAULT '',
                        creator_avatar     TEXT DEFAULT '',
                        nsfw               INTEGER DEFAULT 0,
                        published_at       TEXT DEFAULT '',
                        comment_count      INTEGER DEFAULT 0,
                        downloads          INTEGER DEFAULT 0,
                        rating             REAL DEFAULT 0,
                        rating_count       INTEGER DEFAULT 0,
                        thumbs_up          INTEGER DEFAULT 0,
                        favorite           INTEGER DEFAULT 0,
                        notes              TEXT DEFAULT '',
                        model_type         TEXT DEFAULT '',
                        lora_subtype       TEXT DEFAULT '',
                        hf_repo_id         TEXT DEFAULT '',
                        hf_url             TEXT DEFAULT '',
                        hf_downloads       INTEGER DEFAULT 0,
                        hf_likes           INTEGER DEFAULT 0,
                        hf_author          TEXT DEFAULT '',
                        hf_description     TEXT DEFAULT '',
                        hf_tags            TEXT DEFAULT '[]',
                        hf_last_modified   TEXT DEFAULT '',
                        folder             TEXT DEFAULT '',
                        cached_at          REAL,
                        matched_at         REAL
                    );

                    CREATE TABLE IF NOT EXISTS model_tags (
                        file_path TEXT NOT NULL,
                        tag       TEXT NOT NULL,
                        PRIMARY KEY (file_path, tag)
                    );

                    CREATE TABLE IF NOT EXISTS hash_index (
                        sha256    TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        PRIMARY KEY (sha256, file_path)
                    );

                    CREATE TABLE IF NOT EXISTS metadata_archive (
                        sha256      TEXT PRIMARY KEY,
                        source      TEXT DEFAULT '',
                        data        TEXT DEFAULT '{}',
                        archived_at REAL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS download_queue (
                        id            TEXT PRIMARY KEY,
                        download_url  TEXT NOT NULL,
                        save_dir      TEXT NOT NULL,
                        file_name     TEXT NOT NULL,
                        version_id    INTEGER,
                        preview_url   TEXT DEFAULT '',
                        status        TEXT DEFAULT 'queued',
                        downloaded    INTEGER DEFAULT 0,
                        total         INTEGER DEFAULT 0,
                        progress      REAL DEFAULT 0,
                        error         TEXT DEFAULT '',
                        started_at    REAL DEFAULT 0,
                        updated_at    REAL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS ignored_model_versions (
                        civitai_model_id   INTEGER NOT NULL,
                        civitai_version_id INTEGER NOT NULL,
                        ignored_at         REAL DEFAULT 0,
                        PRIMARY KEY (civitai_model_id, civitai_version_id)
                    );

                    CREATE TABLE IF NOT EXISTS filter_presets (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        name        TEXT NOT NULL UNIQUE,
                        filters     TEXT NOT NULL DEFAULT '{}',
                        created_at  REAL DEFAULT 0,
                        updated_at  REAL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS workflow_images (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_path       TEXT NOT NULL UNIQUE,
                        file_name       TEXT NOT NULL,
                        source          TEXT DEFAULT 'civitai',
                        source_url      TEXT DEFAULT '',
                        civitai_image_id INTEGER,
                        width           INTEGER,
                        height          INTEGER,
                        nsfw_level      INTEGER DEFAULT 0,
                        meta            TEXT DEFAULT '{}',
                        resources       TEXT DEFAULT '[]',
                        has_workflow    INTEGER DEFAULT 0,
                        workflow_json   TEXT DEFAULT '',
                        api_prompt_json TEXT DEFAULT '',
                        parameters_text TEXT DEFAULT '',
                        parsed_params   TEXT DEFAULT '{}',
                        embed_source    TEXT DEFAULT 'none',
                        favorite        INTEGER DEFAULT 0,
                        notes           TEXT DEFAULT '',
                        tags            TEXT DEFAULT '[]',
                        source_root     TEXT DEFAULT '',
                        saved_at        REAL
                    );

                    CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models(sha256);
                    CREATE INDEX IF NOT EXISTS idx_models_folder ON models(folder);
                    CREATE INDEX IF NOT EXISTS idx_models_base_model ON models(base_model);
                    CREATE INDEX IF NOT EXISTS idx_models_matched ON models(matched);
                    CREATE INDEX IF NOT EXISTS idx_models_source ON models(source);
                    CREATE INDEX IF NOT EXISTS idx_hash_index_sha256 ON hash_index(sha256);
                    CREATE INDEX IF NOT EXISTS idx_wf_images_saved ON workflow_images(saved_at);
                """)
                conn.commit()

                # Schema 迁移：为已有数据库添加新字段
                self._migrate_schema(conn)
                self._migrate_workflow_schema(conn)

                # 迁移后才能建新字段的索引
                conn.execute("CREATE INDEX IF NOT EXISTS idx_models_model_type ON models(model_type)")
                # by-hash 回退查询 / 重复检测 / 扩展 check 都按 version_id / model_id 过滤，
                # 之前走全表扫；加索引后千级库 10ms → <1ms
                conn.execute("CREATE INDEX IF NOT EXISTS idx_models_civitai_version_id ON models(civitai_version_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_models_civitai_model_id ON models(civitai_model_id)")
                # source_root 是迁移新加的列，索引必须在 ALTER 之后建，否则旧库建库即崩
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_images_root ON workflow_images(source_root)")
                # civitai_image_id / fingerprint 是迁移新加的列；历史 schema 的旧库可能仍无该列，
                # 建索引前先按 PRAGMA 探测列是否存在，避免 no such column 建库即崩（用于去重/查配方免全表扫）
                _wf_cols = {r[1] for r in conn.execute("PRAGMA table_info(workflow_images)")}
                if "civitai_image_id" in _wf_cols:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_images_civitai_id ON workflow_images(civitai_image_id)")
                if "fingerprint" in _wf_cols:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_images_fingerprint ON workflow_images(fingerprint)")
                conn.commit()

                # FTS5 全文索引：覆盖 file_name / model_name / model_description /
                # trained_words / civitai_tags / version_name，让 search 不再只匹配文件名
                self._init_fts(conn)
            finally:
                conn.close()

    def _init_fts(self, conn):
        """建立 models_fts 虚拟表 + 同步触发器。已存在则 skip。"""
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS models_fts USING fts5(
                    file_name, model_name, model_description,
                    trained_words, civitai_tags, version_name, creator,
                    content='models', content_rowid='rowid',
                    tokenize = 'unicode61 remove_diacritics 2'
                )
            """)
            # 已有表不会重新填充；首次创建时 rowid 范围为空，需要手动 rebuild
            row = conn.execute("SELECT COUNT(*) FROM models_fts").fetchone()
            if row and row[0] == 0:
                total = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
                if total > 0:
                    # 用 INSERT INTO ... SELECT 一次性填充
                    conn.execute("""
                        INSERT INTO models_fts(rowid, file_name, model_name,
                            model_description, trained_words, civitai_tags,
                            version_name, creator)
                        SELECT rowid, file_name, model_name, model_description,
                               trained_words, civitai_tags, version_name, creator
                        FROM models
                    """)
                    logger.info("[Noctyra-MM] FTS5 首次索引 %d 个模型", total)

            # 同步触发器：models 插入/更新/删除 → 自动维护 fts
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS models_fts_ai AFTER INSERT ON models BEGIN
                    INSERT INTO models_fts(rowid, file_name, model_name,
                        model_description, trained_words, civitai_tags, version_name, creator)
                    VALUES (new.rowid, new.file_name, new.model_name,
                        new.model_description, new.trained_words, new.civitai_tags,
                        new.version_name, new.creator);
                END;
                CREATE TRIGGER IF NOT EXISTS models_fts_ad AFTER DELETE ON models BEGIN
                    INSERT INTO models_fts(models_fts, rowid, file_name, model_name,
                        model_description, trained_words, civitai_tags, version_name, creator)
                    VALUES('delete', old.rowid, old.file_name, old.model_name,
                        old.model_description, old.trained_words, old.civitai_tags,
                        old.version_name, old.creator);
                END;
                CREATE TRIGGER IF NOT EXISTS models_fts_au AFTER UPDATE ON models BEGIN
                    INSERT INTO models_fts(models_fts, rowid, file_name, model_name,
                        model_description, trained_words, civitai_tags, version_name, creator)
                    VALUES('delete', old.rowid, old.file_name, old.model_name,
                        old.model_description, old.trained_words, old.civitai_tags,
                        old.version_name, old.creator);
                    INSERT INTO models_fts(rowid, file_name, model_name,
                        model_description, trained_words, civitai_tags, version_name, creator)
                    VALUES (new.rowid, new.file_name, new.model_name,
                        new.model_description, new.trained_words, new.civitai_tags,
                        new.version_name, new.creator);
                END;
            """)
            conn.commit()
        except sqlite3.OperationalError as e:
            # SQLite 没编译 FTS5 支持；记 warning 后静默 fallback 到 LIKE
            logger.warning("[Noctyra-MM] FTS5 不可用，search 将 fallback 到 LIKE: %s", e)
            self._fts_available = False
            return
        self._fts_available = True

    def _migrate_schema(self, conn):
        """增量迁移数据库 schema"""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(models)").fetchall()}
        migrations = [
            ("version_name", "TEXT DEFAULT ''"),
            ("preview_images", "TEXT DEFAULT '[]'"),
            ("downloads", "INTEGER DEFAULT 0"),
            ("rating", "REAL DEFAULT 0"),
            ("rating_count", "INTEGER DEFAULT 0"),
            ("thumbs_up", "INTEGER DEFAULT 0"),
            ("model_type", "TEXT DEFAULT ''"),
            ("creator_avatar", "TEXT DEFAULT ''"),
            ("published_at", "TEXT DEFAULT ''"),
            ("comment_count", "INTEGER DEFAULT 0"),
            ("hf_repo_id", "TEXT DEFAULT ''"),
            ("hf_url", "TEXT DEFAULT ''"),
            ("hf_downloads", "INTEGER DEFAULT 0"),
            ("hf_likes", "INTEGER DEFAULT 0"),
            ("hf_author", "TEXT DEFAULT ''"),
            ("hf_description", "TEXT DEFAULT ''"),
            ("hf_tags", "TEXT DEFAULT '[]'"),
            ("hf_last_modified", "TEXT DEFAULT ''"),
            ("update_available", "INTEGER DEFAULT 0"),
            ("usage_count", "INTEGER DEFAULT 0"),
            ("last_used_at", "REAL DEFAULT 0"),
            ("civitai_tags", "TEXT DEFAULT '[]'"),
            ("file_deleted", "INTEGER DEFAULT 0"),
            ("deleted_at", "REAL DEFAULT 0"),
            ("archived_path", "TEXT DEFAULT ''"),  # 软删除时文件移到存档夹的实际位置，供精确恢复
            ("custom_fields", "TEXT DEFAULT '[]'"),  # 用户在"自定义"Tab 手动锁定的字段名 JSON 列表，匹配/刷新不覆盖
            ("file_corrupt", "INTEGER DEFAULT 0"),  # 扫描判定的损坏标志：1=safetensors 头截断/数据区未铺满
            ("last_update_check_at", "REAL DEFAULT 0"),  # 24h TTL：跳过最近查过的更新检查
            ("civitai_raw", "TEXT DEFAULT ''"),
            ("hf_raw", "TEXT DEFAULT ''"),
            ("hf_match_type", "TEXT DEFAULT ''"),
            # License 筛选：-1 未知, 0 仅个人, 1 允许商用
            ("civitai_allow_commercial", "INTEGER DEFAULT -1"),
            # 用户手动覆盖的类型（自定义 Tab 里填写）；空 = 走自动识别链
            # 解析优先级：user_model_type > safetensors 结构 > 目录名 > CivitAI 分类
            ("user_model_type", "TEXT DEFAULT ''"),
            # LoRA 家族细分（仅用于筛选，不影响 model_type/归档）：'lora'/'lycoris'/'dora'
            # matched 用 CivitAI type 权威；unmatched 用 safetensors 结构判定
            ("lora_subtype", "TEXT DEFAULT ''"),
        ]
        # col 和 typedef 都要走 f-string 拼进 DDL（SQLite 不支持参数化字段名/类型），
        # 所以用白名单校验防止未来有人不小心把外部输入塞进 migrations
        _SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        _SAFE_TYPEDEF = re.compile(r"^[A-Za-z0-9_ '\"\[\]\\.\-]+$")
        for col, typedef in migrations:
            if col in existing:
                continue
            if not _SAFE_IDENT.match(col) or not _SAFE_TYPEDEF.match(typedef):
                logger.error("[Noctyra-MM] 拒绝可疑的迁移字段：%r %r", col, typedef)
                continue
            conn.execute(f"ALTER TABLE models ADD COLUMN {col} {typedef}")
            logger.info("[Noctyra-MM] 数据库迁移：添加字段 %s", col)

            # civitai_tags 新增时，把 model_tags 中的旧数据迁移过来并清空
            if col == "civitai_tags":
                self._migrate_civitai_tags(conn)

            # lora_subtype 新增时，从已有 civitai_model_type 回填 matched 行
            # （LoRA/LoCon/DoRA → lora/lycoris/dora），matched 模型无需重扫即可筛选
            if col == "lora_subtype":
                self._backfill_lora_subtype(conn)

    def _backfill_lora_subtype(self, conn):
        """从已有 civitai_model_type 回填 lora_subtype（matched 的 LoRA 家族模型）。
        CivitAI: LORA→lora, LoCon→lycoris, DoRA→dora。未匹配的 LoRA 需重扫才有结构判定。"""
        try:
            n = conn.execute("""
                UPDATE models SET lora_subtype = CASE civitai_model_type
                    WHEN 'LORA'  THEN 'lora'
                    WHEN 'LoCon' THEN 'lycoris'
                    WHEN 'DoRA'  THEN 'dora'
                END
                WHERE model_type = 'lora'
                  AND civitai_model_type IN ('LORA', 'LoCon', 'DoRA')
                  AND (lora_subtype IS NULL OR lora_subtype = '')
            """).rowcount
            if n:
                logger.info("[Noctyra-MM] 回填 %d 个 matched LoRA 的细分类型(lora_subtype)", n)
        except Exception as e:
            logger.warning("[Noctyra-MM] 回填 lora_subtype 失败: %s", e)

    def _migrate_civitai_tags(self, conn):
        """将 model_tags 表中由 CivitAI 写入的标签迁移到 civitai_tags 字段"""
        rows = conn.execute("""
            SELECT m.file_path, GROUP_CONCAT(mt.tag, '||') as tags
            FROM models m
            JOIN model_tags mt ON m.file_path = mt.file_path
            WHERE m.source = 'civitai'
            GROUP BY m.file_path
        """).fetchall()
        for row in rows:
            fp = row["file_path"]
            tags = row["tags"].split("||") if row["tags"] else []
            tags_json = json.dumps(tags, ensure_ascii=False)
            conn.execute("UPDATE models SET civitai_tags = ? WHERE file_path = ?", (tags_json, fp))
            conn.execute("DELETE FROM model_tags WHERE file_path = ?", (fp,))
        if rows:
            logger.info("[Noctyra-MM] 迁移 %d 个模型的 CivitAI 标签到 civitai_tags 字段", len(rows))

    def _migrate_workflow_schema(self, conn):
        """为 workflow_images 表增量添加新字段"""
        try:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(workflow_images)").fetchall()}
        except Exception:
            return
        wf_migrations = [
            ("workflow_json", "TEXT DEFAULT ''"),
            ("api_prompt_json", "TEXT DEFAULT ''"),
            ("parameters_text", "TEXT DEFAULT ''"),
            ("parsed_params", "TEXT DEFAULT '{}'"),
            ("embed_source", "TEXT DEFAULT 'none'"),
            ("custom_name", "TEXT DEFAULT ''"),
            ("fingerprint", "TEXT DEFAULT ''"),
            ("recipe_version", "INTEGER DEFAULT 0"),
            ("base_model", "TEXT DEFAULT ''"),
            # 'image' 或 'video'；旧记录默认 'image'
            ("media_type", "TEXT DEFAULT 'image'"),
            # 用户手动标 NSFW（0/1），优先于 CivitAI 的 nsfw_level
            ("user_nsfw", "INTEGER DEFAULT 0"),
            # Billfish 文件夹模型：该文件所属的注册文件夹根（绝对路径）；''=遗留托管库
            ("source_root", "TEXT DEFAULT ''"),
        ]
        _SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        _SAFE_TYPEDEF = re.compile(r"^[A-Za-z0-9_ '\"\[\]\\.\-]+$")
        for col, typedef in wf_migrations:
            if col in existing:
                continue
            if not _SAFE_IDENT.match(col) or not _SAFE_TYPEDEF.match(typedef):
                logger.error("[Noctyra-WF] 拒绝可疑的 workflow 迁移字段：%r %r", col, typedef)
                continue
            conn.execute(f"ALTER TABLE workflow_images ADD COLUMN {col} {typedef}")
            logger.info("[Noctyra-WF] 数据库迁移：workflow_images 添加字段 %s", col)

        # 修复历史脏数据：nsfw_level 被误存成字符串（'X'/'Mature'/...），转成位图整数
        try:
            affected = conn.execute("""
                UPDATE workflow_images SET nsfw_level = CASE lower(nsfw_level)
                    WHEN 'none'   THEN 1
                    WHEN 'soft'   THEN 2
                    WHEN 'mature' THEN 4
                    WHEN 'x'      THEN 8
                    WHEN 'xxx'    THEN 16
                    ELSE 0
                END
                WHERE typeof(nsfw_level) = 'text'
            """).rowcount
            if affected:
                logger.info("[Noctyra-WF] 修复 %d 条 nsfw_level 字符串 → 整数", affected)
        except Exception as e:
            logger.warning("[Noctyra-WF] nsfw_level 修复失败: %s", e)

    # ========== 写入操作 ==========

    # upsert_model 与 upsert_models_batch 共用同一条 SQL，避免"改一处漏另一处"
    _UPSERT_MODEL_SQL = """
        INSERT INTO models (
            file_path, file_name, file_ext, file_size, modified,
            sha256, base_model, trained_words, metadata_raw,
            source, source_url, model_name, model_description, preview_url,
            matched, civitai_model_id, civitai_version_id, civitai_model_type,
            creator, nsfw, favorite, notes, model_type, lora_subtype, folder, cached_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(file_path) DO UPDATE SET
            file_name = excluded.file_name,
            file_ext = excluded.file_ext,
            file_size = excluded.file_size,
            modified = excluded.modified,
            sha256 = COALESCE(NULLIF(excluded.sha256, ''), models.sha256),
            base_model = CASE
                WHEN excluded.base_model IS NULL OR excluded.base_model IN ('', 'Unknown')
                THEN models.base_model
                -- 已匹配在线信息的模型，base_model 以 CivitAI/HF 为权威，力扫的本地推断不覆盖
                WHEN models.matched = 1
                THEN models.base_model
                ELSE excluded.base_model
            END,
            trained_words = COALESCE(NULLIF(excluded.trained_words, ''), NULLIF(excluded.trained_words, '[]'), models.trained_words),
            metadata_raw = COALESCE(NULLIF(excluded.metadata_raw, ''), models.metadata_raw),
            model_type = excluded.model_type,
            -- 细分类型：空不覆盖；已匹配的以 CivitAI type 为权威（力扫的结构推断不覆盖）
            lora_subtype = CASE
                WHEN excluded.lora_subtype IS NULL OR excluded.lora_subtype = '' THEN models.lora_subtype
                WHEN models.matched = 1 THEN models.lora_subtype
                ELSE excluded.lora_subtype
            END,
            folder = excluded.folder,
            cached_at = excluded.cached_at,
            -- 文件能被扫描到 = 它确实在磁盘上；若该记录之前是软删(存档)，说明文件又回来了，
            -- 自动取消"已删除"标记（修复"重新下载/拷回后仍卡在已删除"）
            file_deleted = 0,
            deleted_at = 0
    """

    @staticmethod
    def _upsert_params(model: dict, now: float) -> tuple:
        """构造 _UPSERT_MODEL_SQL 的参数元组（list/dict 字段序列化为 JSON）。"""
        trained_words = model.get("trained_words", [])
        if isinstance(trained_words, list):
            trained_words = json.dumps(trained_words, ensure_ascii=False)
        metadata_raw = model.get("metadata_raw")
        if isinstance(metadata_raw, dict):
            metadata_raw = json.dumps(metadata_raw, ensure_ascii=False)
        return (
            model.get("file_path", ""),
            model.get("file_name", ""),
            model.get("file_ext", ""),
            model.get("file_size", 0),
            model.get("modified", 0),
            model.get("sha256", ""),
            model.get("base_model", "Unknown"),
            trained_words,
            metadata_raw,
            model.get("source", ""),
            model.get("source_url", ""),
            model.get("model_name", ""),
            model.get("model_description", ""),
            model.get("preview_url", ""),
            1 if model.get("matched") else 0,
            model.get("civitai_model_id"),
            model.get("civitai_version_id"),
            model.get("civitai_model_type", ""),
            model.get("creator", ""),
            1 if model.get("nsfw") else 0,
            1 if model.get("favorite") else 0,
            model.get("notes", ""),
            model.get("model_type", ""),
            model.get("lora_subtype", ""),
            model.get("folder", ""),
            now,
        )

    def _apply_upsert(self, conn, model: dict, now: float):
        """在已有连接/事务内 upsert 单条模型并维护 hash_index。"""
        conn.execute(self._UPSERT_MODEL_SQL, self._upsert_params(model, now))
        sha256 = model.get("sha256", "")
        file_path = model.get("file_path", "")
        if sha256 and file_path:
            conn.execute(
                "INSERT OR IGNORE INTO hash_index (sha256, file_path) VALUES (?, ?)",
                (sha256, file_path),
            )

    def upsert_model(self, model: dict):
        """插入或更新单个模型记录"""
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                self._apply_upsert(conn, model, now)
                conn.commit()
            finally:
                conn.close()

    def upsert_models_batch(self, models: list):
        """批量插入或更新模型。单条坏数据只跳过并记日志，不静默拖垮整批。"""
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                for model in models:
                    try:
                        self._apply_upsert(conn, model, now)
                    except sqlite3.Error as e:
                        logger.error(
                            "[Noctyra-MM] upsert 单条失败，已跳过: %s (%s)",
                            model.get("file_path", "?"), e,
                        )
                conn.commit()
            finally:
                conn.close()

    def update_online_info(self, sha256: str, info: dict):
        """通过 SHA256 更新在线匹配信息"""
        now = time.time()
        civitai_tags = info.get("tags", [])
        civitai_tags_json = json.dumps(civitai_tags, ensure_ascii=False) if isinstance(civitai_tags, list) else "[]"

        with self._lock:
            conn = self._connect()
            try:
                # 找到对应的 file_path
                row = conn.execute(
                    "SELECT file_path FROM hash_index WHERE sha256 = ? LIMIT 1",
                    (sha256,)
                ).fetchone()
                if not row:
                    # 尝试直接在 models 表中查找
                    row = conn.execute(
                        "SELECT file_path FROM models WHERE sha256 = ? LIMIT 1",
                        (sha256,)
                    ).fetchone()
                if not row:
                    logger.warning("[Noctyra-MM] 未找到 SHA256=%s 对应的模型", sha256[:10])
                    return

                file_path = row["file_path"]

                # 锁定保护：匹配前快照所有同哈希行里"被用户锁定的自定义字段"的当前值，
                # 让下面的大 UPDATE 照常覆盖，匹配后再把锁定值写回（per file_path，兼容重复文件）
                _PROT = ("model_name", "version_name", "creator", "model_description",
                         "preview_url", "base_model", "trained_words")
                locked_snapshot = []
                for lr in conn.execute(
                    f"SELECT file_path, custom_fields, {', '.join(_PROT)} FROM models WHERE sha256 = ?",
                    (sha256,)
                ).fetchall():
                    try:
                        lf = set(json.loads(lr["custom_fields"] or "[]"))
                    except (TypeError, ValueError):
                        lf = set()
                    keep = {f: lr[f] for f in _PROT if f in lf}
                    if keep:
                        locked_snapshot.append((lr["file_path"], keep))

                # 序列化 preview_images
                preview_images = info.get("preview_images", [])
                if isinstance(preview_images, list):
                    preview_images_json = json.dumps(preview_images, ensure_ascii=False)
                else:
                    preview_images_json = "[]"

                # 更新 trained_words（如果在线数据有的话）
                trained_words = info.get("trained_words", [])
                trained_words_json = json.dumps(trained_words, ensure_ascii=False) if isinstance(trained_words, list) and trained_words else None

                # 从 CivitAI 类型映射到 model_type
                civitai_type = info.get("civitai_model_type", "")
                mapped_type = self._map_civitai_type(civitai_type)
                # LoRA 家族细分（CivitAI type 权威）：LORA→lora / LoCon→lycoris / DoRA→dora
                lora_subtype_val = self._map_lora_subtype(civitai_type)

                civitai_raw = info.get("_raw_data", "")
                if civitai_raw and not isinstance(civitai_raw, str):
                    civitai_raw = json.dumps(civitai_raw, ensure_ascii=False)

                conn.execute("""
                    UPDATE models SET
                        source = ?,
                        source_url = ?,
                        model_name = ?,
                        version_name = ?,
                        model_description = ?,
                        preview_url = ?,
                        preview_images = ?,
                        matched = 1,
                        civitai_model_id = ?,
                        civitai_version_id = ?,
                        civitai_model_type = ?,
                        lora_subtype = CASE WHEN ? != '' THEN ? ELSE lora_subtype END,
                        creator = ?,
                        creator_avatar = ?,
                        nsfw = ?,
                        published_at = ?,
                        comment_count = ?,
                        downloads = ?,
                        rating = ?,
                        rating_count = ?,
                        thumbs_up = ?,
                        -- CivitAI 的 model_type 只在 scanner 尚未分类（空 / 'other'）时写入，
                        -- 否则保留 scanner 的目录名推断，避免 Flux/Qwen 等在 CivitAI 上标为
                        -- "Checkpoint" 的 UNet 模型被污染成 "checkpoint"，进而被整理错位置。
                        model_type = CASE
                            WHEN ? != '' AND (model_type IS NULL OR model_type IN ('', 'other'))
                            THEN ?
                            ELSE model_type
                        END,
                        base_model = CASE WHEN ? != 'Unknown' THEN ? ELSE base_model END,
                        trained_words = CASE WHEN ? IS NOT NULL THEN ? ELSE trained_words END,
                        civitai_tags = ?,
                        civitai_raw = CASE WHEN ? != '' THEN ? ELSE civitai_raw END,
                        civitai_allow_commercial = CASE WHEN ? >= 0 THEN ? ELSE civitai_allow_commercial END,
                        matched_at = ?
                    WHERE sha256 = ?
                """, (
                    info.get("source", ""),
                    info.get("source_url", ""),
                    info.get("model_name", ""),
                    info.get("version_name", ""),
                    info.get("model_description", ""),
                    info.get("preview_url", ""),
                    preview_images_json,
                    info.get("civitai_model_id"),
                    info.get("civitai_version_id"),
                    civitai_type,
                    lora_subtype_val, lora_subtype_val,
                    info.get("creator", ""),
                    info.get("creator_avatar", ""),
                    1 if info.get("nsfw") else 0,
                    info.get("published_at", ""),
                    info.get("comment_count", 0),
                    info.get("downloads", 0),
                    info.get("rating", 0),
                    info.get("rating_count", 0),
                    info.get("thumbs_up", 0),
                    mapped_type, mapped_type,
                    info.get("base_model", "Unknown"), info.get("base_model", "Unknown"),
                    trained_words_json, trained_words_json,
                    civitai_tags_json,
                    civitai_raw, civitai_raw,
                    int(info.get("civitai_allow_commercial", -1)),
                    int(info.get("civitai_allow_commercial", -1)),
                    now,
                    sha256,   # 按 sha256 更新所有同哈希行：重复文件（同内容多份）一起继承匹配数据
                ))

                # 把"被用户锁定的自定义字段"写回（覆盖刚被匹配数据冲掉的那几列）
                for fp, keep in locked_snapshot:
                    assigns = ", ".join(f"{k} = ?" for k in keep)  # k 来自硬编码 _PROT，非用户输入
                    conn.execute(f"UPDATE models SET {assigns} WHERE file_path = ?",
                                 list(keep.values()) + [fp])

                # 归档元数据
                self._archive_metadata(conn, sha256, info)

                conn.commit()
            finally:
                conn.close()

    def update_hf_info(self, sha256: str, hf_info: dict):
        """更新 HuggingFace 来源信息（不覆盖 CivitAI 主字段）"""
        with self._lock:
            conn = self._connect()
            try:
                # 找到对应的 file_path
                row = conn.execute(
                    "SELECT file_path, source FROM models WHERE sha256 = ? LIMIT 1",
                    (sha256,)
                ).fetchone()
                if not row:
                    return

                file_path = row["file_path"]
                current_source = row["source"]

                hf_tags = hf_info.get("tags", [])
                hf_tags_json = json.dumps(hf_tags, ensure_ascii=False) if isinstance(hf_tags, list) else "[]"

                hf_raw = hf_info.get("_raw_data", "")
                if hf_raw and not isinstance(hf_raw, str):
                    hf_raw = json.dumps(hf_raw, ensure_ascii=False)

                conn.execute("""
                    UPDATE models SET
                        hf_repo_id = ?,
                        hf_url = ?,
                        hf_downloads = ?,
                        hf_likes = ?,
                        hf_author = ?,
                        hf_description = ?,
                        hf_tags = ?,
                        hf_last_modified = ?,
                        hf_match_type = ?,
                        hf_raw = CASE WHEN ? != '' THEN ? ELSE hf_raw END
                    WHERE sha256 = ?
                """, (
                    hf_info.get("repo_id", ""),
                    hf_info.get("source_url", ""),
                    hf_info.get("downloads", 0),
                    hf_info.get("likes", 0),
                    hf_info.get("author", ""),
                    hf_info.get("model_description", ""),
                    hf_tags_json,
                    hf_info.get("last_modified", ""),
                    hf_info.get("match_type", ""),
                    hf_raw, hf_raw,
                    sha256,   # 同哈希副本一起更新
                ))

                # 如果还没有主来源匹配，则用 HF 作为主来源
                if not current_source:
                    conn.execute("""
                        UPDATE models SET
                            source = 'huggingface',
                            source_url = ?,
                            model_name = CASE WHEN model_name = '' THEN ? ELSE model_name END,
                            base_model = CASE WHEN base_model = 'Unknown' AND ? != 'Unknown' THEN ? ELSE base_model END,
                            matched = 1,
                            matched_at = ?
                        WHERE sha256 = ?
                    """, (
                        hf_info.get("source_url", ""),
                        hf_info.get("model_name", ""),
                        hf_info.get("base_model", "Unknown"), hf_info.get("base_model", "Unknown"),
                        time.time(),
                        sha256,   # 同哈希副本一起更新
                    ))

                # 归档 HF 元数据
                self._archive_metadata(conn, sha256, hf_info, source="huggingface")

                conn.commit()
            finally:
                conn.close()

    # 在线元数据列（与 update_online_info 写的口径一致）。不含 file_path/file_name/folder/
    # favorite/sha256/notes/user_* 等文件级字段——重复文件这些各自独立，绝不复制。
    _DUP_ONLINE_COLS = [
        "source", "source_url", "model_name", "version_name", "model_description",
        "preview_url", "preview_images", "civitai_model_id", "civitai_version_id",
        "civitai_model_type", "creator", "creator_avatar", "nsfw", "published_at",
        "comment_count", "downloads", "rating", "rating_count", "thumbs_up",
        "base_model", "trained_words", "civitai_tags", "civitai_raw",
        "civitai_allow_commercial", "matched_at",
        "hf_repo_id", "hf_url", "hf_downloads", "hf_likes", "hf_author",
        "hf_description", "hf_tags", "hf_last_modified", "hf_match_type", "hf_raw",
    ]

    def backfill_duplicate_matches(self) -> int:
        """把已匹配模型的在线元数据补给【同 sha256】的未匹配副本（同内容多份文件一起受益）。
        返回回填的行数。只复制在线元数据列，不动文件级字段（路径/收藏/笔记等）。"""
        n = 0
        with self._lock:
            conn = self._connect()
            try:
                have = {r[1] for r in conn.execute("PRAGMA table_info(models)")}
                cols = [c for c in self._DUP_ONLINE_COLS if c in have]
                if not cols:
                    return 0
                # 同一 sha256 既有已匹配、又有未匹配的（才需要回填）
                shas = [r[0] for r in conn.execute(
                    "SELECT sha256 FROM models WHERE sha256 IS NOT NULL AND sha256 != '' "
                    "GROUP BY sha256 HAVING MAX(matched) = 1 AND MIN(matched) = 0"
                ).fetchall()]
                if not shas:
                    return 0
                sel = ", ".join(cols)
                set_clause = ", ".join(f"{c} = ?" for c in cols) + ", matched = 1"
                for sha in shas:
                    src = conn.execute(
                        f"SELECT {sel} FROM models WHERE sha256 = ? AND matched = 1 LIMIT 1",
                        (sha,),
                    ).fetchone()
                    if not src:
                        continue
                    cur = conn.execute(
                        f"UPDATE models SET {set_clause} WHERE sha256 = ? AND matched = 0",
                        (*[src[c] for c in cols], sha),
                    )
                    n += cur.rowcount
                if n:
                    conn.commit()
            finally:
                conn.close()
        if n:
            logger.info("[Noctyra-MM] 重复文件回填匹配数据：%d 行", n)
        return n

    def update_hash(self, file_path: str, sha256: str):
        """更新模型的 SHA256"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE models SET sha256 = ? WHERE file_path = ?",
                    (sha256, file_path)
                )
                if sha256:
                    conn.execute(
                        "INSERT OR IGNORE INTO hash_index (sha256, file_path) VALUES (?, ?)",
                        (sha256, file_path)
                    )
                conn.commit()
            finally:
                conn.close()

    def repair_folders(self, model_roots) -> int:
        """按 file_path 反推正确的 folder 字段，修复历史脏数据。

        scanner 的增量扫描会跳过 mtime 未变的文件，老的错误 folder（比如
        下载时 basename(save_dir) 漏掉 root 前缀）不会被覆盖。此方法专门
        一次性刷一遍，让所有行的 folder 和 scanner 口径对齐。
        """
        if not model_roots:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT file_path, folder FROM models WHERE file_deleted = 0"
                ).fetchall()
                fixes = []
                for r in rows:
                    fp = r["file_path"]
                    if not fp:
                        continue
                    fp_nc = os.path.normcase(os.path.normpath(fp))
                    current_root = None
                    for root in model_roots:
                        r_nc = os.path.normcase(os.path.normpath(root))
                        if fp_nc.startswith(r_nc + os.sep):
                            current_root = root
                            break
                    if not current_root:
                        continue
                    folder_rel = os.path.relpath(os.path.dirname(fp), current_root)
                    if folder_rel == ".":
                        new_folder = os.path.basename(current_root)
                    else:
                        new_folder = (os.path.basename(current_root) + "/" +
                                      folder_rel.replace("\\", "/"))
                    if new_folder != (r["folder"] or ""):
                        fixes.append((new_folder, fp))
                if fixes:
                    conn.executemany(
                        "UPDATE models SET folder = ? WHERE file_path = ?", fixes
                    )
                    conn.commit()
                    logger.info("[Noctyra-MM] 修复 %d 个 folder 字段", len(fixes))
                return len(fixes)
            finally:
                conn.close()

    def remove_missing(self, existing_paths: set):
        """清理数据库中已不存在于磁盘的模型记录

        智能检测文件移动：如果旧路径消失但新路径有相同 SHA256 的文件，
        视为文件移动，更新路径并保留所有元数据，避免重新匹配。
        """
        normalized = {os.path.normpath(p) for p in existing_paths}
        # 反向映射：normpath -> 原始路径
        norm_to_orig = {os.path.normpath(p): p for p in existing_paths}

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT file_path, sha256, file_deleted FROM models").fetchall()
                missing = [r for r in rows
                           if not r["file_deleted"] and os.path.normpath(r["file_path"]) not in normalized]
                # 存档(软删)记录：文件本就不在扫描根。仅当同哈希文件在新路径出现（你从网盘下回放到别处）
                # 才迁移合并、顺带变回在库，避免留"已删除"残影；否则原样保留（存档记录绝不清）。
                archived_missing = [r for r in rows
                                    if r["file_deleted"] and r["sha256"]
                                    and os.path.normpath(r["file_path"]) not in normalized]

                if not missing and not archived_missing:
                    return

                # 建立新路径的 SHA256 索引（从已在数据库中的记录获取）
                existing_rows = conn.execute(
                    "SELECT file_path, sha256 FROM models WHERE sha256 IS NOT NULL AND sha256 != ''"
                ).fetchall()
                new_path_by_hash = {}
                for r in existing_rows:
                    np = os.path.normpath(r["file_path"])
                    if np in normalized and r["sha256"]:
                        new_path_by_hash.setdefault(r["sha256"], r["file_path"])

                moved = 0
                to_delete = []

                for r in missing:
                    old_path = r["file_path"]
                    sha256 = r["sha256"]

                    if sha256 and sha256 in new_path_by_hash:
                        new_path = new_path_by_hash[sha256]
                        # 把旧记录的元数据合并到新路径的记录上
                        self._merge_moved_record(conn, old_path, new_path)
                        moved += 1
                    else:
                        to_delete.append(old_path)

                # 存档记录的哈希若在新路径出现 → 迁移合并（_merge_moved_record 不动 file_deleted，
                # 新记录是扫描出的在库态，等于"自动归位"）；没出现就什么都不做、保留存档。
                for r in archived_missing:
                    sha256 = r["sha256"]
                    if sha256 in new_path_by_hash:
                        self._merge_moved_record(conn, r["file_path"], new_path_by_hash[sha256])
                        moved += 1

                if to_delete:
                    batch_size = 500
                    for i in range(0, len(to_delete), batch_size):
                        batch = to_delete[i:i + batch_size]
                        placeholders = ",".join("?" * len(batch))
                        conn.execute(f"DELETE FROM models WHERE file_path IN ({placeholders})", batch)
                        conn.execute(f"DELETE FROM model_tags WHERE file_path IN ({placeholders})", batch)
                        conn.execute(f"DELETE FROM hash_index WHERE file_path IN ({placeholders})", batch)

                conn.commit()
                if moved:
                    logger.info("[Noctyra-MM] 检测到 %d 个文件移动，已更新路径", moved)
                if to_delete:
                    logger.info("[Noctyra-MM] 清理了 %d 个已删除的模型记录", len(to_delete))
            finally:
                conn.close()

    def _merge_moved_record(self, conn, old_path: str, new_path: str):
        """将旧路径记录的元数据合并到新路径记录，然后删除旧记录"""
        old = conn.execute("SELECT * FROM models WHERE file_path = ?", (old_path,)).fetchone()
        if not old:
            return

        # 需要保留的在线匹配字段（新记录可能没有这些数据）
        fields_to_merge = [
            "source", "source_url", "model_name", "model_description", "preview_url",
            "preview_images", "matched", "matched_at",
            "civitai_model_id", "civitai_version_id", "civitai_model_type",
            "creator", "creator_avatar", "nsfw",
            "base_model", "trained_words", "version_name",
            "thumbs_up", "downloads", "rating", "rating_count", "comment_count",
            "published_at", "civitai_tags",
            "hf_repo_id", "hf_url", "hf_author", "hf_description",
            "hf_downloads", "hf_likes", "hf_last_modified", "hf_tags",
            "hf_match_type",
            "favorite", "notes",
            "civitai_raw", "hf_raw",
        ]

        updates = []
        values = []
        for f in fields_to_merge:
            old_val = old[f] if f in old.keys() else None
            if old_val is not None and old_val != "" and old_val != 0:
                updates.append(f"{f} = ?")
                values.append(old_val)

        if updates:
            values.append(new_path)
            conn.execute(
                f"UPDATE models SET {', '.join(updates)} WHERE file_path = ?",
                values
            )

        # 迁移标签
        old_tags = conn.execute("SELECT tag FROM model_tags WHERE file_path = ?", (old_path,)).fetchall()
        for t in old_tags:
            conn.execute("INSERT OR IGNORE INTO model_tags (file_path, tag) VALUES (?, ?)", (new_path, t["tag"]))

        # 删除旧记录
        conn.execute("DELETE FROM models WHERE file_path = ?", (old_path,))
        conn.execute("DELETE FROM model_tags WHERE file_path = ?", (old_path,))
        conn.execute("DELETE FROM hash_index WHERE file_path = ?", (old_path,))

    def normalize_base_models(self) -> int:
        """把库里 base_model 的别名归并成规范名（Qwen/qwen_image→Qwen Image、SD1.5→SD 1.5、
        SDXL 1.0→SDXL …）。启动时调用一次自动并掉历史多写法。幂等（规范值不会再变）。"""
        from .base_models import normalize_base_model
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT base_model FROM models WHERE base_model IS NOT NULL AND base_model != ''"
                ).fetchall()
                changed = 0
                for r in rows:
                    bm = r["base_model"]
                    canon = normalize_base_model(bm)
                    if canon and canon != bm:
                        conn.execute("UPDATE models SET base_model = ? WHERE base_model = ?", (canon, bm))
                        changed += 1
                if changed:
                    conn.commit()
                    logger.info("[Noctyra-MM] base_model 归一化：合并 %d 个别名写法", changed)
                return changed
            finally:
                conn.close()

    # ========== 查询操作 ==========

    def get_all(self, filters: dict = None, sort_by: str = "file_name",
                sort_dir: str = "", page: int = 1, page_size: int = 40) -> Tuple[List[dict], int]:
        """分页查询模型列表

        Args:
            filters: {"search", "folder", "base_model", "source"}
            sort_by: "file_name" | "file_size" | "modified" | "base_model" | "model_name" | "usage_count" | "published_at"
            sort_dir: "asc" 或 "desc"，为空时使用各字段默认方向
            page: 页码（从 1 开始）
            page_size: 每页条数

        Returns:
            (models_list, total_count)
        """
        filters = filters or {}
        where_clauses = []
        params = []

        if filters.get("search"):
            if getattr(self, "_fts_available", True):
                # FTS5 MATCH：对每个 term 加前缀通配 `*` 支持子串式查找
                # 转义 " 和 - 等 FTS 特殊字符
                raw = filters["search"].strip()
                # 简易净化：只留字母数字空格下划线点斜杠中文；其他字符转空格
                cleaned = re.sub(r"[^\w\s\-/\u4e00-\u9fff]", " ", raw)
                terms = [t for t in cleaned.split() if t]
                if terms:
                    # 每个词加前缀通配：foo* AND bar*
                    query = " ".join(f'"{t}"*' for t in terms)
                    where_clauses.append(
                        "rowid IN (SELECT rowid FROM models_fts WHERE models_fts MATCH ?)"
                    )
                    params.append(query)
            else:
                # FTS5 不可用时 fallback 到 LIKE（覆盖字段更少）
                where_clauses.append("(file_name LIKE ? OR model_name LIKE ?)")
                pattern = f"%{filters['search']}%"
                params.extend([pattern, pattern])

        if filters.get("folder"):
            where_clauses.append("(folder = ? OR folder LIKE ?)")
            params.extend([filters["folder"], filters["folder"] + "/%"])

        if filters.get("base_model"):
            where_clauses.append("base_model = ?")
            params.append(filters["base_model"])

        if filters.get("model_type"):
            where_clauses.append("model_type = ?")
            params.append(filters["model_type"])

        # LoRA 家族细分筛选（lora/lycoris/dora）：不影响 model_type，仅过滤
        if filters.get("lora_subtype"):
            where_clauses.append("lora_subtype = ?")
            params.append(filters["lora_subtype"])

        if filters.get("source"):
            source = filters["source"]
            if source == "deleted":
                where_clauses.append("file_deleted = 1")
            elif source == "unmatched":
                where_clauses.append("matched = 0")
            elif source == "favorite":
                where_clauses.append("favorite = 1")
            elif source == "updatable":
                where_clauses.append("update_available = 1")
            elif source == "corrupt":
                where_clauses.append("file_corrupt = 1")
            else:
                where_clauses.append("source = ?")
                params.append(source)

        if filters.get("source") != "deleted":
            where_clauses.append("file_deleted = 0")

        if filters.get("sfw_only"):
            where_clauses.append("nsfw = 0")

        if filters.get("tag"):
            where_clauses.append(
                "file_path IN (SELECT file_path FROM model_tags WHERE tag = ?)"
            )
            params.append(filters["tag"])

        # License 筛选：commercial=1 / personal=0 / 其他值忽略
        lic = filters.get("license")
        if lic in ("commercial", "personal"):
            where_clauses.append("civitai_allow_commercial = ?")
            params.append(1 if lic == "commercial" else 0)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # 排序
        direction = sort_dir.upper() if sort_dir.lower() in ("asc", "desc") else ""
        if direction:
            dir_sql = direction
            sort_map = {
                "file_name": f"file_name COLLATE NOCASE {dir_sql}",
                "model_name": f"COALESCE(NULLIF(model_name,''), file_name) COLLATE NOCASE {dir_sql}",
                "file_size": f"file_size {dir_sql}",
                "modified": f"modified {dir_sql}",
                "base_model": f"base_model COLLATE NOCASE {dir_sql}, file_name COLLATE NOCASE {dir_sql}",
                "usage_count": f"usage_count {dir_sql}, file_name COLLATE NOCASE ASC",
                # 发布时间（CivitAI publishedAt，ISO 字符串可按字典序）；空值(未匹配)永远垫底
                "published_at": f"(published_at = '' OR published_at IS NULL) ASC, published_at {dir_sql}, file_name COLLATE NOCASE ASC",
            }
        else:
            sort_map = {
                "file_name": "file_name COLLATE NOCASE ASC",
                "model_name": "COALESCE(NULLIF(model_name,''), file_name) COLLATE NOCASE ASC",
                "file_size": "file_size DESC",
                "modified": "modified DESC",
                "base_model": "base_model COLLATE NOCASE ASC, file_name COLLATE NOCASE ASC",
                "usage_count": "usage_count DESC, file_name COLLATE NOCASE ASC",
                # 默认：发布时间从新到旧；空值(未匹配)垫底
                "published_at": "(published_at = '' OR published_at IS NULL) ASC, published_at DESC, file_name COLLATE NOCASE ASC",
            }
        order_sql = sort_map.get(sort_by, sort_map["file_name"])
        # 唯一主键兜底：排序列可能并列（同名文件、相同大小/时间等），
        # 不加唯一 tiebreaker 时 LIMIT/OFFSET 在相邻页可能重复或漏行（前端表现为“滑到底出现两份”）。
        # file_path 是 models 的 PRIMARY KEY，用 BINARY 排序保证全序稳定。
        order_sql = f"{order_sql}, file_path ASC"

        with self._lock:
            conn = self._connect(readonly=True)
            try:
                # 总数
                total = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM models WHERE {where_sql}", params
                ).fetchone()["cnt"]

                # 列表查询剔除三个大 JSON 列（metadata_raw/civitai_raw/hf_raw，每行 10-100KB）：
                # _row_to_dict 反正都会 pop 掉、前端卡片也不用（hf_gated 仅详情用），
                # 用 SELECT * 会把它们整页(80 行)拉进内存还白解析一次 hf_raw → 翻页变卡。
                if getattr(self, "_list_cols_sql", None) is None:
                    _all = [r[1] for r in conn.execute("PRAGMA table_info(models)")]
                    # model_description/hf_description(合计占列表载荷 ~45%) + creator_avatar
                    # 仅详情弹窗用（走 fetchModelDetail 全量重取），列表/卡片不读 → 一并裁掉减小载荷
                    _skip = {"metadata_raw", "civitai_raw", "hf_raw",
                             "model_description", "hf_description", "creator_avatar"}
                    self._list_cols_sql = ", ".join(c for c in _all if c not in _skip)
                cols = self._list_cols_sql

                # 分页数据
                offset = (page - 1) * page_size
                rows = conn.execute(
                    f"SELECT {cols} FROM models WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                    params + [page_size, offset]
                ).fetchall()

                models = [self._row_to_dict(r) for r in rows]

                # 附加 tags
                if models:
                    tags_map: Dict[str, List[str]] = {}
                    file_paths = [m["file_path"] for m in models]
                    batch_size = 500
                    for i in range(0, len(file_paths), batch_size):
                        batch = file_paths[i:i + batch_size]
                        placeholders = ",".join("?" * len(batch))
                        tag_rows = conn.execute(
                            f"SELECT file_path, tag FROM model_tags WHERE file_path IN ({placeholders})",
                            batch
                        ).fetchall()
                        for tr in tag_rows:
                            tags_map.setdefault(tr["file_path"], []).append(tr["tag"])
                    for m in models:
                        m["tags"] = tags_map.get(m["file_path"], [])

                return models, total
            finally:
                conn.close()

    def get_models_by_names(self, names: list) -> dict:
        """给一组 ComfyUI widget 选项名（相对路径或 basename），返回每个名字
        匹配到的模型摘要 {name: summary}。未匹配的 name 不出现在结果里。

        匹配策略：basename 命中为主（O(1)）；同名多文件时再用完整相对路径后缀精确选。
        给画布上的"模型选择器"用，只回传渲染卡片需要的轻量字段。
        """
        if not names:
            return {}
        basenames = set()
        for n in names:
            if n:
                basenames.add(os.path.basename(str(n).replace("\\", "/")))
        if not basenames:
            return {}

        # 分批查询：basenames 可能 >999（大型工作流的 widget 选项数），
        # 超过 SQLite 参数上限会抛 OperationalError
        basenames_list = list(basenames)
        # 画布选择器是悬浮 hover UI，只取渲染卡片要的轻量列，避免 SELECT * 后逐行
        # _row_to_dict 把 preview_images/hf_tags/civitai_tags/hf_raw 全 JSON 解一遍
        cols = ("file_name, file_path, model_name, base_model, favorite, "
                "matched, trained_words, preview_url, preview_images, nsfw")
        rows = []
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                for i in range(0, len(basenames_list), 500):
                    chunk = basenames_list[i:i + 500]
                    placeholders = ",".join("?" * len(chunk))
                    rows.extend(conn.execute(
                        f"SELECT {cols} FROM models WHERE file_name IN ({placeholders})",
                        tuple(chunk),
                    ).fetchall())
            finally:
                conn.close()

        def _lean(r: sqlite3.Row) -> dict:
            try:
                tw = json.loads(r["trained_words"]) if r["trained_words"] else []
            except (json.JSONDecodeError, TypeError):
                tw = []
            try:
                pi = json.loads(r["preview_images"]) if r["preview_images"] else []
                pi = [p for p in pi if isinstance(p, dict)] if isinstance(pi, list) else []
            except (json.JSONDecodeError, TypeError):
                pi = []
            return {
                "file_name": r["file_name"],
                "file_path": r["file_path"] or "",
                "model_name": r["model_name"] or "",
                "base_model": r["base_model"] or "",
                "favorite": bool(r["favorite"]),
                "matched": bool(r["matched"]),
                "trained_words": tw if isinstance(tw, list) else [],
                "preview_images": pi,
                "preview_url": r["preview_url"] or "",
                "nsfw": bool(r["nsfw"]),
            }

        by_name: dict = {}
        for r in rows:
            d = _lean(r)
            by_name.setdefault(d["file_name"], []).append(d)

        def _summary(d: dict) -> dict:
            purl = d.get("preview_url") or ""
            pi = d.get("preview_images") or []
            # 主预览类型：优先非视频（与 _row_to_dict 一致）
            primary = next((p for p in pi if p.get("type") != "video"), None) or (pi[0] if pi else None)
            ptype = (primary.get("type") if primary else "image") or "image"
            if not purl:
                # 没有主预览 URL 时退回预览图列表：优先静态图（加载快），全是视频才用视频
                pick = next((i for i in pi if i.get("type") != "video" and i.get("url")), None) \
                    or next((i for i in pi if i.get("url")), None)
                if pick:
                    purl = pick["url"]
                    ptype = "video" if pick.get("type") == "video" else "image"
            if purl.startswith("sidecar://"):  # 本地 sidecar 图无远程 URL，置空显示占位
                purl = ""
                ptype = "image"
            # 与管理页卡片口径一致：用"主预览(封面)"的 nsfw_level，缺失再回退 model.nsfw → 16。
            # 不用所有预览的最大值——否则封面 SFW、但混了 NSFW 样图的模型，选择器会比管理页多打码 → 两边不一致。
            primary_level = int(primary.get("nsfw_level", 0) or 0) if primary else 0
            nsfw_level = primary_level or (16 if d.get("nsfw") else 0)
            return {
                "name": d.get("model_name") or d.get("file_name"),
                "file_name": d.get("file_name"),
                "file_path": d.get("file_path", ""),
                "base_model": d.get("base_model") or "",
                "preview_url": purl,
                "preview_type": ptype,
                "nsfw_level": nsfw_level,
                "nsfw": bool(d.get("nsfw")),   # 模型级 NSFW 标志：选择器"仅 SFW"按它隐藏，与管理页 nsfw=0 一致
                "favorite": bool(d.get("favorite", False)),
                "matched": bool(d.get("matched", False)),
                "trained_words": d.get("trained_words") or [],
            }

        result: dict = {}
        for n in names:
            if not n:
                continue
            bn = os.path.basename(str(n).replace("\\", "/"))
            candidates = by_name.get(bn)
            if not candidates:
                continue
            if len(candidates) == 1:
                match = candidates[0]
            else:
                norm_n = str(n).replace("\\", "/").lower()
                match = next(
                    (c for c in candidates
                     if c["file_path"].replace("\\", "/").lower().endswith(norm_n)),
                    candidates[0],
                )
            result[n] = _summary(match)
        return result

    def get_by_path(self, file_path: str) -> Optional[dict]:
        """通过文件路径获取单个模型（含完整预览图数据）"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                row = conn.execute("SELECT * FROM models WHERE file_path = ?", (file_path,)).fetchone()
                if not row:
                    return None
                model = self._row_to_dict(row, include_images=True)
                tag_rows = conn.execute(
                    "SELECT tag FROM model_tags WHERE file_path = ?", (file_path,)
                ).fetchall()
                model["tags"] = [t["tag"] for t in tag_rows]
                return model
            finally:
                conn.close()

    def get_by_hash(self, sha256: str) -> Optional[dict]:
        """通过 SHA256 获取模型（含完整预览图数据）"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                row = conn.execute("SELECT * FROM models WHERE sha256 = ? LIMIT 1", (sha256,)).fetchone()
                if not row:
                    return None
                model = self._row_to_dict(row, include_images=True)
                tag_rows = conn.execute(
                    "SELECT tag FROM model_tags WHERE file_path = ?", (model["file_path"],)
                ).fetchall()
                model["tags"] = [t["tag"] for t in tag_rows]
                return model
            finally:
                conn.close()

    def query_by_version_id(self, version_id: int, include_deleted: bool = False) -> List[dict]:
        """通过 CivitAI version_id 查询已下载的模型（供浏览器扩展批量检查）。
        include_deleted=True 时连软删（留记录）的也返回，结果带 file_deleted 字段。"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                sql = "SELECT file_path, file_name, model_name, sha256, civitai_version_id, civitai_model_id, file_deleted FROM models WHERE civitai_version_id = ?"
                if not include_deleted:
                    sql += " AND file_deleted = 0"
                rows = conn.execute(sql, (version_id,)).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def query_by_model_id(self, model_id: int, include_deleted: bool = False) -> List[dict]:
        """通过 CivitAI model_id 查询已下载的所有版本。
        include_deleted=True 时连软删（留记录）的也返回，结果带 file_deleted 字段。"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                sql = "SELECT file_path, file_name, model_name, version_name, sha256, civitai_version_id, civitai_model_id, file_deleted FROM models WHERE civitai_model_id = ?"
                if not include_deleted:
                    sql += " AND file_deleted = 0"
                rows = conn.execute(sql, (model_id,)).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_model_version_total(self, model_id: int) -> int:
        """该 CivitAI 模型在线共有几个版本（从本地任一记录的 civitai_raw.model.modelVersions 取）。
        取不到返回 0。给浏览器扩展判"本地版本是否下全"用。"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                row = conn.execute(
                    "SELECT civitai_raw FROM models WHERE civitai_model_id = ? AND civitai_raw != '' LIMIT 1",
                    (model_id,)
                ).fetchone()
            finally:
                conn.close()
        if not row or not row["civitai_raw"]:
            return 0
        try:
            mv = (json.loads(row["civitai_raw"]).get("model") or {}).get("modelVersions")
            return len(mv) if isinstance(mv, list) else 0
        except (TypeError, ValueError):
            return 0

    def _filter_existing_ids(self, col: str, ids) -> set:
        """给一组 id，返回其中在本地库存在（未软删）的子集。col 限定为内部列名。"""
        clean = []
        for v in ids:
            try:
                clean.append(int(v))
            except (TypeError, ValueError):
                pass
        if not clean:
            return set()
        found = set()
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                for i in range(0, len(clean), 900):  # 留余量给 999 参数上限
                    chunk = clean[i:i + 900]
                    ph = ",".join("?" * len(chunk))
                    rows = conn.execute(
                        f"SELECT DISTINCT {col} FROM models "
                        f"WHERE file_deleted = 0 AND {col} IN ({ph})",
                        tuple(chunk),
                    ).fetchall()
                    found.update(r[0] for r in rows)
            finally:
                conn.close()
        return found

    def filter_existing_version_ids(self, version_ids) -> set:
        """一次查出本地存在的 CivitAI version_id 子集，给图库资源完整度批量判断，
        替代逐资源 query_by_version_id 的 N+1。"""
        return self._filter_existing_ids("civitai_version_id", version_ids)

    def filter_existing_model_ids(self, model_ids) -> set:
        """一次查出本地存在的 CivitAI model_id 子集（用途同上）。"""
        return self._filter_existing_ids("civitai_model_id", model_ids)

    def get_local_versions(self, civitai_model_id: int, exclude_path: str = "") -> List[dict]:
        """获取同一 CivitAI 模型的其他本地版本"""
        if not civitai_model_id:
            return []
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("""
                    SELECT file_path, file_name, version_name, base_model,
                           file_size, civitai_version_id, preview_url
                    FROM models
                    WHERE civitai_model_id = ? AND file_path != ?
                    ORDER BY civitai_version_id DESC
                """, (civitai_model_id, exclude_path)).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_related(self, file_path: str, limit: int = 8) -> List[dict]:
        """获取相关模型：相同 base_model 但不同类型的模型"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                current = conn.execute(
                    "SELECT base_model, model_type FROM models WHERE file_path = ?",
                    (file_path,)
                ).fetchone()
                if not current or not current["base_model"] or current["base_model"] == "Unknown":
                    return []

                rows = conn.execute("""
                    SELECT file_path, file_name, model_name, base_model, model_type,
                           civitai_model_type, preview_url, file_size, source
                    FROM models
                    WHERE base_model = ? AND file_path != ? AND model_type != ?
                    ORDER BY usage_count DESC, file_name COLLATE NOCASE ASC
                    LIMIT ?
                """, (current["base_model"], file_path, current["model_type"] or "", limit)).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_unmatched(self, include_rematch: bool = False) -> List[dict]:
        """获取需要匹配的模型

        Args:
            include_rematch: True = 包含所有需要补充信息的模型（缺 CivitAI 详情或缺 HF 数据）
        """
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                if include_rematch:
                    rows = conn.execute("""
                        SELECT * FROM models
                        WHERE sha256 != '' AND sha256 IS NOT NULL AND file_deleted = 0
                    """).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM models WHERE matched = 0 AND sha256 != '' AND sha256 IS NOT NULL AND file_deleted = 0"
                    ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_folders(self) -> List[dict]:
        """获取文件夹列表及每个文件夹的模型数量"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT folder, COUNT(*) as count FROM models WHERE file_deleted = 0 GROUP BY folder ORDER BY folder"
                ).fetchall()
                return [{"folder": r["folder"], "count": r["count"]} for r in rows]
            finally:
                conn.close()

    def get_tags(self, limit: int = 50) -> List[dict]:
        """获取 Top N 标签及数量"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT tag, COUNT(*) as count FROM model_tags GROUP BY tag ORDER BY count DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                return [{"tag": r["tag"], "count": r["count"]} for r in rows]
            finally:
                conn.close()

    def add_tags(self, file_path: str, tags: List[str]):
        """为模型添加标签"""
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO model_tags (file_path, tag) VALUES (?, ?)",
                    [(file_path, t.strip()) for t in tags if t.strip()]
                )
                conn.commit()
            finally:
                conn.close()

    def remove_tag(self, file_path: str, tag: str):
        """删除模型的一个标签"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM model_tags WHERE file_path = ? AND tag = ?",
                    (file_path, tag)
                )
                conn.commit()
            finally:
                conn.close()

    def set_tags(self, file_path: str, tags: List[str]):
        """替换模型的全部标签（一次性覆盖）"""
        cleaned = []
        seen = set()
        for t in tags:
            s = t.strip() if isinstance(t, str) else ""
            if s and s not in seen:
                cleaned.append(s)
                seen.add(s)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM model_tags WHERE file_path = ?", (file_path,))
                if cleaned:
                    conn.executemany(
                        "INSERT OR IGNORE INTO model_tags (file_path, tag) VALUES (?, ?)",
                        [(file_path, t) for t in cleaned]
                    )
                conn.commit()
            finally:
                conn.close()

    def aggregate_trained_words(self, limit: int = 500, min_count: int = 1) -> list:
        """汇总所有模型的 trained_words，返回 [{word, count, model_types}] 按 count 降序。

        trained_words 存成 JSON 字符串列表；SQLite 没有 JSON_EACH 的跨行聚合能力，
        所以在 Python 侧做聚合。对于超大库（>10K 模型）可能慢，但常见量级完全 OK。
        """
        conn = self._connect(readonly=True)
        try:
            rows = conn.execute(
                "SELECT trained_words, model_type FROM models "
                "WHERE file_deleted = 0 AND trained_words != '' AND trained_words != '[]'"
            ).fetchall()
        finally:
            conn.close()

        counts = {}  # word -> {count, model_types: set}
        for r in rows:
            try:
                words = json.loads(r["trained_words"] or "[]")
            except (ValueError, TypeError):
                continue
            if not isinstance(words, list):
                continue
            mtype = (r["model_type"] or "").strip().lower() or "other"
            for w in words:
                if not isinstance(w, (str, int, float)):
                    continue
                w = str(w).strip()
                if not w:
                    continue
                entry = counts.get(w)
                if entry is None:
                    entry = {"word": w, "count": 0, "model_types": set()}
                    counts[w] = entry
                entry["count"] += 1
                entry["model_types"].add(mtype)

        # 过滤 + 排序 + 转 list
        out = [
            {
                "word": e["word"],
                "count": e["count"],
                "model_types": sorted(e["model_types"]),
            }
            for e in counts.values()
            if e["count"] >= min_count
        ]
        out.sort(key=lambda x: (-x["count"], x["word"].lower()))
        return out[:limit]

    def get_base_models(self) -> List[str]:
        """获取所有不同的 base model"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT DISTINCT base_model FROM models WHERE base_model != '' AND file_deleted = 0 ORDER BY base_model"
                ).fetchall()
                return [r["base_model"] for r in rows]
            finally:
                conn.close()

    def get_base_model_stats(self) -> list:
        """各 base_model 的条目数，按数量倒序"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("""
                    SELECT COALESCE(NULLIF(base_model, ''), 'Unknown') AS name,
                           COUNT(*) AS count
                    FROM models
                    WHERE file_deleted = 0
                    GROUP BY name
                    ORDER BY count DESC, name COLLATE NOCASE ASC
                """).fetchall()
                return [{"name": r["name"], "count": r["count"]} for r in rows]
            finally:
                conn.close()

    def get_statistics(self) -> dict:
        """统计页聚合：总览 / 按类型 / 按基础模型 / 来源 / NSFW / 存储 / 使用。单次连接批量算。"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                def scalar(sql, params=()):
                    row = conn.execute(sql, params).fetchone()
                    return list(row)[0] if row else 0

                def fetch(sql, params=()):
                    return conn.execute(sql, params).fetchall()

                W = "file_deleted = 0"
                total = scalar(f"SELECT COUNT(*) FROM models WHERE {W}")
                matched = scalar(f"SELECT COUNT(*) FROM models WHERE matched = 1 AND {W}")
                deleted = scalar("SELECT COUNT(*) FROM models WHERE file_deleted = 1")
                total_bytes = scalar(f"SELECT COALESCE(SUM(file_size), 0) FROM models WHERE {W}")
                nsfw = scalar(f"SELECT COUNT(*) FROM models WHERE nsfw = 1 AND {W}")
                favorites = scalar(f"SELECT COUNT(*) FROM models WHERE favorite = 1 AND {W}")

                # 按类型：数量 + 存储
                by_type = [
                    {"type": r["model_type"] or "other", "count": r["c"], "bytes": r["s"]}
                    for r in fetch(
                        f"SELECT model_type, COUNT(*) c, COALESCE(SUM(file_size),0) s "
                        f"FROM models WHERE {W} GROUP BY model_type ORDER BY c DESC"
                    )
                ]
                # 按基础模型（Top 15）
                by_base_model = [
                    {"name": r["name"], "count": r["c"]}
                    for r in fetch(
                        f"SELECT COALESCE(NULLIF(base_model,''),'Unknown') name, COUNT(*) c "
                        f"FROM models WHERE {W} GROUP BY name ORDER BY c DESC, name COLLATE NOCASE ASC LIMIT 15"
                    )
                ]
                # 来源
                by_source = {
                    (r["source"] or "unmatched"): r["c"]
                    for r in fetch(f"SELECT source, COUNT(*) c FROM models WHERE {W} GROUP BY source")
                }
                # 使用情况
                used = scalar(f"SELECT COUNT(*) FROM models WHERE usage_count > 0 AND {W}")
                total_usage = scalar(f"SELECT COALESCE(SUM(usage_count),0) FROM models WHERE {W}")
                top_used = [
                    {"name": r["model_name"] or r["file_name"],
                     "type": r["model_type"] or "other", "usage_count": r["usage_count"],
                     "preview_url": r["preview_url"] or ""}
                    for r in fetch(
                        f"SELECT file_name, model_name, model_type, usage_count, preview_url "
                        f"FROM models WHERE usage_count > 0 AND {W} "
                        f"ORDER BY usage_count DESC, file_name COLLATE NOCASE ASC LIMIT 12"
                    )
                ]
                return {
                    "overview": {
                        "total": total, "matched": matched, "unmatched": max(0, total - matched),
                        "deleted": deleted, "favorites": favorites,
                        "total_bytes": total_bytes, "nsfw": nsfw, "sfw": max(0, total - nsfw),
                    },
                    "by_type": by_type,
                    "by_base_model": by_base_model,
                    "by_source": by_source,
                    "usage": {
                        "used": used, "unused": max(0, total - used),
                        "total_usage": total_usage, "top_used": top_used,
                    },
                }
            finally:
                conn.close()

    def get_civitai_refresh_targets(self) -> list:
        """所有可以通过 CivitAI API 刷新 base_model 的条目
        返回 [{file_path, civitai_version_id, base_model}, ...]
        """
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("""
                    SELECT file_path, civitai_version_id, base_model
                    FROM models
                    WHERE file_deleted = 0
                      AND civitai_version_id IS NOT NULL
                      AND civitai_version_id != 0
                """).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def update_base_model_only(self, file_path: str, base_model: str):
        """只更新 base_model 列，不动其他字段。用户已锁定 base_model 时跳过（刷新不覆盖手填值）。"""
        if not base_model:
            return
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT custom_fields FROM models WHERE file_path = ?", (file_path,)).fetchone()
                try:
                    locked = set(json.loads(row["custom_fields"] or "[]")) if row else set()
                except (TypeError, ValueError):
                    locked = set()
                if "base_model" in locked:
                    return  # 用户锁定了 base_model，刷新不覆盖
                conn.execute(
                    "UPDATE models SET base_model = ? WHERE file_path = ?",
                    (base_model, file_path),
                )
                conn.commit()
            finally:
                conn.close()

    def get_stats(self, source: str = "") -> dict:
        """获取统计信息。source=="deleted" 时 type_counts 按软删模型(file_deleted=1)
        统计，供"已删除/留记录"视图的类型 tab 显示正确计数。"""
        type_fd = 1 if source == "deleted" else 0
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                total = conn.execute("SELECT COUNT(*) as cnt FROM models WHERE file_deleted = 0").fetchone()["cnt"]
                matched = conn.execute("SELECT COUNT(*) as cnt FROM models WHERE matched = 1 AND file_deleted = 0").fetchone()["cnt"]
                deleted = conn.execute("SELECT COUNT(*) as cnt FROM models WHERE file_deleted = 1").fetchone()["cnt"]
                type_rows = conn.execute(
                    "SELECT model_type, COUNT(*) as cnt FROM models WHERE file_deleted = ? GROUP BY model_type",
                    (type_fd,),
                ).fetchall()
                type_counts = {r["model_type"] or "other": r["cnt"] for r in type_rows}
                updatable = conn.execute("SELECT COUNT(*) as cnt FROM models WHERE update_available = 1 AND file_deleted = 0").fetchone()["cnt"]
                return {
                    "total": total,
                    "matched": matched,
                    "unmatched": total - matched,
                    "deleted": deleted,
                    "updatable": updatable,
                    "type_counts": type_counts,
                }
            finally:
                conn.close()

    def update_favorite(self, file_path: str, favorite: bool):
        """切换收藏状态"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE models SET favorite = ? WHERE file_path = ?",
                    (1 if favorite else 0, file_path)
                )
                conn.commit()
            finally:
                conn.close()

    def set_corrupt_flags(self, flags: dict):
        """批量写入损坏标志。flags = {file_path: 0/1}。扫描后调用（只更新被解析过的文件）。"""
        items = [(int(bool(v)), fp) for fp, v in (flags or {}).items()]
        if not items:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany("UPDATE models SET file_corrupt = ? WHERE file_path = ?", items)
                conn.commit()
            finally:
                conn.close()

    def update_notes(self, file_path: str, notes: str):
        """更新用户笔记"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE models SET notes = ? WHERE file_path = ?",
                    (notes, file_path)
                )
                conn.commit()
            finally:
                conn.close()

    # 允许用户在"自定义"Tab 里手改的字段白名单
    _CUSTOM_FIELDS = (
        "model_name", "base_model", "creator", "version_name",
        "trained_words", "model_description", "preview_url", "source",
        "user_model_type",
    )

    def update_custom_info(self, file_path: str, fields: dict, lock=None, unlock=None):
        """更新用户手填的自定义字段（只允许白名单里的列）。

        trained_words 若是 list 会自动序列化成 JSON；source 传空会被忽略（保留原值）。
        lock/unlock：要锁定 / 解锁的字段名集合 —— 锁定的字段后续匹配/刷新不再覆盖
        （记进 custom_fields 列；解锁则移除）。
        """
        data = {}
        for key in self._CUSTOM_FIELDS:
            if key not in fields:
                continue
            value = fields[key]
            if key == "trained_words" and isinstance(value, list):
                value = json.dumps(value, ensure_ascii=False)
            if key == "source" and not value:
                continue
            data[key] = value

        lock = set(lock or [])
        unlock = set(unlock or [])
        with self._lock:
            conn = self._connect()
            try:
                if lock or unlock:
                    row = conn.execute("SELECT custom_fields FROM models WHERE file_path = ?", (file_path,)).fetchone()
                    try:
                        cur = set(json.loads(row["custom_fields"] or "[]")) if row else set()
                    except (TypeError, ValueError):
                        cur = set()
                    cur = (cur | lock) - unlock
                    data["custom_fields"] = json.dumps(sorted(cur), ensure_ascii=False)  # 列名硬编码，非用户输入
                if not data:
                    return
                assignments = ", ".join(f"{k} = ?" for k in data.keys())
                params = list(data.values()) + [file_path]
                conn.execute(
                    f"UPDATE models SET {assignments} WHERE file_path = ?",
                    params
                )
                conn.commit()
            finally:
                conn.close()

    def delete_model(self, file_path: str) -> bool:
        """从数据库中删除模型记录"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM model_tags WHERE file_path = ?", (file_path,))
                conn.execute("DELETE FROM hash_index WHERE file_path = ?", (file_path,))
                cursor = conn.execute("DELETE FROM models WHERE file_path = ?", (file_path,))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def soft_delete_model(self, file_path: str, archived_path: str = "") -> bool:
        """标记模型文件已删除（保留记录）。archived_path=文件移到存档夹的实际位置，供精确恢复。"""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "UPDATE models SET file_deleted = 1, deleted_at = ?, archived_path = ? WHERE file_path = ?",
                    (time.time(), archived_path or "", file_path)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def restore_model(self, file_path: str) -> bool:
        """恢复软删除的模型记录（清掉存档位置标记）"""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "UPDATE models SET file_deleted = 0, deleted_at = 0, archived_path = '' WHERE file_path = ?",
                    (file_path,)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def get_archived_path(self, file_path: str) -> str:
        """读取软删除记录存档时记下的实际文件位置（空=旧记录或未移动文件）。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT archived_path FROM models WHERE file_path = ?", (file_path,)
                ).fetchone()
                return (row["archived_path"] or "") if row else ""
            finally:
                conn.close()

    def increment_usage(self, file_names: list):
        """批量增加模型使用次数（精确匹配 file_name，或 file_path 以分隔符+name 结尾）"""
        if not file_names:
            return
        now = time.time()
        updated = 0
        with self._lock:
            conn = self._connect()
            try:
                for name in file_names:
                    if not name:
                        continue
                    # 用 ESCAPE 转义防止 name 中的 % 和 _ 被 LIKE 当通配符
                    escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    cursor = conn.execute("""
                        UPDATE models SET usage_count = usage_count + 1, last_used_at = ?
                        WHERE file_name = ?
                           OR file_path LIKE ? ESCAPE '\\'
                           OR file_path LIKE ? ESCAPE '\\'
                    """, (now, name, f"%/{escaped}", f"%\\{escaped}"))
                    updated += cursor.rowcount
                conn.commit()
                if updated > 0:
                    logger.debug("[Noctyra-MM] 使用统计: %d 个文件名匹配到 %d 条记录", len(file_names), updated)
            finally:
                conn.close()

    def update_file_path(self, old_path: str, new_path: str, new_folder: str):
        """移动文件后更新数据库中的路径和文件夹"""
        new_name = os.path.basename(new_path)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE models SET file_path = ?, file_name = ?, folder = ? WHERE file_path = ?",
                    (new_path, new_name, new_folder, old_path)
                )
                conn.execute(
                    "UPDATE model_tags SET file_path = ? WHERE file_path = ?",
                    (new_path, old_path)
                )
                conn.execute(
                    "UPDATE hash_index SET file_path = ? WHERE file_path = ?",
                    (new_path, old_path)
                )
                conn.commit()
            finally:
                conn.close()

    def has_file(self, file_path: str) -> bool:
        """检查文件路径是否已存在"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                row = conn.execute(
                    "SELECT 1 FROM models WHERE file_path = ?", (file_path,)
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def set_update_available(self, file_paths: List[str], checked_paths: List[str] = None):
        """标记哪些模型有可用更新。
        checked_paths=None → 全量重置再设置（旧行为）；
        给定 checked_paths → 只重置"本次检查过的"，再设置有更新的
        （配合 24h TTL：跳过未检查的模型，保留它们已有的更新标记）。"""
        def _batched_update(conn, sql_tpl, paths):
            for i in range(0, len(paths), 500):
                batch = paths[i:i + 500]
                ph = ",".join("?" * len(batch))
                conn.execute(sql_tpl.format(ph=ph), batch)

        with self._lock:
            conn = self._connect()
            try:
                if checked_paths is None:
                    conn.execute("UPDATE models SET update_available = 0")
                elif checked_paths:
                    _batched_update(conn, "UPDATE models SET update_available = 0 WHERE file_path IN ({ph})", checked_paths)
                if file_paths:
                    _batched_update(conn, "UPDATE models SET update_available = 1 WHERE file_path IN ({ph})", file_paths)
                conn.commit()
            finally:
                conn.close()

    def mark_update_checked(self, file_paths: List[str], ts: float):
        """记录这些模型的最近更新检查时间（供 24h TTL 跳过最近查过的）。"""
        if not file_paths:
            return
        with self._lock:
            conn = self._connect()
            try:
                for i in range(0, len(file_paths), 500):
                    batch = file_paths[i:i + 500]
                    ph = ",".join("?" * len(batch))
                    conn.execute(
                        f"UPDATE models SET last_update_check_at = ? WHERE file_path IN ({ph})",
                        [ts] + batch,
                    )
                conn.commit()
            finally:
                conn.close()

    def get_civitai_models(self) -> List[dict]:
        """获取所有已匹配 CivitAI 的模型（用于更新检查）"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("""
                    SELECT file_path, file_name, model_name, version_name,
                           civitai_model_id, civitai_version_id, last_update_check_at
                    FROM models
                    WHERE source = 'civitai' AND civitai_model_id IS NOT NULL
                          AND file_deleted = 0
                """).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_hf_models(self) -> List[dict]:
        """获取所有已匹配 HuggingFace 的模型（用于更新检查）"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("""
                    SELECT file_path, file_name, model_name, hf_repo_id,
                           hf_url, hf_last_modified
                    FROM models
                    WHERE hf_repo_id IS NOT NULL AND hf_repo_id != ''
                          AND file_deleted = 0
                """).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_existing_hashes(self) -> Dict[str, str]:
        """获取已有的 file_path -> sha256 映射，用于增量扫描"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT file_path, sha256 FROM models WHERE sha256 IS NOT NULL AND sha256 != ''"
                ).fetchall()
                return {r["file_path"]: r["sha256"] for r in rows}
            finally:
                conn.close()

    def get_existing_file_stats(self) -> Dict[str, tuple]:
        """获取已有的 file_path -> (modified, file_size) 映射，用于增量扫描跳过未变文件。
        同时比 mtime 与 size：只比 mtime 会被"原地替换但保留/未变 mtime"骗过，留旧 hash。
        只返回活动记录（file_deleted=0），避免旧记录干扰判定。"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT file_path, modified, file_size FROM models WHERE file_deleted = 0"
                ).fetchall()
                return {
                    r["file_path"]: ((r["modified"] or 0.0), (r["file_size"] or 0))
                    for r in rows
                }
            finally:
                conn.close()

    # ========== 内部方法 ==========

    # CivitAI type -> 内部 model_type 映射
    _CIVITAI_TYPE_MAP = {
        "Checkpoint": "checkpoint",
        "LORA": "lora",
        "LoCon": "lora",
        "DoRA": "lora",
        "TextualInversion": "embedding",
        "Hypernetwork": "hypernetwork",
        "AestheticGradient": "other",
        "Controlnet": "controlnet",
        "Upscaler": "upscale",
        "VAE": "vae",
        "TextEncoder": "text_encoder",
        "CLIPVision": "clip_vision",
        "MotionModule": "motion",
        "Detection": "detection",
        "Poses": "other",
        "Wildcards": "other",
    }

    @classmethod
    def _map_civitai_type(cls, civitai_type: str) -> str:
        """将 CivitAI 模型类型映射为内部分类"""
        return cls._CIVITAI_TYPE_MAP.get(civitai_type, "")

    # CivitAI LoRA 家族 type → 内部细分（仅筛选用，model_type 仍统一为 lora）
    _LORA_SUBTYPE_MAP = {"LORA": "lora", "LoCon": "lycoris", "DoRA": "dora"}

    @classmethod
    def _map_lora_subtype(cls, civitai_type: str) -> str:
        """CivitAI 的 LoRA 家族 type 映射到细分（LoCon=LyCORIS）。非 LoRA 家族返回 ''。"""
        return cls._LORA_SUBTYPE_MAP.get(civitai_type, "")

    @staticmethod
    def _row_to_dict(row: sqlite3.Row, include_images: bool = False) -> dict:
        """将 sqlite3.Row 转换为 dict，解析 JSON 字段"""
        d = dict(row)
        # 解析 trained_words
        tw = d.get("trained_words", "[]")
        try:
            d["trained_words"] = json.loads(tw) if tw else []
        except (json.JSONDecodeError, TypeError):
            d["trained_words"] = []
        # 解析 preview_images；只保留 dict 元素，旧/导入数据若是字符串列表
        # （如 ["url1","url2"]）后续的 p.get(...) 会抛 AttributeError 冒泡到查询层
        pi = d.get("preview_images", "[]")
        try:
            parsed_pi = json.loads(pi) if pi else []
            d["preview_images"] = [p for p in parsed_pi if isinstance(p, dict)] \
                if isinstance(parsed_pi, list) else []
        except (json.JSONDecodeError, TypeError):
            d["preview_images"] = []
        # 导出主预览类型 + NSFW 分级（卡片用）
        if d["preview_images"]:
            first = next((p for p in d["preview_images"] if p.get("type") != "video"), None)
            primary = first or d["preview_images"][0]
            d["preview_type"] = primary.get("type", "image")
            d["preview_nsfw_level"] = int(primary.get("nsfw_level", 0) or 0)
            # 整条模型的最大 NSFW 等级（任意预览图）
            try:
                d["max_nsfw_level"] = max(
                    (int(p.get("nsfw_level", 0) or 0) for p in d["preview_images"]),
                    default=0,
                )
            except (TypeError, ValueError):
                d["max_nsfw_level"] = 0
            # 多图徽章计数（卡片用）：替代把整个 preview_image_urls 数组发给前端
            _with_url = [p for p in d["preview_images"] if p.get("url")]
            d["preview_media_count"] = len(_with_url)
            d["preview_video_count"] = sum(1 for p in _with_url if p.get("type") == "video")
        else:
            d["preview_type"] = "image"
            d["preview_nsfw_level"] = 0
            d["max_nsfw_level"] = 0
            d["preview_media_count"] = 0
            d["preview_video_count"] = 0
        # 列表页不需要完整图片数据，只保留 URL + type + nsfw_level
        if not include_images and d["preview_images"]:
            d["preview_image_urls"] = [
                {
                    "url": img["url"],
                    "type": img.get("type", "image"),
                    "nsfw_level": int(img.get("nsfw_level", 0) or 0),
                }
                for img in d["preview_images"] if img.get("url")
            ]
            d.pop("preview_images")
        # 解析 hf_tags
        ht = d.get("hf_tags", "[]")
        try:
            d["hf_tags"] = json.loads(ht) if ht else []
        except (json.JSONDecodeError, TypeError):
            d["hf_tags"] = []
        # 解析 civitai_tags
        ct = d.get("civitai_tags", "[]")
        try:
            d["civitai_tags"] = json.loads(ct) if ct else []
        except (json.JSONDecodeError, TypeError):
            d["civitai_tags"] = []
        # 布尔字段
        d["matched"] = bool(d.get("matched", 0))
        d["favorite"] = bool(d.get("favorite", 0))
        d["nsfw"] = bool(d.get("nsfw", 0))
        # 从 hf_raw 里抽 gated 标记（受限仓库提示用）
        hr = d.get("hf_raw")
        if hr:
            try:
                raw = json.loads(hr)
                g = raw.get("gated")
                d["hf_gated"] = False if (g is None or g is False or g == "false") else str(g)
            except (json.JSONDecodeError, TypeError, AttributeError):
                d["hf_gated"] = False
        else:
            d["hf_gated"] = False
        # 不返回大字段（太大），需要时单独查
        d.pop("metadata_raw", None)
        d.pop("civitai_raw", None)
        d.pop("hf_raw", None)
        return d

    def rebuild(self):
        """清空所有数据（重建缓存用）"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM model_tags")
                conn.execute("DELETE FROM hash_index")
                conn.execute("DELETE FROM models")
                conn.commit()
                logger.info("[Noctyra-MM] 数据库已清空，准备重建")
            finally:
                conn.close()

    def get_duplicates(self) -> list:
        """查找 SHA256 相同的重复模型，按哈希分组返回"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("""
                    SELECT sha256, file_path, file_name, file_size, model_name,
                           base_model, folder, preview_url, source
                    FROM models
                    WHERE file_deleted = 0 AND sha256 IN (
                        SELECT sha256 FROM models
                        WHERE sha256 != '' AND sha256 IS NOT NULL AND file_deleted = 0
                        GROUP BY sha256 HAVING COUNT(*) > 1
                    )
                    ORDER BY sha256, file_name
                """).fetchall()
                groups = {}
                for r in rows:
                    d = dict(r)
                    groups.setdefault(d["sha256"], []).append(d)
                return list(groups.values())
            finally:
                conn.close()

    # ========== JSON 迁移 ==========

    def _try_migrate_json(self, cache_dir: str):
        """尝试从旧 JSON 缓存导入数据"""
        cache_file = os.path.join(cache_dir, "model_cache.json")
        if not os.path.exists(cache_file):
            return

        logger.info("[Noctyra-MM] 发现旧 JSON 缓存，开始迁移到 SQLite...")
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                old_data = json.load(f)

            count = 0
            batch = []
            for sha256, entry in old_data.items():
                model = {
                    "file_path": entry.get("file_path", ""),
                    "file_name": entry.get("file_name", ""),
                    "file_ext": entry.get("file_ext", ""),
                    "file_size": entry.get("file_size", 0),
                    "modified": entry.get("modified", 0),
                    "sha256": sha256,
                    "base_model": entry.get("base_model", "Unknown"),
                    "trained_words": entry.get("trained_words", []),
                    "source": entry.get("source", ""),
                    "source_url": entry.get("source_url", ""),
                    "model_name": entry.get("model_name", ""),
                    "model_description": entry.get("model_description", ""),
                    "preview_url": entry.get("preview_url", ""),
                    "matched": entry.get("matched", False),
                    "tags": entry.get("tags", []),
                    "creator": entry.get("creator", ""),
                    "nsfw": entry.get("nsfw", False),
                    "folder": entry.get("folder", ""),
                }
                # 从 online_info 中提取（旧格式兼容）
                online = entry.get("online_info", {})
                if online:
                    model["source"] = model["source"] or online.get("source", "")
                    model["source_url"] = model["source_url"] or online.get("source_url", "")
                    model["model_name"] = model["model_name"] or online.get("model_name", "")
                    model["preview_url"] = model["preview_url"] or online.get("preview_url", "")
                    model["creator"] = model["creator"] or online.get("creator", "")
                    model["tags"] = model["tags"] or online.get("tags", [])
                    model["matched"] = True

                if model["file_path"]:
                    batch.append(model)
                    count += 1

            if batch:
                self.upsert_models_batch(batch)

            # 重命名旧文件（若 .bak 已存在则覆盖，避免 Windows 下 rename 失败）
            os.replace(cache_file, cache_file + ".bak")
            index_file = os.path.join(cache_dir, "hash_index.json")
            if os.path.exists(index_file):
                os.replace(index_file, index_file + ".bak")

            logger.info("[Noctyra-MM] JSON 迁移完成，共导入 %d 条记录", count)

        except Exception as e:
            logger.error("[Noctyra-MM] JSON 迁移失败: %s", e)

    def get_all_preview_urls(self) -> set:
        """获取数据库中所有被引用的预览图 URL（含 preview_url 和 preview_images 内的 URL）"""
        urls = set()
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("SELECT preview_url, preview_images FROM models").fetchall()
                for row in rows:
                    pu = row["preview_url"]
                    if pu:
                        urls.add(pu)
                    pi = row["preview_images"]
                    if pi:
                        try:
                            images = json.loads(pi)
                            for img in images:
                                u = img.get("url", "") if isinstance(img, dict) else ""
                                if u:
                                    urls.add(u)
                        except (json.JSONDecodeError, TypeError):
                            pass
            finally:
                conn.close()
        return urls

    def get_preview_url_owners(self) -> dict:
        """构建 预览URL → {name, file_path} 的反查表（主预览 + preview_images 里的每张）。
        给任务中心把"失败的预览图"映射回所属模型,并支持点击跳转到该模型详情。"""
        mapping = {}
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT model_name, file_name, file_path, preview_url, preview_images "
                    "FROM models WHERE file_deleted = 0"
                ).fetchall()
                for row in rows:
                    name = (row["model_name"] or row["file_name"] or "").strip()
                    if not name:
                        continue
                    owner = {"name": name, "file_path": row["file_path"] or ""}
                    pu = row["preview_url"]
                    if pu:
                        mapping.setdefault(pu, owner)
                    if row["preview_images"]:
                        try:
                            imgs = json.loads(row["preview_images"]) or []
                        except (json.JSONDecodeError, TypeError):
                            imgs = []
                        for img in imgs:
                            if isinstance(img, dict):
                                u = img.get("url")
                                if u:
                                    mapping.setdefault(u, owner)
            finally:
                conn.close()
        return mapping

    def get_all_image_sources(self) -> list:
        """返回所有模型的图片相关字段，供预缓存时提取 URL 使用"""
        results = []
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute(
                    "SELECT preview_url, creator_avatar, preview_images, "
                    "model_description, hf_description, hf_repo_id FROM models"
                ).fetchall()
                for row in rows:
                    images = []
                    if row["preview_images"]:
                        try:
                            images = json.loads(row["preview_images"]) or []
                        except (json.JSONDecodeError, TypeError):
                            images = []
                    results.append({
                        "preview_url": row["preview_url"] or "",
                        "creator_avatar": row["creator_avatar"] or "",
                        "preview_images": images,
                        "model_description": row["model_description"] or "",
                        "hf_description": row["hf_description"] or "",
                        "hf_repo_id": row["hf_repo_id"] or "",
                    })
            finally:
                conn.close()
        return results

    # ========== 元数据归档 ==========

    def _archive_metadata(self, conn, sha256: str, info: dict, source: str = ""):
        """将匹配到的元数据归档（在已有事务内调用，不单独 commit）"""
        if not sha256:
            return
        src = source or info.get("source", "")
        data_json = json.dumps(info, ensure_ascii=False, default=str)
        conn.execute("""
            INSERT INTO metadata_archive (sha256, source, data, archived_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                data = ?,
                source = CASE WHEN ? != '' THEN ? ELSE source END,
                archived_at = ?
        """, (sha256, src, data_json, time.time(),
              data_json, src, src, time.time()))

    def get_archived(self, sha256: str) -> Optional[dict]:
        """从归档中获取元数据"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                row = conn.execute(
                    "SELECT data, source FROM metadata_archive WHERE sha256 = ?",
                    (sha256,)
                ).fetchone()
                if row:
                    try:
                        return json.loads(row["data"])
                    except (json.JSONDecodeError, TypeError):
                        return None
                return None
            finally:
                conn.close()

    def get_archive_stats(self) -> dict:
        """获取归档统计"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                total = conn.execute("SELECT COUNT(*) FROM metadata_archive").fetchone()[0]
                civitai = conn.execute(
                    "SELECT COUNT(*) FROM metadata_archive WHERE source = 'civitai'"
                ).fetchone()[0]
                hf = conn.execute(
                    "SELECT COUNT(*) FROM metadata_archive WHERE source = 'huggingface'"
                ).fetchone()[0]
                return {"archive_total": total, "archive_civitai": civitai, "archive_hf": hf}
            finally:
                conn.close()

    # ========== 导入导出 ==========

    def export_all(self) -> list:
        """导出所有模型数据（含 tags），用于备份/迁移"""
        with self._lock:
            conn = self._connect(readonly=True)
            try:
                rows = conn.execute("SELECT * FROM models").fetchall()
                models = []
                for row in rows:
                    d = dict(row)
                    d.pop("metadata_raw", None)
                    tag_rows = conn.execute(
                        "SELECT tag FROM model_tags WHERE file_path = ?",
                        (d["file_path"],)
                    ).fetchall()
                    d["tags"] = [t["tag"] for t in tag_rows]
                    models.append(d)
                return models
            finally:
                conn.close()

    def import_models(self, models: list, mode: str = "merge") -> dict:
        """导入模型数据

        Args:
            models: 模型字典列表
            mode: "merge"（按 SHA256 合并到已有记录）或 "overwrite"（完全替换匹配记录）

        Returns:
            {"updated": int, "skipped": int}
        """
        updated = 0
        skipped = 0

        importable_fields = {
            "source", "source_url", "model_name", "version_name",
            "model_description", "preview_url", "preview_images", "matched",
            "civitai_model_id", "civitai_version_id", "civitai_model_type",
            "creator", "creator_avatar", "nsfw", "published_at",
            "downloads", "rating", "rating_count", "thumbs_up",
            "base_model", "trained_words", "favorite", "notes",
            "hf_repo_id", "hf_url", "hf_downloads", "hf_likes",
            "hf_author", "hf_description", "hf_tags", "hf_last_modified",
            "comment_count", "update_available", "civitai_tags",
        }

        with self._lock:
            conn = self._connect()
            try:
                for m in models:
                    sha256 = m.get("sha256", "")
                    if not sha256:
                        skipped += 1
                        continue

                    existing = conn.execute(
                        "SELECT file_path FROM models WHERE sha256 = ?", (sha256,)
                    ).fetchone()
                    if not existing:
                        skipped += 1
                        continue

                    fp = existing["file_path"]

                    sets = []
                    params = []
                    for field in importable_fields:
                        if field not in m:
                            continue
                        val = m[field]
                        if isinstance(val, (list, dict)):
                            val = json.dumps(val, ensure_ascii=False)
                        if mode == "merge" and field in ("favorite", "notes"):
                            pass
                        elif mode == "merge":
                            current = conn.execute(
                                f"SELECT {field} FROM models WHERE file_path = ?", (fp,)
                            ).fetchone()
                            if current and current[0] and str(current[0]).strip():
                                continue
                        sets.append(f"{field} = ?")
                        params.append(val)

                    if sets:
                        params.append(fp)
                        conn.execute(
                            f"UPDATE models SET {', '.join(sets)} WHERE file_path = ?",
                            params
                        )

                    tags = m.get("tags", [])
                    if tags:
                        for tag in tags:
                            conn.execute(
                                "INSERT OR IGNORE INTO model_tags (file_path, tag) VALUES (?, ?)",
                                (fp, tag)
                            )

                    updated += 1

                conn.commit()
                logger.info("[Noctyra-MM] 导入完成: 更新 %d, 跳过 %d", updated, skipped)
                return {"updated": updated, "skipped": skipped}
            finally:
                conn.close()

