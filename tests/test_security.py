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

"""路径白名单校验测试 —— 删除/移动等危险文件操作前的安全护栏。"""
import os

from manager.routes_common import path_within_roots


def test_inside_root(tmp_path):
    root = str(tmp_path / "models")
    os.makedirs(root)
    f = os.path.join(root, "a.safetensors")
    assert path_within_roots(f, [root]) is True


def test_nested_inside_root(tmp_path):
    root = str(tmp_path / "models")
    f = os.path.join(root, "lora", "sub", "x.safetensors")
    assert path_within_roots(f, [root]) is True


def test_root_itself(tmp_path):
    root = str(tmp_path / "models")
    assert path_within_roots(root, [root]) is True


def test_outside_root_rejected(tmp_path):
    root = str(tmp_path / "models")
    evil = str(tmp_path / "secret" / "passwords.txt")
    assert path_within_roots(evil, [root]) is False


def test_sibling_prefix_not_bypass(tmp_path):
    # /models 不应允许 /models_evil（前缀相同但非子目录）
    root = str(tmp_path / "models")
    evil = str(tmp_path / "models_evil" / "x")
    assert path_within_roots(evil, [root]) is False


def test_traversal_rejected(tmp_path):
    root = str(tmp_path / "models")
    os.makedirs(root)
    evil = os.path.join(root, "..", "etc", "shadow")
    assert path_within_roots(evil, [root]) is False


def test_empty_path_rejected(tmp_path):
    assert path_within_roots("", [str(tmp_path)]) is False
    assert path_within_roots(None, [str(tmp_path)]) is False


def test_no_roots_rejected(tmp_path):
    f = str(tmp_path / "x")
    assert path_within_roots(f, []) is False
    assert path_within_roots(f, [None, ""]) is False


def test_multiple_roots(tmp_path):
    r1 = str(tmp_path / "a")
    r2 = str(tmp_path / "b")
    f = os.path.join(r2, "x.safetensors")
    assert path_within_roots(f, [r1, r2]) is True
