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
ComfyUI-Noctyra-Manager

本地模型管理器 + 工作流图库 + CivitAI / HuggingFace 匹配
浏览器扩展：在 CivitAI 页面显示已下载、一键推送下载

自定义节点已独立为 ComfyUI-Noctyra 插件。
"""

__version__ = "1.0.0"

# 此插件不注册节点。保留空映射让 ComfyUI 加载时不报错。
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

# 注册模型管理器路由到 ComfyUI
try:
    from server import PromptServer
    from .manager.routes import setup_routes
    setup_routes(PromptServer.instance.app)
    from .manager.usage_tracker import setup_usage_tracking
    setup_usage_tracking()
    print(f"\033[34m[ComfyUI-Noctyra-Manager]\033[0m v{__version__} \033[92mLoaded\033[0m")
except Exception as e:
    import traceback
    print(f"\033[34m[ComfyUI-Noctyra-Manager]\033[0m v{__version__} \033[91m{e}\033[0m")
    traceback.print_exc()

WEB_DIRECTORY = "web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
