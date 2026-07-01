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
路径模板渲染测试：覆盖 _render_path_template 的占位符替换、fallback 行为。
"""

import os


def _render(template: str, model: dict, base_model: str = "SDXL"):
    """直接访问 _OrganizeMixin._render_path_template（不用构造 ModelManager）"""
    from manager.manager_organize import _OrganizeMixin

    class _Stub(_OrganizeMixin):
        class _Cfg:
            def get(self, key, default=None):
                return {"base_model_path_mappings": {}}.get(key, default)
        config = _Cfg()

    return _Stub()._render_path_template(template, model, base_model)


def test_empty_template_returns_empty():
    assert _render("", {}, "SDXL") == ""


def test_base_model_placeholder():
    assert _render("{base_model}", {}, "SDXL") == "SDXL"


def test_author_creator_alias():
    m = {"creator": "alice"}
    assert _render("{author}", m) == "alice"
    assert _render("{creator}", m) == "alice"


def test_model_name_fallback_to_stem():
    """没 model_name 时回退到 file_path 的 stem"""
    m = {"file_path": "D:/models/loras/TestLora.safetensors"}
    assert _render("{model_name}", m, "SDXL") == "TestLora"


def test_version_name():
    assert _render("{version_name}", {"version_name": "v1"}, "SDXL") == "v1"


def test_multi_segment_template():
    m = {"creator": "alice", "model_name": "Foo"}
    assert _render("{base_model}/{author}/{model_name}", m, "Flux.1 D") == "Flux.1 D/alice/Foo"


def test_unsafe_chars_replaced():
    """Windows 非法字符 <>:"/\\|?* 会被替换为下划线"""
    m = {"model_name": 'bad<>name?'}
    rendered = _render("{model_name}", m, "SDXL")
    assert "<" not in rendered and ">" not in rendered and "?" not in rendered


def test_unknown_base_model_segment_dropped():
    """base_model = 'Unknown' 的段应被跳过"""
    assert _render("{base_model}", {}, "Unknown") == ""


def test_first_tag_from_civitai_tags():
    m = {"civitai_tags": ["style", "anime"]}
    assert _render("{first_tag}", m, "SDXL") == "style"


def test_first_tag_fallback_to_notag():
    assert _render("{first_tag}", {}, "SDXL") == "no-tag"


def test_source_placeholder():
    assert _render("{source}", {"source": "civitai"}, "SDXL") == "civitai"
    assert _render("{source}", {}, "SDXL") == "local"


def test_missing_placeholders():
    """_missing_placeholders 识别确实缺数据的占位符"""
    from manager.manager_organize import _OrganizeMixin

    class _Stub(_OrganizeMixin):
        config = None  # 不会用到

    s = _Stub()
    # 模板要 creator 但 model 里没有
    missing = s._missing_placeholders("{creator}/{model_name}", {"model_name": "X"}, "SDXL")
    assert "creator" in missing
    assert "model_name" not in missing
    assert "base_model" not in missing  # 模板里没写 {base_model} 就不算缺


def test_missing_base_model_when_unknown():
    from manager.manager_organize import _OrganizeMixin

    class _Stub(_OrganizeMixin):
        config = None

    s = _Stub()
    missing = s._missing_placeholders("{base_model}", {}, "Unknown")
    assert missing == ["base_model"]
