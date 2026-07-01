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

"""safetensors 文件头读取测试（结构 Tab 用）。"""

import json
import struct


def _write_safetensors(path, header: dict, data_len: int = 0):
    hb = json.dumps(header).encode("utf-8")
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(hb)))
        f.write(hb)
        if data_len:
            f.write(b"\x00" * data_len)


def test_read_header_parses_metadata_and_tensors(tmp_path):
    from manager.scanner import read_safetensors_header
    p = str(tmp_path / "m.safetensors")
    _write_safetensors(p, {
        "model.weight": {"dtype": "F8_E4M3", "shape": [1024, 256], "data_offsets": [0, 262144]},
        "model.bias": {"dtype": "BF16", "shape": [1024], "data_offsets": [262144, 264192]},
        "__metadata__": {"format": "pt", "modelspec.title": "Test", "prompt": '{"1":{}}'},
    }, data_len=8)

    info = read_safetensors_header(p)
    assert info["metadata"]["modelspec.title"] == "Test"
    assert info["metadata"]["prompt"] == '{"1":{}}'
    by = {t["name"]: t for t in info["tensors"]}
    assert "__metadata__" not in by                  # 元数据不混进张量列表
    assert by["model.weight"]["dtype"] == "F8_E4M3"
    assert by["model.weight"]["shape"] == [1024, 256]
    assert by["model.weight"]["n_bytes"] == 262144
    assert by["model.bias"]["dtype"] == "BF16"
    assert info["tensor_count"] == 2


def test_read_header_rejects_non_safetensors(tmp_path):
    from manager.scanner import read_safetensors_header
    p = str(tmp_path / "m.ckpt")
    with open(p, "wb") as f:
        f.write(b"not safetensors")
    info = read_safetensors_header(p)
    assert info["tensors"] == []
    assert "error" in info


def test_read_header_handles_no_metadata(tmp_path):
    from manager.scanner import read_safetensors_header
    p = str(tmp_path / "m.safetensors")
    _write_safetensors(p, {
        "w": {"dtype": "F16", "shape": [2, 2], "data_offsets": [0, 8]},
    }, data_len=8)
    info = read_safetensors_header(p)
    assert info["metadata"] == {}
    assert info["tensor_count"] == 1


def test_read_header_rejects_garbage_length(tmp_path):
    from manager.scanner import read_safetensors_header
    p = str(tmp_path / "bad.safetensors")
    with open(p, "wb") as f:
        f.write(struct.pack("<Q", 10 ** 12))   # 头长度荒谬 → 拒绝
        f.write(b"{}")
    info = read_safetensors_header(p)
    assert info["tensors"] == []
    assert "error" in info
