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
Noctyra 模型管理器 — 独立运行入口

不依赖 ComfyUI，直接起一个 aiohttp 服务跑全套功能（除 usage_tracker 外）。
主要用例：
  - 日常只想扫模型、下 CivitAI、整理图库、不开 ComfyUI 的时候
  - 跨机器调试路由 / 前端

用法（在 ComfyUI-Noctyra 插件目录下执行）：

  # Windows 便携版直接用捆绑的嵌入式 python：
  ..\\..\\python_embeded\\python.exe -m manager

  # 或系统 python（需要已装 aiohttp）：
  python -m manager

  # 指定端口（默认读 manager_config.json 里的 server_port，缺省 8199）：
  python -m manager --port 9000

  # 指定绑定地址（默认 127.0.0.1，局域网访问可写 0.0.0.0）：
  python -m manager --host 0.0.0.0
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import webbrowser
from pathlib import Path

# 允许从插件根目录执行时能 import 相对包
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# 默认探测的 ComfyUI 端口；ComfyUI 主程序自己的端口，不等于独立模式的 server_port
_COMFYUI_DEFAULT_PORT = 8188
# 探测间隔和连续命中阈值
_PROBE_INTERVAL_SECONDS = 10
_REQUIRED_HITS = 2
# 倒计时后再真正关闭，给用户/前端 toast 的时间
_SHUTDOWN_GRACE_SECONDS = 5


async def _probe_comfyui(port: int) -> bool:
    """探测指定端口上的 ComfyUI-Noctyra 插件是否在线。用 extension/ping 端点，轻量。"""
    import aiohttp
    url = f"http://127.0.0.1:{port}/api/noctyra/extension/ping"
    timeout = aiohttp.ClientTimeout(total=2)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status == 200
    except Exception:
        return False


async def _comfyui_watchdog(app, config, comfyui_port: int, logger):
    """后台循环：探测到 ComfyUI 运行中则优雅退出独立模式。"""
    from manager.websocket import get_progress_ws
    ws = get_progress_ws()
    consecutive = 0
    logger.info("[Noctyra] ComfyUI 自动退出监听已启动（探测端口 %d，连续 %d 次命中触发）",
                comfyui_port, _REQUIRED_HITS)
    while True:
        try:
            await asyncio.sleep(_PROBE_INTERVAL_SECONDS)
            hit = await _probe_comfyui(comfyui_port)
            if hit:
                consecutive += 1
                logger.debug("[Noctyra] ComfyUI 探测命中 %d/%d", consecutive, _REQUIRED_HITS)
                if consecutive >= _REQUIRED_HITS:
                    logger.info("[Noctyra] 检测到 ComfyUI 已启动，准备在 %d 秒后退出独立模式",
                                _SHUTDOWN_GRACE_SECONDS)
                    try:
                        await ws.broadcast("standalone_shutdown_warning", {
                            "seconds": _SHUTDOWN_GRACE_SECONDS,
                            "reason": "comfyui_detected",
                            "comfyui_port": comfyui_port,
                        })
                    except Exception as e:
                        logger.debug("[Noctyra] WS 广播失败（无客户端？）：%s", e)
                    await asyncio.sleep(_SHUTDOWN_GRACE_SECONDS)
                    _trigger_graceful_shutdown(logger)
                    return
            else:
                if consecutive > 0:
                    logger.debug("[Noctyra] ComfyUI 探测失败，重置计数")
                consecutive = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[Noctyra] watchdog 循环异常: %s", e)


def _trigger_graceful_shutdown(logger):
    """触发 aiohttp web.run_app 的优雅退出路径。
    Windows 下 os.kill(pid, SIGINT) 会把 KeyboardInterrupt 抛到 run_app 里，
    run_app 会自动走 cleanup。"""
    logger.info("[Noctyra] 独立模式正在退出...")
    try:
        if sys.platform == "win32":
            # Windows 的 signal.CTRL_C_EVENT 只能发给 console group，用 SIGINT 更稳
            os.kill(os.getpid(), signal.SIGINT)
        else:
            os.kill(os.getpid(), signal.SIGTERM)
    except Exception as e:
        # 信号发送失败时不要直接 os._exit（会跳过 aiohttp cleanup / DB 关闭，
        # 可能留下 .wal 残留或损坏 tmp 文件）。用 SystemExit 让事件循环走正常退出路径。
        logger.warning("[Noctyra] 优雅退出信号发送失败：%s；抛 SystemExit 让 run_app 清理", e)
        raise SystemExit(1)


