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
pytest 共享 fixture。

测试隔离原则：
- 每个需要 DB 的测试用独立的 tmp_path，不污染用户真实 .cache
- 不依赖 ComfyUI：我们不 import __init__.py，直接 import manager.* 子模块
"""

import os
import sys

# 保证 `from manager.xxx import ...` 能解析
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """提供一个隔离的临时数据库实例，每次测试都从空库开始。"""
    from manager.database import ModelDatabase
    db_path = str(tmp_path / "test_cache.sqlite")
    db = ModelDatabase(db_path)
    yield db
