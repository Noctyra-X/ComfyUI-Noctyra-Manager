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
recipes 模块测试：fingerprint_v1 算法的确定性、顺序无关性、权重处理。
"""
from manager.recipes import (
    fingerprint_v1, compute_fingerprint, short_fingerprint,
    extract_base_model_from_image_info,
)


def _lora(vid=None, name="", weight=1.0):
    r = {"type": "lora"}
    if vid is not None:
        r["modelVersionId"] = vid
    if name:
        r["name"] = name
    r["weight"] = weight
    return r


class TestFingerprintV1:
    def test_empty_inputs_return_valid_hash(self):
        fp = fingerprint_v1("", [])
        assert isinstance(fp, str) and len(fp) == 64

    def test_deterministic(self):
        bm = "Flux.1 D"
        r = [_lora(vid=111, weight=0.8), _lora(vid=222, weight=1.0)]
        assert fingerprint_v1(bm, r) == fingerprint_v1(bm, r)

    def test_order_independent(self):
        """LoRA 顺序不同，指纹相同"""
        bm = "Flux.1 D"
        r1 = [_lora(vid=111, weight=0.8), _lora(vid=222, weight=1.0)]
        r2 = [_lora(vid=222, weight=1.0), _lora(vid=111, weight=0.8)]
        assert fingerprint_v1(bm, r1) == fingerprint_v1(bm, r2)

    def test_different_weights_different_fingerprint(self):
        bm = "Flux.1 D"
        r1 = [_lora(vid=111, weight=0.8)]
        r2 = [_lora(vid=111, weight=0.9)]
        assert fingerprint_v1(bm, r1) != fingerprint_v1(bm, r2)

    def test_different_base_model_different_fingerprint(self):
        r = [_lora(vid=111, weight=1.0)]
        assert fingerprint_v1("Flux.1 D", r) != fingerprint_v1("SDXL", r)

    def test_base_model_normalization_case_and_space(self):
        r = [_lora(vid=111, weight=1.0)]
        # 大小写 + 多空格视为同一 base_model
        assert fingerprint_v1("Flux.1 D", r) == fingerprint_v1("FLUX.1  D", r)

    def test_weight_rounding_to_4_decimal(self):
        bm = "SDXL"
        r1 = [_lora(vid=111, weight=0.80001)]
        r2 = [_lora(vid=111, weight=0.80002)]
        # 第 5 位差异在 4 位小数归一化后应被抹平
        assert fingerprint_v1(bm, r1) == fingerprint_v1(bm, r2)

    def test_includes_checkpoint(self):
        """同 LoRA 组合、不同 checkpoint 应产生不同指纹"""
        bm = "SDXL"
        lora = _lora(vid=111, weight=1.0)
        r1 = [{"type": "checkpoint", "modelVersionId": 500}, lora]
        r2 = [{"type": "checkpoint", "modelVersionId": 501}, lora]
        assert fingerprint_v1(bm, r1) != fingerprint_v1(bm, r2)

    def test_lora_alias_types_treated_as_lora(self):
        """LoCon / DoRA / LyCORIS 作为 LoRA 的别名，应和纯 LoRA 组合同等对待"""
        bm = "SDXL"
        r1 = [_lora(vid=111, weight=1.0)]
        r2 = [{"type": "locon", "modelVersionId": 111, "weight": 1.0}]
        assert fingerprint_v1(bm, r1) == fingerprint_v1(bm, r2)

    def test_non_lora_non_checkpoint_ignored(self):
        """VAE / ControlNet / embedding 不参与指纹计算"""
        bm = "SDXL"
        r1 = [_lora(vid=111, weight=1.0)]
        r2 = [_lora(vid=111, weight=1.0), {"type": "vae", "modelVersionId": 999}]
        assert fingerprint_v1(bm, r1) == fingerprint_v1(bm, r2)

    def test_fallback_to_name_when_no_version_id(self):
        """资源无 version_id 时按 name 参与指纹"""
        bm = "SDXL"
        r1 = [_lora(name="StyleA.safetensors", weight=1.0)]
        r2 = [_lora(name="StyleB.safetensors", weight=1.0)]
        assert fingerprint_v1(bm, r1) != fingerprint_v1(bm, r2)


class TestComputeFingerprint:
    def test_returns_version_info(self):
        info = compute_fingerprint("SDXL", [])
        assert info["recipe_version"] == 1
        assert len(info["fingerprint"]) == 64

    def test_unknown_version_falls_back_to_v1(self):
        info = compute_fingerprint("SDXL", [], version=99)
        assert info["recipe_version"] == 1


class TestShortFingerprint:
    def test_truncation(self):
        fp = "a" * 64
        assert short_fingerprint(fp) == "a" * 8
        assert short_fingerprint(fp, length=12) == "a" * 12

    def test_empty(self):
        assert short_fingerprint("") == ""


class TestExtractBaseModel:
    def test_from_meta_basemodel_field(self):
        info = {"meta": {"baseModel": "Flux.1 D"}}
        assert extract_base_model_from_image_info(info) == "Flux.1 D"

    def test_from_checkpoint_resource(self):
        info = {
            "meta": {
                "resources": [
                    {"type": "checkpoint", "baseModel": "SDXL", "modelVersionId": 1}
                ]
            }
        }
        assert extract_base_model_from_image_info(info) == "SDXL"

    def test_from_meta_model_field_as_last_resort(self):
        info = {"meta": {"Model": "some_checkpoint.safetensors"}}
        # 不严格是 base_model，但兜底返回
        assert extract_base_model_from_image_info(info) == "some_checkpoint.safetensors"

    def test_empty_when_nothing_available(self):
        assert extract_base_model_from_image_info({}) == ""