def _configure_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # aiohttp.access 默认非常啰嗦，降一级
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(
        prog="python -m manager",
        description="Noctyra 模型管理器独立服务（无需 ComfyUI）",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="绑定地址；0.0.0.0 = 全部接口（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=None,
                        help="监听端口（默认读 manager_config.json 的 server_port，缺省 8199）")
    parser.add_argument("--comfyui-port", type=int, default=_COMFYUI_DEFAULT_PORT,
                        help=f"ComfyUI 监听端口，用于自动退出探测（默认 {_COMFYUI_DEFAULT_PORT}）")
    parser.add_argument("--no-auto-shutdown", action="store_true",
                        help="禁用 ComfyUI 自动退出探测（即使配置文件里开着也不生效）")
    parser.add_argument("--no-browser", action="store_true",
                        help="启动后不自动打开浏览器")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="打开 DEBUG 级别日志")
    args = parser.parse_args()

    _configure_logging(args.verbose)
    logger = logging.getLogger("noctyra.standalone")

    # 延迟 import：先把 sys.path 改好，再让包解析相对导入
    try:
        from aiohttp import web
        from manager.routes import setup_routes
        from manager.config import get_config
        from manager.routes_common import set_runtime_mode
    except ImportError as e:
        print(f"[Noctyra] 启动失败：依赖缺失 — {e}")
        print("请确认已安装 aiohttp。便携版 ComfyUI 自带；系统 python 执行 `pip install aiohttp`。")
        sys.exit(1)

    # 让后端各路由返回 mode=standalone，前端据此渲染徽章
    set_runtime_mode("standalone")

    cfg = get_config()
    port = args.port if args.port is not None else cfg.server_port

    app = web.Application(client_max_size=1024 * 1024 * 512)  # 512MB 上限给导入大图用
    setup_routes(app)

    # 自动退出探测：配置项 + CLI 参数双重开关（任一关就关）
    auto_shutdown = cfg.get("auto_shutdown_on_comfyui", True) and not args.no_auto_shutdown
    if auto_shutdown:
        async def _start_watchdog(_app):
            _app["_comfyui_watchdog_task"] = asyncio.create_task(
                _comfyui_watchdog(_app, cfg, args.comfyui_port, logger)
            )

        async def _stop_watchdog(_app):
            task = _app.get("_comfyui_watchdog_task")
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        app.on_startup.append(_start_watchdog)
        app.on_cleanup.append(_stop_watchdog)

    # 启动后自动打开浏览器（--no-browser 可关）
    if not args.no_browser:
        browser_host = "localhost" if args.host in ("0.0.0.0", "::") else args.host
        browser_url = f"http://{browser_host}:{port}/noctyra-manager"

        async def _auto_open_browser(_app):
            async def _open_after_ready():
                # 等 aiohttp 真正开始监听再开浏览器，避免用户看到 connection refused
                await asyncio.sleep(0.8)
                try:
                    webbrowser.open(browser_url, new=2)
                    logger.info("[Noctyra] 已在默认浏览器打开：%s", browser_url)
                except Exception as e:
                    logger.warning("[Noctyra] 自动打开浏览器失败：%s（请手动访问 %s）", e, browser_url)
            _app["_browser_open_task"] = asyncio.create_task(_open_after_ready())

        app.on_startup.append(_auto_open_browser)

    # 独立模式跳过 usage_tracker（它钩 ComfyUI 的 PromptServer，独立启时无意义）
    logger.info("[Noctyra] 独立模式启动：usage_tracker 未加载（无 ComfyUI）")
    logger.info("[Noctyra] 配置文件：%s", cfg._config_path)
    logger.info("[Noctyra] 缓存目录：%s", cfg.cache_dir)
    logger.info("[Noctyra] 自动退出：%s", "开（探测 ComfyUI 端口 %d）" % args.comfyui_port
                if auto_shutdown else "关")
    logger.info("[Noctyra] 模型目录（%d 个）：", len(cfg.model_roots))
    for r in cfg.model_roots:
        logger.info("[Noctyra]   - %s", r)

    url_host = "localhost" if args.host == "0.0.0.0" else args.host
    logger.info("=" * 60)
    logger.info("[Noctyra] 模型管理器：http://%s:%d/noctyra-manager", url_host, port)
    logger.info("[Noctyra] 工作流图库：http://%s:%d/noctyra-workflows", url_host, port)
    logger.info("=" * 60)

    try:
        web.run_app(app, host=args.host, port=port, print=None)
    except KeyboardInterrupt:
        logger.info("[Noctyra] 已退出")


if __name__ == "__main__":
    main()
