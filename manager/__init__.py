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
Noctyra Model Manager

模型管理模块，支持：
- 本地模型扫描（safetensors 元数据读取、SHA256 计算）
- CivitAI 模型信息匹配（哈希查询）
- HuggingFace 模型信息匹配（搜索 + 手动绑定）
- SQLite 持久缓存
- Web 管理界面（集成到 ComfyUI）
"""

__version__ = "1.0.1"
