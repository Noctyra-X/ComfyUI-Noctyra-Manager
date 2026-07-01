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

"""统一的代理解析：config 优先（类型 + host:port + 可选账号密码），回退环境变量。

被 huggingface / civarchive / preview_cache / civitai 复用，避免各自一份逻辑漂移。

http/https 代理走 aiohttp 原生的 `proxy=` 参数；socks 代理 aiohttp 的 `proxy=` 不支持，
必须在建 session 时用 aiohttp_socks 的 ProxyConnector。所以本模块对外给两件东西：
  - get_proxy()       → 给 `session.get(proxy=...)` 用：http/https 返回 URL，socks 返回 None
  - make_connector()  → 给 `ClientSession(connector=...)` 用：socks 返回 ProxyConnector，否则 None
调用方两个都用上即可同时支持 http/https/socks。向后兼容：默认 type=http、无认证，
等价于旧的 `http://host:port`。
"""

import logging
import os
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger("noctyra.proxy")

_SOCKS_SCHEMES = ("socks5", "socks5h", "socks4", "socks4a")
_VALID_SCHEMES = ("http", "https") + _SOCKS_SCHEMES


def _build_from_config() -> Optional[str]:
    """从 config 拼完整代理 URL（含 type/认证）。未启用或缺 host/port → None。"""
    try:
        from .config import get_config
        cfg = get_config()
    except Exception:
        return None
    if not cfg.get("proxy_enabled"):
        return None
    host = (cfg.get("proxy_host") or "").strip()
    port = (cfg.get("proxy_port") or "").strip()
    if not (host and port):
        return None
    ptype = (cfg.get("proxy_type") or "http").strip().lower()
    if ptype not in _VALID_SCHEMES:
        ptype = "http"
    user = (cfg.get("proxy_username") or "").strip()
    pwd = cfg.get("proxy_password") or ""  # 密码不 strip（可能含首尾特殊字符）
    if user:
        # 账号/密码里的 :@/ 等需转义，否则破坏 URL 结构
        auth = f"{quote(user, safe='')}:{quote(str(pwd), safe='')}@"
    else:
        auth = ""
    return f"{ptype}://{auth}{host}:{port}"


def get_proxy_url() -> Optional[str]:
    """完整代理 URL（含 type/认证，socks 也在内）。config 优先，回退环境变量。
    供 make_connector 判断 scheme 用；一般调用方用 get_proxy()/make_connector() 即可。"""
    url = _build_from_config()
    if url:
        return url
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            or os.environ.get("https_proxy") or os.environ.get("http_proxy"))


def _is_socks(url: Optional[str]) -> bool:
    return bool(url) and url.lower().split("://", 1)[0] in _SOCKS_SCHEMES


def get_proxy() -> Optional[str]:
    """给 aiohttp 的 `proxy=` 参数用。http/https 代理 → 返回 URL；socks 代理 → 返回 None
    （socks 由 make_connector 处理，proxy= 不支持）。无代理 → None。"""
    url = get_proxy_url()
    return None if _is_socks(url) else url


def make_connector():
    """给 `aiohttp.ClientSession(connector=...)` 用。socks 代理 → aiohttp_socks.ProxyConnector；
    其余（http/https 代理或无代理）→ None，让 aiohttp 用默认 TCPConnector。
    aiohttp_socks 缺失或 URL 异常时记一条日志并回退直连（None）。"""
    url = get_proxy_url()
    if _is_socks(url):
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(url)
        except Exception as e:
            logger.warning("[Noctyra-MM] socks 代理初始化失败（%s），回退直连", e)
    return None
