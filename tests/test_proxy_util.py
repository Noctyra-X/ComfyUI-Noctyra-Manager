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

"""代理解析路由逻辑：http/https 走 aiohttp 原生 proxy=，socks 走 connector。"""

import pytest
from manager import proxy_util


class TestSchemeRouting:
    def test_is_socks(self):
        assert proxy_util._is_socks("socks5://x:1") is True
        assert proxy_util._is_socks("socks5h://x:1") is True
        assert proxy_util._is_socks("socks4://x:1") is True
        assert proxy_util._is_socks("http://x:1") is False
        assert proxy_util._is_socks("https://x:1") is False
        assert proxy_util._is_socks(None) is False
        assert proxy_util._is_socks("") is False

    def test_socks_proxy_not_passed_to_request(self, monkeypatch):
        # socks 代理：get_proxy_url 含它，但 get_proxy()（给 proxy= 用）返回 None
        monkeypatch.setattr(proxy_util, "_build_from_config", lambda: "socks5://127.0.0.1:7898")
        assert proxy_util.get_proxy_url() == "socks5://127.0.0.1:7898"
        assert proxy_util.get_proxy() is None

    def test_http_proxy_passthrough(self, monkeypatch):
        monkeypatch.setattr(proxy_util, "_build_from_config", lambda: "http://127.0.0.1:7897")
        assert proxy_util.get_proxy() == "http://127.0.0.1:7897"

    def test_make_connector_none_for_http(self, monkeypatch):
        # http 代理无需 ProxyConnector（走原生 proxy=），make_connector 返回 None
        monkeypatch.setattr(proxy_util, "_build_from_config", lambda: "http://127.0.0.1:7897")
        assert proxy_util.make_connector() is None

    def test_make_connector_none_when_no_proxy(self, monkeypatch):
        monkeypatch.setattr(proxy_util, "_build_from_config", lambda: None)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("http_proxy", raising=False)
        assert proxy_util.get_proxy() is None
        assert proxy_util.make_connector() is None

    def test_env_fallback_when_config_empty(self, monkeypatch):
        monkeypatch.setattr(proxy_util, "_build_from_config", lambda: None)
        monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:1080")
        assert proxy_util.get_proxy() == "http://10.0.0.1:1080"
