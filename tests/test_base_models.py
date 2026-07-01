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

"""base_model 对齐 CivitAI 官方枚举的测试。"""

import os


def test_aligns_our_old_names_to_civitai_official():
    from manager.base_models import normalize_base_model as n
    # 我们以前自造的名 → CivitAI 官方
    assert n("Qwen Image") == "Qwen"
    assert n("qwen_image_edit") == "Qwen"
    assert n("Flux 1") == "Flux.1 D"
    assert n("SDXL") == "SDXL 1.0"
    assert n("ltx2") == "LTXV2"


def test_official_names_pass_through():
    from manager.base_models import normalize_base_model as n
    for name in ["Qwen", "SDXL 1.0", "Flux.1 D", "Flux.1 Kontext", "Illustrious",
                 "Anima", "NoobAI", "SD 1.5", "Chroma", "ZImageTurbo",
                 "LTXV2", "LTXV 2.3", "LTXV", "Flux.2 Klein 9B", "Flux.2 Klein 9B-base"]:
        assert n(name) == name, name


def test_civitai_granularity_not_merged():
    """CivitAI 官方区分的不同底模绝不合并（这是上一轮差点犯的错）。"""
    from manager.base_models import normalize_base_model as n
    assert n("LTXV2") != n("LTXV 2.3")                         # LTX 不同版本
    assert n("Flux.2 Klein 9B") != n("Flux.2 Klein 9B-base")   # base 与非 base
    assert n("Flux.2 Klein 9B") != n("Flux.2 Klein 4B-base")   # 9B 与 4B
    assert n("Flux.1 D") != n("Flux.1 Kontext")               # Dev 与 Kontext
    # Wan：不折叠成泛化 "Wan Video"，保留版本区分（2.1 与 2.2 是不同架构）
    assert n("Wan 2.2") == "Wan 2.2"
    assert n("Wan 2.1") == "Wan 2.1"
    assert n("Wan 2.1") != n("Wan 2.2")


def test_metadata_variants_to_official():
    from manager.base_models import normalize_base_model as n
    assert n("sd_v1-5") == "SD 1.5"
    assert n("sdxl_base") == "SDXL 1.0"
    assert n("flux1") == "Flux.1 D"
    assert n("flux1-schnell") == "Flux.1 S"


def test_empty_and_passthrough():
    from manager.base_models import normalize_base_model as n
    assert n("") == "Unknown"
    assert n(None) == "Unknown"
    assert n("Other") == "Unknown"
    # 官方列表里还没有的新模型 → 原样保留（等动态拉取补上）
    assert n("BrandNewModel2027") == "BrandNewModel2027"


def test_dynamic_set_official_recognizes_new():
    from manager import base_models as bm
    assert bm.normalize_base_model("FutureModel X") == "FutureModel X"  # 未知
    try:
        bm.set_official_models(["FutureModel X"])
        assert bm.normalize_base_model("futuremodel x") == "FutureModel X"  # 拉取后认得（含大小写归一）
    finally:
        bm.set_official_models(list(bm._OFFICIAL_FALLBACK))  # 复位，避免污染其他测试


# 原 test_determine_base_model_* 已删除：base_model 不再从 safetensors 头推断
# （改为只用 CivitAI 匹配的权威值，未匹配留 Unknown）。


def test_db_normalize_realigns_to_official(tmp_db):
    conn = tmp_db._connect()
    for fp, bm in [("/a/x", "Qwen Image"), ("/a/y", "Flux 1"), ("/a/z", "SDXL"),
                   ("/a/w", "ltx2"), ("/a/k", "LTXV 2.3")]:
        conn.execute("INSERT INTO models (file_path, file_name, base_model) VALUES (?,?,?)",
                     (fp, os.path.basename(fp) + ".safetensors", bm))
    conn.commit()
    conn.close()

    tmp_db.normalize_base_models()

    conn = tmp_db._connect(readonly=True)
    rows = {r["file_path"]: r["base_model"] for r in conn.execute("SELECT file_path, base_model FROM models").fetchall()}
    conn.close()
    assert rows["/a/x"] == "Qwen"
    assert rows["/a/y"] == "Flux.1 D"
    assert rows["/a/z"] == "SDXL 1.0"
    assert rows["/a/w"] == "LTXV2"
    assert rows["/a/k"] == "LTXV 2.3"     # 官方名,不变
