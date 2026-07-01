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

"""项目文件夹（data_root）引导层测试 —— 数据落点的安全关键逻辑。"""

import json
import os


def _np(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _setup(tmp_path, project_name="proj", write_pointer=True):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    project = tmp_path / project_name
    if write_pointer:
        (plugin / ".noctyra_data_root").write_text(str(project), encoding="utf-8")
    return str(plugin), str(project)


def test_no_pointer_is_legacy_mode(tmp_path):
    """无指针 → 传统插件目录模式，行为与改造前一致。"""
    from manager.config import Config
    plugin, _ = _setup(tmp_path, write_pointer=False)
    cfg = Config(plugin_dir=plugin)
    assert cfg.data_root == ""
    assert _np(cfg.cache_dir) == _np(os.path.join(plugin, ".cache"))
    assert _np(os.path.dirname(cfg._config_path)) == _np(plugin)


def test_pointer_enables_external_mode(tmp_path):
    """有指针 → 配置/库/图库都落在项目文件夹，且库平铺（无 .cache 隐藏层）。"""
    from manager.config import Config
    plugin, project = _setup(tmp_path)
    cfg = Config(plugin_dir=plugin)
    assert _np(cfg.data_root) == _np(project)
    assert _np(cfg.cache_dir) == _np(project)                       # 平铺
    assert _np(os.path.dirname(cfg._config_path)) == _np(project)   # 配置也在项目文件夹
    assert _np(cfg.workflow_gallery_dir) == _np(os.path.join(project, "gallery"))
    assert os.path.isdir(project)                                   # 自动建好


def test_external_mode_exempts_anti_residue(tmp_path):
    """关键：外部项目文件夹在插件目录外，但绝不能被「防迁移残留」逻辑重置掉。"""
    from manager.config import Config
    plugin, project = _setup(tmp_path)
    cfg = Config(plugin_dir=plugin)
    # cache_dir 指向插件外的项目文件夹，仍保持不变（旧逻辑会把它打回 <plugin>/.cache）
    assert _np(cfg.cache_dir) == _np(project)
    assert _np(cfg.cache_dir) != _np(os.path.join(plugin, ".cache"))


def test_external_mode_forces_cache_dir_over_stale_value(tmp_path):
    """项目文件夹的配置里若残留旧 cache_dir，外部模式强制覆盖为项目文件夹本身。"""
    from manager.config import Config
    plugin, project = _setup(tmp_path)
    os.makedirs(project, exist_ok=True)
    with open(os.path.join(project, "manager_config.json"), "w", encoding="utf-8") as f:
        json.dump({"cache_dir": "C:/old/stale/cache"}, f)
    cfg = Config(plugin_dir=plugin)
    assert _np(cfg.cache_dir) == _np(project)


def test_unusable_pointer_falls_back_without_data_loss(tmp_path):
    """指针指向不可用路径（外置盘未挂载等）→ 退回插件目录 + 置标志，绝不另起空库。"""
    from manager.config import Config
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")     # 父级是文件 → makedirs 必失败
    bad = str(tmp_path / "afile" / "sub")
    (plugin / ".noctyra_data_root").write_text(bad, encoding="utf-8")

    cfg = Config(plugin_dir=str(plugin))
    assert cfg.data_root == ""
    assert cfg.data_root_missing is True
    assert _np(cfg.cache_dir) == _np(os.path.join(str(plugin), ".cache"))


def test_write_and_clear_pointer_roundtrip(tmp_path):
    from manager.config import Config
    plugin, project = _setup(tmp_path, write_pointer=False)
    cfg = Config(plugin_dir=plugin)            # 传统模式启动
    cfg.write_data_root_pointer(project)
    assert _np(cfg._read_data_root_pointer(plugin)) == _np(project)
    cfg.write_data_root_pointer("")            # 清除 → 回退传统模式
    assert cfg._read_data_root_pointer(plugin) == ""


def _seed_legacy_data(cfg):
    """在传统模式的 cache/gallery 里造点数据。"""
    cache = cfg.cache_dir
    os.makedirs(os.path.join(cache, "previews"), exist_ok=True)
    with open(os.path.join(cache, "model_cache.sqlite"), "w", encoding="utf-8") as f:
        f.write("DB")
    with open(os.path.join(cache, "previews", "p.webp"), "w", encoding="utf-8") as f:
        f.write("img")
    gallery = cfg.workflow_gallery_dir
    os.makedirs(gallery, exist_ok=True)
    with open(os.path.join(gallery, "g.png"), "w", encoding="utf-8") as f:
        f.write("pic")
    cfg.save()
    return cache, gallery


def test_migrate_move_in_copies_everything(tmp_path):
    from manager.config import Config
    plugin, _ = _setup(tmp_path, write_pointer=False)
    cfg = Config(plugin_dir=plugin)
    _seed_legacy_data(cfg)

    target = str(tmp_path / "proj")
    res = cfg.migrate_to(target)
    assert res["success"] and res["mode"] == "move"
    assert os.path.isfile(os.path.join(target, "model_cache.sqlite"))
    assert os.path.isfile(os.path.join(target, "previews", "p.webp"))
    assert os.path.isfile(os.path.join(target, "gallery", "g.png"))
    assert os.path.isfile(os.path.join(target, "manager_config.json"))
    assert _np(cfg._read_data_root_pointer(plugin)) == _np(target)   # 指针已写


def test_migrate_adopt_does_not_overwrite(tmp_path):
    from manager.config import Config
    plugin, _ = _setup(tmp_path, write_pointer=False)
    cfg = Config(plugin_dir=plugin)
    target = tmp_path / "proj"
    target.mkdir()
    (target / "model_cache.sqlite").write_text("EXISTING", encoding="utf-8")  # 目标已有库

    res = cfg.migrate_to(str(target))
    assert res["success"] and res["mode"] == "adopt"
    assert (target / "model_cache.sqlite").read_text(encoding="utf-8") == "EXISTING"  # 没被覆盖
    assert _np(cfg._read_data_root_pointer(plugin)) == _np(str(target))


def test_cleanup_removes_old_local_data_on_next_boot(tmp_path):
    from manager.config import Config
    plugin, _ = _setup(tmp_path, write_pointer=False)
    cfg = Config(plugin_dir=plugin)
    old_cache, old_gallery = _seed_legacy_data(cfg)

    target = str(tmp_path / "proj")
    cfg.migrate_to(target)
    assert os.path.isdir(old_cache)   # 迁移当下旧数据仍在（运行时库开着，不当场删）

    # 下次启动：进外部模式、新库就绪 → 清理旧本地数据 + 删标记
    cfg2 = Config(plugin_dir=plugin)
    assert _np(cfg2.data_root) == _np(target)
    assert not os.path.isdir(old_cache)
    assert not os.path.isdir(old_gallery)
    assert not os.path.isfile(os.path.join(plugin, ".noctyra_cleanup_after_migrate"))


def test_cleanup_never_touches_project_folder(tmp_path):
    """护栏：清理绝不删项目文件夹里的数据。"""
    from manager.config import Config
    plugin, _ = _setup(tmp_path, write_pointer=False)
    cfg = Config(plugin_dir=plugin)
    _seed_legacy_data(cfg)
    target = str(tmp_path / "proj")
    cfg.migrate_to(target)
    cfg2 = Config(plugin_dir=plugin)
    # 新数据完好
    assert os.path.isfile(os.path.join(target, "model_cache.sqlite"))
    assert os.path.isfile(os.path.join(target, "gallery", "g.png"))
    assert _np(cfg2.cache_dir) == _np(target)
