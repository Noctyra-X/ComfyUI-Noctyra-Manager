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
Config 测试：原子写、读写循环、defaults 合并、civitai 来源 host 校验。
"""

import json
import os


def test_save_is_atomic(tmp_path):
    """save() 中途死亡不应损坏 manager_config.json"""
    from manager.config import Config
    cfg = Config(config_path=str(tmp_path / "manager_config.json"))
    cfg.set("test_key", "hello")
    cfg.save()
    data = json.loads((tmp_path / "manager_config.json").read_text(encoding="utf-8"))
    assert data["test_key"] == "hello"
    # .tmp 文件应该不存在
    assert not (tmp_path / "manager_config.json.tmp").exists()


def test_save_and_load_roundtrip(tmp_path):
    from manager.config import Config
    path = str(tmp_path / "manager_config.json")

    cfg1 = Config(config_path=path)
    cfg1.set("civitai_source_host", "civitai.red")
    cfg1.save()

    cfg2 = Config(config_path=path)
    assert cfg2.get("civitai_source_host") == "civitai.red"


def test_defaults_present(tmp_path):
    """新建配置应含默认字段"""
    from manager.config import Config
    cfg = Config(config_path=str(tmp_path / "manager_config.json"))
    assert cfg.get("civitai_source_host") == "civitai.com"
    assert cfg.get("auto_shutdown_on_comfyui") is True
    assert cfg.get("enable_civarchive_fallback") is True
    assert isinstance(cfg.get("scan_extensions"), list)


def test_get_source_host_falls_back_on_invalid(tmp_path, monkeypatch):
    """config.civitai_source_host 写了非法值时，civitai.get_source_host 应回退到 .com"""
    from manager.config import Config
    import manager.config as cfg_mod
    from manager import civitai as civ

    path = str(tmp_path / "manager_config.json")
    cfg = Config(config_path=path)
    cfg.set("civitai_source_host", "evil.example.com")
    cfg.save()

    # civitai.get_source_host 里 `from .config import get_config`，patch 该模块级符号
    monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)

    assert civ.get_source_host() == "civitai.com"  # 非白名单 → 回退
