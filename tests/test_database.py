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
ModelDatabase 基础 CRUD + filter_presets + workflow_images 测试。
"""

import pytest


@pytest.fixture
def model_row():
    return {
        "file_path": "D:/models/loras/test.safetensors",
        "file_name": "test.safetensors",
        "file_ext": ".safetensors",
        "file_size": 123456,
        "modified": 1700000000.0,
        "sha256": "a" * 64,
        "base_model": "SDXL",
        "trained_words": ["word1", "word2"],
        "model_type": "lora",
        "folder": "loras",
    }


class TestUpsertAndQuery:
    def test_upsert_and_get_by_path(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        got = tmp_db.get_by_path(model_row["file_path"])
        assert got is not None
        assert got["file_name"] == "test.safetensors"
        assert got["base_model"] == "SDXL"
        assert got["trained_words"] == ["word1", "word2"]

    def test_get_by_hash(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        got = tmp_db.get_by_hash("a" * 64)
        assert got is not None
        assert got["file_path"] == model_row["file_path"]

    def test_get_by_missing_hash_returns_none(self, tmp_db):
        assert tmp_db.get_by_hash("b" * 64) is None

    def test_get_all_filter_by_source(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        # 默认 source=''，过滤 "civitai" 应返回空
        rows, total = tmp_db.get_all(filters={"source": "civitai"})
        assert total == 0
        # 过滤 "unmatched" 应返回它（因为 matched=0 默认）
        rows, total = tmp_db.get_all(filters={"source": "unmatched"})
        assert total == 1

    def test_get_all_filter_by_base_model(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        rows, total = tmp_db.get_all(filters={"base_model": "SDXL"})
        assert total == 1
        rows, total = tmp_db.get_all(filters={"base_model": "Flux.1 D"})
        assert total == 0

    def test_get_all_filter_by_lora_subtype(self, tmp_db, model_row):
        # 三种细分各存一条，按 lora_subtype 应能精确筛出
        tmp_db.upsert_model(dict(model_row, file_path="d/plain.safetensors",
                                 sha256="a" * 64, lora_subtype="lora"))
        tmp_db.upsert_model(dict(model_row, file_path="d/lyco.safetensors",
                                 sha256="b" * 64, lora_subtype="lycoris"))
        tmp_db.upsert_model(dict(model_row, file_path="d/dora.safetensors",
                                 sha256="c" * 64, lora_subtype="dora"))
        rows, total = tmp_db.get_all(filters={"lora_subtype": "lycoris"})
        assert total == 1
        assert rows[0]["file_path"] == "d/lyco.safetensors"
        rows, total = tmp_db.get_all(filters={"lora_subtype": "dora"})
        assert total == 1
        # 不加细分筛选 → 三条都在
        rows, total = tmp_db.get_all(filters={"model_type": "lora"})
        assert total == 3

    def test_map_lora_subtype(self):
        from manager.database import ModelDatabase as DB
        assert DB._map_lora_subtype("LORA") == "lora"
        assert DB._map_lora_subtype("LoCon") == "lycoris"
        assert DB._map_lora_subtype("DoRA") == "dora"
        assert DB._map_lora_subtype("Checkpoint") == ""
        assert DB._map_lora_subtype("") == ""

    def test_backfill_lora_subtype_from_civitai_type(self, tmp_db, model_row):
        # 旧库回填场景：matched 的 LoCon 模型但 lora_subtype 为空 → 回填成 lycoris（免重扫）
        tmp_db.upsert_model(dict(model_row, file_path="d/x.safetensors",
                                 sha256="e" * 64, lora_subtype=""))
        conn = tmp_db._connect()
        try:
            conn.execute("UPDATE models SET civitai_model_type='LoCon', matched=1 "
                         "WHERE file_path='d/x.safetensors'")
            conn.commit()
            tmp_db._backfill_lora_subtype(conn)
            conn.commit()
            row = conn.execute(
                "SELECT lora_subtype FROM models WHERE file_path='d/x.safetensors'"
            ).fetchone()
        finally:
            conn.close()
        assert row["lora_subtype"] == "lycoris"

    def test_upsert_preserves_sha256_on_empty_input(self, tmp_db, model_row):
        """已有 sha256 的记录再 upsert 但传空 sha256，不应覆盖"""
        tmp_db.upsert_model(model_row)
        updated = dict(model_row, sha256="")  # 空字符串
        tmp_db.upsert_model(updated)
        got = tmp_db.get_by_path(model_row["file_path"])
        assert got["sha256"] == "a" * 64  # 保留原值


class TestGetModelsByNames:
    """画布模型选择器用的批量名字→元数据匹配。"""

    def test_basic_match(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        result = tmp_db.get_models_by_names(["test.safetensors"])
        assert "test.safetensors" in result
        assert result["test.safetensors"]["file_name"] == "test.safetensors"
        assert result["test.safetensors"]["base_model"] == "SDXL"

    def test_relative_path_match_by_basename(self, tmp_db, model_row):
        """ComfyUI 常传含子目录的相对路径，应按 basename 命中。"""
        tmp_db.upsert_model(model_row)
        result = tmp_db.get_models_by_names(["loras/test.safetensors", "sub\\test.safetensors"])
        assert "loras/test.safetensors" in result
        assert "sub\\test.safetensors" in result

    def test_over_999_names_no_crash(self, tmp_db, model_row):
        """>999 个名字不能触发 SQLite 参数上限 OperationalError（分批查询）。"""
        tmp_db.upsert_model(model_row)
        names = [f"fake_{i}.safetensors" for i in range(1500)]
        names.append("test.safetensors")
        result = tmp_db.get_models_by_names(names)  # 不应抛异常
        assert "test.safetensors" in result
        assert not [k for k in result if k.startswith("fake_")]

    def test_empty_input(self, tmp_db):
        assert tmp_db.get_models_by_names([]) == {}


class TestRowToDictRobustness:
    def test_get_by_path_handles_string_preview_images(self, tmp_db, model_row):
        """旧/导入数据里 preview_images 可能是字符串列表，不能让查询抛 AttributeError。"""
        tmp_db.upsert_model(model_row)
        conn = tmp_db._connect()
        try:
            conn.execute(
                "UPDATE models SET preview_images=? WHERE file_path=?",
                ('["http://x/a.png", "http://x/b.png"]', model_row["file_path"]),
            )
            conn.commit()
        finally:
            conn.close()
        got = tmp_db.get_by_path(model_row["file_path"])  # 不应抛异常
        assert got is not None
        assert got["max_nsfw_level"] == 0


class TestSoftDeleteViews:
    """软删（删文件留记录）相关：已删除视图的类型计数 + 扩展 check 的 include_deleted。"""

    def test_get_stats_deleted_view_type_counts(self, tmp_db, model_row):
        tmp_db.upsert_model(dict(model_row, model_type="unet"))
        s = tmp_db.get_stats()
        assert s["type_counts"].get("unet") == 1
        assert s["deleted"] == 0
        # 软删后：正常视图不再计入该类型，deleted 计数 +1
        tmp_db.soft_delete_model(model_row["file_path"])
        s = tmp_db.get_stats()
        assert s["deleted"] == 1
        assert s["type_counts"].get("unet", 0) == 0
        # 已删除视图：type_counts 改按软删模型统计
        sd = tmp_db.get_stats(source="deleted")
        assert sd["type_counts"].get("unet") == 1

    def test_query_by_version_id_include_deleted(self, tmp_db, model_row):
        tmp_db.upsert_model(dict(model_row, civitai_version_id=12345))
        assert len(tmp_db.query_by_version_id(12345)) == 1
        tmp_db.soft_delete_model(model_row["file_path"])
        # 默认排除软删（扩展旧行为：软删=不存在）
        assert tmp_db.query_by_version_id(12345) == []
        # include_deleted=True 返回，并带 file_deleted 标记
        rows = tmp_db.query_by_version_id(12345, include_deleted=True)
        assert len(rows) == 1 and rows[0]["file_deleted"] == 1


class TestUpdateCheckTTL:
    """更新检查 24h TTL：set_update_available 增量清除 + mark_update_checked。"""

    def test_set_update_available_full_reset(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        tmp_db.set_update_available([model_row["file_path"]])
        assert tmp_db.get_by_path(model_row["file_path"])["update_available"] == 1
        tmp_db.set_update_available([])  # checked_paths=None → 全量清空
        assert tmp_db.get_by_path(model_row["file_path"])["update_available"] == 0

    def test_set_update_available_incremental_preserves_skipped(self, tmp_db, model_row):
        a = dict(model_row, file_path="D:/m/a.safetensors", file_name="a.safetensors")
        b = dict(model_row, file_path="D:/m/b.safetensors", file_name="b.safetensors")
        tmp_db.upsert_model(a)
        tmp_db.upsert_model(b)
        # 上次检查：B 有更新
        tmp_db.set_update_available([b["file_path"]])
        # 本次只检查 A（A 有更新），B 被 24h TTL 跳过（不在 checked_paths）
        tmp_db.set_update_available([a["file_path"]], checked_paths=[a["file_path"]])
        assert tmp_db.get_by_path(a["file_path"])["update_available"] == 1
        assert tmp_db.get_by_path(b["file_path"])["update_available"] == 1  # 跳过的保留旧标记

    def test_mark_update_checked(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        tmp_db.mark_update_checked([model_row["file_path"]], 1781000000.0)
        assert tmp_db.get_by_path(model_row["file_path"])["last_update_check_at"] == 1781000000.0


class TestFts5Search:
    def test_search_matches_trained_words(self, tmp_db, model_row):
        """FTS5 应该能按 trained_words 匹配（LIKE 版本只看 file_name/model_name）"""
        tmp_db.upsert_model(model_row)
        # trained_words = ["word1", "word2"]；按文件名找不到的词，FTS 能找到
        rows, total = tmp_db.get_all(filters={"search": "word1"})
        assert total == 1, "期望 FTS 按 trained_words 命中"

    def test_search_prefix_match(self, tmp_db, model_row):
        """前缀匹配：搜 'wor' 应该命中 'word1' / 'word2'"""
        tmp_db.upsert_model(model_row)
        rows, total = tmp_db.get_all(filters={"search": "wor"})
        assert total == 1

    def test_search_case_insensitive(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        rows, total = tmp_db.get_all(filters={"search": "WORD1"})
        assert total == 1

    def test_search_no_match(self, tmp_db, model_row):
        tmp_db.upsert_model(model_row)
        rows, total = tmp_db.get_all(filters={"search": "nonexistent_xyz"})
        assert total == 0

    def test_search_on_model_name(self, tmp_db, model_row):
        """model_name 也应该被 FTS 索引"""
        row = dict(model_row)
        row["model_name"] = "Acid Cyberpunk Style"
        tmp_db.upsert_model(row)
        rows, total = tmp_db.get_all(filters={"search": "cyberpunk"})
        assert total == 1


class TestTriggerWordsAggregate:
    def test_aggregate_counts_and_sort(self, tmp_db):
        tmp_db.upsert_model({
            "file_path": "a.safetensors", "file_name": "a.safetensors",
            "file_ext": ".safetensors", "file_size": 1, "modified": 0,
            "sha256": "a" * 64, "trained_words": ["common", "unique_a"],
            "model_type": "lora", "folder": "loras",
        })
        tmp_db.upsert_model({
            "file_path": "b.safetensors", "file_name": "b.safetensors",
            "file_ext": ".safetensors", "file_size": 1, "modified": 0,
            "sha256": "b" * 64, "trained_words": ["common", "unique_b"],
            "model_type": "lora", "folder": "loras",
        })
        words = tmp_db.aggregate_trained_words()
        by_word = {w["word"]: w for w in words}
        assert by_word["common"]["count"] == 2
        assert by_word["unique_a"]["count"] == 1
        # 按 count 降序
        assert words[0]["word"] == "common"
        # model_types 追踪
        assert "lora" in by_word["common"]["model_types"]


class TestFilterPresets:
    def test_save_and_list(self, tmp_db):
        p = tmp_db.save_filter_preset("我的预设", {"base_model": "Flux.1 D"})
        assert p["name"] == "我的预设"
        assert p["filters"] == {"base_model": "Flux.1 D"}

        presets = tmp_db.list_filter_presets()
        assert len(presets) == 1
        assert presets[0]["filters"]["base_model"] == "Flux.1 D"

    def test_upsert_by_name(self, tmp_db):
        p1 = tmp_db.save_filter_preset("foo", {"a": "1"})
        p2 = tmp_db.save_filter_preset("foo", {"a": "2"})
        assert p1["id"] == p2["id"]  # 同 name 更新而不是新建
        assert p2["filters"] == {"a": "2"}

    def test_delete_by_name(self, tmp_db):
        tmp_db.save_filter_preset("foo", {})
        assert tmp_db.delete_filter_preset("foo") is True
        assert tmp_db.delete_filter_preset("foo") is False  # 不存在返回 False

    def test_empty_name_raises(self, tmp_db):
        with pytest.raises(ValueError):
            tmp_db.save_filter_preset("", {})


class TestIgnoredVersions:
    def test_add_list_and_check(self, tmp_db):
        tmp_db.add_ignored_version(100, 500)
        tmp_db.add_ignored_version(100, 501)
        tmp_db.add_ignored_version(200, 600)

        assert set(tmp_db.list_ignored_versions(100)) == {500, 501}
        assert tmp_db.list_ignored_versions(200) == [600]
        assert tmp_db.list_ignored_versions(999) == []

        assert tmp_db.is_version_ignored(100, 500) is True
        assert tmp_db.is_version_ignored(100, 999) is False

    def test_remove(self, tmp_db):
        tmp_db.add_ignored_version(100, 500)
        tmp_db.remove_ignored_version(100, 500)
        assert tmp_db.is_version_ignored(100, 500) is False

    def test_add_idempotent(self, tmp_db):
        tmp_db.add_ignored_version(100, 500)
        tmp_db.add_ignored_version(100, 500)  # 重复不报错
        assert tmp_db.list_ignored_versions(100) == [500]


class TestWorkflowImages:
    def _wf_row(self, **overrides):
        base = {
            "file_path": "D:/cache/workflows/test.png",
            "file_name": "test.png",
            "source": "civitai",
            "source_url": "https://civitai.com/images/12345",
            "civitai_image_id": 12345,
            "width": 512,
            "height": 768,
            "nsfw_level": 0,
            "meta": {"prompt": "test"},
            "resources": [
                {"type": "lora", "modelVersionId": 111, "weight": 0.8}
            ],
            "has_workflow": False,
            "workflow_json": None,
            "api_prompt_json": None,
            "parameters_text": "",
            "parsed_params": {},
            "embed_source": "none",
        }
        base.update(overrides)
        return base

    def test_save_and_get(self, tmp_db):
        img_id = tmp_db.save_workflow_image(self._wf_row())
        assert img_id > 0
        got = tmp_db.get_workflow_image(img_id)
        assert got is not None
        assert got["civitai_image_id"] == 12345
        # fingerprint 应自动计算（64 字符 SHA256）
        assert len(got["fingerprint"]) == 64
        assert got["recipe_version"] == 1

    def test_save_deduplicates_by_file_path(self, tmp_db):
        id1 = tmp_db.save_workflow_image(self._wf_row())
        id2 = tmp_db.save_workflow_image(self._wf_row())
        assert id1 == id2  # 同 file_path 更新不新建

    def test_same_resources_same_fingerprint(self, tmp_db):
        r1 = self._wf_row(file_path="D:/a.png", civitai_image_id=1)
        r2 = self._wf_row(file_path="D:/b.png", civitai_image_id=2)
        id1 = tmp_db.save_workflow_image(r1)
        id2 = tmp_db.save_workflow_image(r2)
        got1 = tmp_db.get_workflow_image(id1)
        got2 = tmp_db.get_workflow_image(id2)
        assert got1["fingerprint"] == got2["fingerprint"]

    def test_list_by_fingerprint_excludes_self(self, tmp_db):
        r1 = self._wf_row(file_path="D:/a.png", civitai_image_id=1)
        r2 = self._wf_row(file_path="D:/b.png", civitai_image_id=2)
        id1 = tmp_db.save_workflow_image(r1)
        tmp_db.save_workflow_image(r2)

        got1 = tmp_db.get_workflow_image(id1)
        fp = got1["fingerprint"]

        # 用 id1 作为 exclude_id，列表里应不含 id1
        others = tmp_db.list_workflow_images_by_fingerprint(fp, exclude_id=id1)
        assert len(others) == 1
        assert others[0]["id"] != id1

    def test_get_by_civitai_id(self, tmp_db):
        img_id = tmp_db.save_workflow_image(self._wf_row(civitai_image_id=99))
        got = tmp_db.get_workflow_image_by_civitai_id(99)
        assert got is not None and got["id"] == img_id
        assert tmp_db.get_workflow_image_by_civitai_id(42) is None

    def test_delete(self, tmp_db):
        img_id = tmp_db.save_workflow_image(self._wf_row())
        assert tmp_db.delete_workflow_image(img_id) is True
        assert tmp_db.get_workflow_image(img_id) is None
        assert tmp_db.delete_workflow_image(img_id) is False  # 不存在返回 False


class TestRecipeReverseLookup:
    """模型 → 配方反查：get_workflow_images_for_model（json_each + modelVersionId/modelId）。"""

    def _wf(self, **ov):
        base = {
            "file_path": "D:/cache/workflows/r.png",
            "file_name": "r.png",
            "source": "civitai",
            "civitai_image_id": 1,
            "resources": [{"type": "lora", "modelVersionId": 111, "modelId": 22, "weight": 1}],
        }
        base.update(ov)
        return base

    def test_match_by_version_id(self, tmp_db):
        id1 = tmp_db.save_workflow_image(self._wf(file_path="D:/a.png", civitai_image_id=1))
        tmp_db.save_workflow_image(self._wf(
            file_path="D:/b.png", civitai_image_id=2,
            resources=[{"type": "lora", "modelVersionId": 999, "modelId": 88}],
        ))
        hits = tmp_db.get_workflow_images_for_model(version_id=111)
        assert [h["id"] for h in hits] == [id1]

    def test_match_by_model_id(self, tmp_db):
        id1 = tmp_db.save_workflow_image(self._wf(file_path="D:/a.png", civitai_image_id=1))
        hits = tmp_db.get_workflow_images_for_model(model_id=22)
        assert id1 in [h["id"] for h in hits]

    def test_version_or_model_union(self, tmp_db):
        # 一张图按 version 命中，另一张按 model 命中 → 两者都返回（OR 语义）
        a = tmp_db.save_workflow_image(self._wf(
            file_path="D:/a.png", civitai_image_id=1,
            resources=[{"type": "lora", "modelVersionId": 111}]))
        b = tmp_db.save_workflow_image(self._wf(
            file_path="D:/b.png", civitai_image_id=2,
            resources=[{"type": "lora", "modelId": 22}]))
        ids = {h["id"] for h in tmp_db.get_workflow_images_for_model(version_id=111, model_id=22)}
        assert ids == {a, b}

    def test_distinct_when_multiple_resources_match(self, tmp_db):
        # 同一行里两条资源都命中 → DISTINCT 只出现一次
        rid = tmp_db.save_workflow_image(self._wf(
            file_path="D:/a.png", civitai_image_id=1,
            resources=[
                {"type": "lora", "modelVersionId": 111},
                {"type": "checkpoint", "modelVersionId": 111},
            ]))
        hits = tmp_db.get_workflow_images_for_model(version_id=111)
        assert [h["id"] for h in hits] == [rid]

    def test_no_ids_returns_empty(self, tmp_db):
        tmp_db.save_workflow_image(self._wf())
        assert tmp_db.get_workflow_images_for_model() == []
        assert tmp_db.get_workflow_images_for_model(version_id=None, model_id=None) == []

    def test_no_match_returns_empty(self, tmp_db):
        tmp_db.save_workflow_image(self._wf())
        assert tmp_db.get_workflow_images_for_model(version_id=7654321) == []


class TestStatistics:
    """统计页聚合 get_statistics。"""

    def _m(self, **ov):
        base = {
            "file_path": "D:/m/a.safetensors", "file_name": "a.safetensors",
            "file_ext": ".safetensors", "file_size": 1000, "modified": 1700000000.0,
            "sha256": "a" * 64, "base_model": "SDXL", "model_type": "lora", "folder": "loras",
        }
        base.update(ov)
        return base

    def test_empty(self, tmp_db):
        st = tmp_db.get_statistics()
        assert st["overview"]["total"] == 0
        assert st["by_type"] == []
        assert st["usage"]["used"] == 0
        assert {"overview", "by_type", "by_base_model", "by_source", "usage"} <= set(st)

    def test_counts_and_storage(self, tmp_db):
        tmp_db.upsert_model(self._m(
            file_path="D:/m/a.safetensors", sha256="a" * 64,
            model_type="lora", base_model="SDXL", file_size=1000))
        tmp_db.upsert_model(self._m(
            file_path="D:/m/b.safetensors", sha256="b" * 64,
            model_type="checkpoint", base_model="SD 1.5", file_size=2000))
        st = tmp_db.get_statistics()
        assert st["overview"]["total"] == 2
        assert st["overview"]["total_bytes"] == 3000
        types = {t["type"]: t for t in st["by_type"]}
        assert types["lora"]["count"] == 1
        assert types["checkpoint"]["bytes"] == 2000
        assert isinstance(st["usage"]["top_used"], list)
        assert any(b["name"] == "SDXL" for b in st["by_base_model"])
