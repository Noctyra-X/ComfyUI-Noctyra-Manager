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

"""图库 Billfish 文件夹模型 + 扫描器测试。"""

import os

import pytest


def _make_png(path, size=(8, 8)):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, (123, 50, 200)).save(path)


class _FakeConfig:
    """只暴露 gallery_folders（扫描器/树构建器唯一依赖）的桩。"""
    def __init__(self, folders):
        self._folders = folders

    @property
    def gallery_folders(self):
        return self._folders


@pytest.fixture
def gallery_tree(tmp_path):
    """建一个带子目录（含下划线目录，测 LIKE 转义）的图片树。"""
    root = tmp_path / "lib"
    _make_png(str(root / "a.png"), (10, 20))
    _make_png(str(root / "sub" / "b.png"), (30, 10))
    _make_png(str(root / "sub_x" / "c.png"), (12, 12))  # 下划线目录：不能被 sub 误匹配
    _make_png(str(root / "note.txt".replace(".txt", ".png")), (4, 4))  # 占位，确保多文件
    return str(root)


def test_scan_indexes_in_place(tmp_db, gallery_tree):
    from manager.gallery_scanner import scan_gallery
    cfg = _FakeConfig([{"path": gallery_tree, "name": "lib", "enabled": True}])

    res = scan_gallery(tmp_db, cfg)
    assert res["added"] == 4
    assert res["roots"] == 1

    # 全部在该根下
    listed = tmp_db.list_workflow_images(page=1, page_size=100, folder=gallery_tree)
    assert listed["total"] == 4
    # file_path 是真实路径（原地，未拷贝）
    for img in listed["images"]:
        assert img["file_path"].startswith(gallery_tree)
        assert os.path.isfile(img["file_path"])


def test_scan_is_incremental(tmp_db, gallery_tree):
    from manager.gallery_scanner import scan_gallery
    cfg = _FakeConfig([{"path": gallery_tree, "name": "lib", "enabled": True}])
    scan_gallery(tmp_db, cfg)
    res2 = scan_gallery(tmp_db, cfg)
    assert res2["added"] == 0
    assert res2["skipped"] == 4


def test_folder_filter_excludes_sibling_with_prefix(tmp_db, gallery_tree):
    from manager.gallery_scanner import scan_gallery
    cfg = _FakeConfig([{"path": gallery_tree, "name": "lib", "enabled": True}])
    scan_gallery(tmp_db, cfg)

    sub = os.path.join(gallery_tree, "sub")
    sub_x = os.path.join(gallery_tree, "sub_x")

    only_sub = tmp_db.list_workflow_images(page=1, page_size=100, folder=sub)
    # sub 下只有 b.png；sub_x 不能被算进来
    assert only_sub["total"] == 1
    assert only_sub["images"][0]["file_name"] == "b.png"

    only_sub_x = tmp_db.list_workflow_images(page=1, page_size=100, folder=sub_x)
    assert only_sub_x["total"] == 1
    assert only_sub_x["images"][0]["file_name"] == "c.png"


def test_folder_tree_counts_recursively(tmp_db, gallery_tree):
    from manager.gallery_scanner import scan_gallery, build_folder_tree
    cfg = _FakeConfig([{"path": gallery_tree, "name": "lib", "enabled": True, "builtin": False}])
    scan_gallery(tmp_db, cfg)

    tree = build_folder_tree(cfg, tmp_db.gallery_dir_counts())
    assert len(tree) == 1
    rootnode = tree[0]
    assert rootnode["count"] == 4  # 递归总数
    child_names = {c["name"]: c["count"] for c in rootnode["children"]}
    assert child_names.get("sub") == 1
    assert child_names.get("sub_x") == 1


def test_scan_prunes_deleted_files(tmp_db, gallery_tree):
    from manager.gallery_scanner import scan_gallery
    cfg = _FakeConfig([{"path": gallery_tree, "name": "lib", "enabled": True}])
    scan_gallery(tmp_db, cfg)

    os.remove(os.path.join(gallery_tree, "a.png"))
    res = scan_gallery(tmp_db, cfg)
    assert res["pruned"] == 1
    assert tmp_db.list_workflow_images(page=1, page_size=100, folder=gallery_tree)["total"] == 3


def test_delete_gallery_under(tmp_db, gallery_tree):
    from manager.gallery_scanner import scan_gallery
    cfg = _FakeConfig([{"path": gallery_tree, "name": "lib", "enabled": True}])
    scan_gallery(tmp_db, cfg)

    sub = os.path.join(gallery_tree, "sub")
    removed = tmp_db.delete_gallery_under(sub)
    assert removed == 1
    # 只删了 sub 下的，根仍有 3 条
    assert tmp_db.list_workflow_images(page=1, page_size=100, folder=gallery_tree)["total"] == 3


def test_config_gallery_folders_injects_builtin(tmp_path):
    from manager.config import Config
    cfg = Config(config_path=str(tmp_path / "manager_config.json"))
    extra = str(tmp_path / "extra")
    os.makedirs(extra, exist_ok=True)
    cfg.set("gallery_folders", [{"path": extra, "name": "extra", "enabled": True}])

    folders = cfg.gallery_folders
    # 第一个永远是内置「下载/导入」，builtin=True
    assert folders[0]["builtin"] is True
    assert any(f["path"] == extra and not f["builtin"] for f in folders)


def test_init_on_legacy_db_without_source_root(tmp_path):
    """回归：升级前的旧库（workflow_images 无 source_root 列）打开时必须平滑迁移。

    曾因 source_root 的索引建在 ALTER 加列之前，旧库建库即崩 → 整个 manager 起不来、
    模型页和图库都加载不出卡片。索引须在迁移之后建。"""
    import sqlite3
    db_path = str(tmp_path / "legacy.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE workflow_images (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            saved_at  REAL
        )
    """)
    conn.execute(
        "INSERT INTO workflow_images (file_path, file_name, saved_at) VALUES (?,?,?)",
        ("/legacy/a.png", "a.png", 1.0),
    )
    conn.commit()
    conn.close()

    from manager.database import ModelDatabase
    db = ModelDatabase(db_path)   # 不应抛异常
    assert "/legacy/a.png" in db.gallery_existing_paths()

    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(workflow_images)")}
    idxs = {r[1] for r in conn.execute("PRAGMA index_list(workflow_images)")}
    conn.close()
    assert "source_root" in cols          # 迁移补上了列
    assert "idx_wf_images_root" in idxs   # 索引在迁移之后成功建立


def test_config_gallery_folders_dedups_builtin(tmp_path):
    from manager.config import Config
    cfg = Config(config_path=str(tmp_path / "manager_config.json"))
    builtin = cfg.workflow_gallery_dir
    # 用户把内置目录又注册一遍 → 不应重复出现
    cfg.set("gallery_folders", [{"path": builtin, "name": "dup", "enabled": True}])
    paths = [f["path"] for f in cfg.gallery_folders]
    # 规范化后内置只出现一次
    norm = [os.path.normcase(os.path.normpath(os.path.abspath(p))) for p in paths]
    assert len(norm) == len(set(norm))
