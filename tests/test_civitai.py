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
civitai helpers 测试：is_civitai_host / build_model_url / build_image_url。
"""

from manager.civitai import is_civitai_host, build_model_url, build_image_url, CivitaiClient


class TestIsCivitaiHost:
    def test_com(self):
        assert is_civitai_host("https://civitai.com/models/123")

    def test_red(self):
        assert is_civitai_host("https://civitai.red/models/456")

    def test_green(self):
        assert is_civitai_host("https://civitai.green/images/1")

    def test_case_insensitive(self):
        assert is_civitai_host("https://CIVITAI.COM/models/1")

    def test_rejects_huggingface(self):
        assert not is_civitai_host("https://huggingface.co/user/repo")

    def test_rejects_similar_names(self):
        # 主机名边界匹配，不做子串——否则伪造域名会骗到 CivitAI API key（凭据泄露）
        assert not is_civitai_host("https://notcivitai.example.com/models/1")
        assert not is_civitai_host("https://example.com/civitai.com/foo")  # civitai.com 只在路径里
        assert not is_civitai_host("https://civitai.com.evil.net/foo")     # 伪造子域前缀
        assert is_civitai_host("https://image.civitai.com/xxx")            # 真实子域应通过

    def test_empty(self):
        assert not is_civitai_host("")
        assert not is_civitai_host(None)


class TestBuildModelUrl:
    def test_with_version_id(self, tmp_path, monkeypatch):
        """带 version_id 时加 ?modelVersionId=N"""
        from manager.config import Config
        import manager.config as cfg_mod
        import manager.civitai as civ  # noqa: F401
        cfg = Config(config_path=str(tmp_path / "c.json"))
        # build_model_url / get_source_host 里 `from .config import get_config`
        # 是函数内局部导入，会从 manager.config 拿到模块级 get_config。patch 模块级符号。
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)
        assert build_model_url(123, 456) == "https://civitai.com/models/123?modelVersionId=456"

    def test_without_version_id(self, tmp_path, monkeypatch):
        from manager.config import Config
        import manager.config as cfg_mod
        import manager.civitai as civ  # noqa: F401
        cfg = Config(config_path=str(tmp_path / "c.json"))
        # build_model_url / get_source_host 里 `from .config import get_config`
        # 是函数内局部导入，会从 manager.config 拿到模块级 get_config。patch 模块级符号。
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)
        assert build_model_url(123) == "https://civitai.com/models/123"

    def test_respects_source_host_setting(self, tmp_path, monkeypatch):
        from manager.config import Config
        import manager.config as cfg_mod
        cfg = Config(config_path=str(tmp_path / "c.json"))
        cfg.set("civitai_source_host", "civitai.red")
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)
        assert build_model_url(123) == "https://civitai.red/models/123"

    def test_empty_model_id(self, tmp_path, monkeypatch):
        from manager.config import Config
        import manager.config as cfg_mod
        import manager.civitai as civ  # noqa: F401
        cfg = Config(config_path=str(tmp_path / "c.json"))
        # build_model_url / get_source_host 里 `from .config import get_config`
        # 是函数内局部导入，会从 manager.config 拿到模块级 get_config。patch 模块级符号。
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)
        assert build_model_url(None) == ""
        assert build_model_url(0) == ""


class TestBuildImageUrl:
    def test_basic(self, tmp_path, monkeypatch):
        from manager.config import Config
        import manager.config as cfg_mod
        import manager.civitai as civ  # noqa: F401
        cfg = Config(config_path=str(tmp_path / "c.json"))
        # build_model_url / get_source_host 里 `from .config import get_config`
        # 是函数内局部导入，会从 manager.config 拿到模块级 get_config。patch 模块级符号。
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)
        assert build_image_url(777) == "https://civitai.com/images/777"

    def test_empty(self, tmp_path, monkeypatch):
        from manager.config import Config
        import manager.config as cfg_mod
        import manager.civitai as civ  # noqa: F401
        cfg = Config(config_path=str(tmp_path / "c.json"))
        # build_model_url / get_source_host 里 `from .config import get_config`
        # 是函数内局部导入，会从 manager.config 拿到模块级 get_config。patch 模块级符号。
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)
        assert build_image_url(None) == ""


class TestParseRetryAfter:
    def test_seconds(self):
        assert CivitaiClient._parse_retry_after("60") == 60

    def test_float_seconds(self):
        assert CivitaiClient._parse_retry_after("12.9") == 12

    def test_none_and_empty(self):
        assert CivitaiClient._parse_retry_after(None) is None
        assert CivitaiClient._parse_retry_after("") is None

    def test_http_date_future(self):
        # 远未来的 HTTP-date 应解析出正数秒（之前只认秒数会误退回默认 60s）
        import datetime
        from email.utils import format_datetime
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=120)
        secs = CivitaiClient._parse_retry_after(format_datetime(future))
        assert secs is not None and 90 < secs <= 120

    def test_http_date_past_returns_none(self):
        # 已过去的日期 → 非正数 → None
        assert CivitaiClient._parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None

    def test_garbage_returns_none(self):
        assert CivitaiClient._parse_retry_after("not-a-date") is None
