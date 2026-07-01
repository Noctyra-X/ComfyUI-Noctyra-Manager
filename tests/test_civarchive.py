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
civarchive transform 测试：纯函数，不发 HTTP 请求。
"""

from manager.civarchive import _transform_to_civitai_info


def test_transforms_full_payload():
    payload = {
        "data": {
            "id": 12345,
            "name": "Test Model",
            "type": "LORA",
            "creator": {"username": "alice", "image": "https://x/alice.png"},
            "tags": ["style", "anime"],
            "version": {
                "id": 67890,
                "name": "v1.0",
                "baseModel": "Flux.1 D",
                "publishedAt": "2026-01-01T00:00:00Z",
                "trainedWords": ["trigger"],
                "images": [
                    {"url": "https://x/1.jpg", "nsfwLevel": 0, "width": 512, "height": 512}
                ],
                "files": [
                    {"downloadUrl": "https://x/model.safetensors"}
                ],
            },
        }
    }

    info = _transform_to_civitai_info(payload)
    assert info is not None
    assert info["source"] == "civarchive"
    assert info["model_name"] == "Test Model"
    assert info["version_name"] == "v1.0"
    assert info["base_model"] == "Flux.1 D"
    assert info["civitai_model_id"] == 12345
    assert info["civitai_version_id"] == 67890
    assert info["creator"] == "alice"
    assert info["preview_url"] == "https://x/1.jpg"
    assert info["download_url"] == "https://x/model.safetensors"
    assert info["trained_words"] == ["trigger"]


def test_preview_images_keep_type_and_prefer_image_main():
    """CivArchive 的 image 带 type(image/video)必须保留；主预览优先选静态图，
    避免视频被当图片渲染成"no image"占位（LTX/Wan 视频模型的历史 bug）。"""
    payload = {
        "data": {
            "id": 1,
            "version": {
                "id": 2,
                "images": [
                    {"url": "https://x/clip.mp4", "type": "video", "nsfwLevel": 8},
                    {"url": "https://x/cover.jpg", "type": "image", "nsfwLevel": 0},
                ],
            },
        }
    }
    info = _transform_to_civitai_info(payload)
    pi = info["preview_images"]
    assert [p["type"] for p in pi] == ["video", "image"]   # type 必须保留
    assert info["preview_url"] == "https://x/cover.jpg"     # 主预览优先静态图
    # NSFW 等级字段名必须是 nsfw_level（下划线），与 database 读取口径一致；
    # 误存驼峰 nsfwLevel 会导致 database 读不到、NSFW 模型不打码
    assert pi[0]["nsfw_level"] == 8 and "nsfwLevel" not in pi[0]


def test_preview_all_videos_fallback_to_first():
    """整组全是视频时，主预览退回第一个视频（而非空）。"""
    payload = {
        "data": {"id": 1, "version": {"id": 2, "images": [
            {"url": "https://x/a.mp4", "type": "video"},
            {"url": "https://x/b.mp4", "type": "video"},
        ]}}
    }
    info = _transform_to_civitai_info(payload)
    assert info["preview_url"] == "https://x/a.mp4"
    assert all(p["type"] == "video" for p in info["preview_images"])


def test_preview_missing_type_defaults_to_image():
    """没有 type 字段的旧式 image 默认按图片处理（不破坏纯图片模型）。"""
    info = _transform_to_civitai_info({
        "data": {"id": 1, "version": {"id": 2, "images": [
            {"url": "https://x/1.jpg", "nsfwLevel": 0},
        ]}}
    })
    assert info["preview_images"][0]["type"] == "image"
    assert info["preview_url"] == "https://x/1.jpg"


def test_rejects_payload_without_ids():
    # 没有任何 id 的 payload 视为无效
    assert _transform_to_civitai_info({"data": {"name": "orphan"}}) is None


def test_empty_payload():
    assert _transform_to_civitai_info({}) is None
    assert _transform_to_civitai_info(None) is None


def test_handles_unwrapped_payload():
    """有些 endpoint 返回时不嵌 'data' 层"""
    info = _transform_to_civitai_info({
        "id": 1,
        "name": "X",
        "version": {"id": 2, "baseModel": "SDXL"},
    })
    assert info is not None
    assert info["civitai_model_id"] == 1
    assert info["civitai_version_id"] == 2


def test_handles_mirror_files():
    """带 mirrors 的文件应取非删除镜像的 URL"""
    payload = {
        "data": {
            "id": 1,
            "version": {
                "id": 2,
                "files": [
                    {
                        "mirrors": [
                            {"url": "https://mirror1/file.safetensors", "deletedAt": None},
                        ]
                    }
                ],
            },
        }
    }
    info = _transform_to_civitai_info(payload)
    assert info["download_url"] == "https://mirror1/file.safetensors"
