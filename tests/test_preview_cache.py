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

"""PreviewCache.get_thumb 列表卡片缩略图生成测试。"""

import os
import pytest


@pytest.fixture
def cache(tmp_path):
    from manager.preview_cache import PreviewCache
    return PreviewCache(str(tmp_path))


def _make_png(path, w, h):
    from PIL import Image
    Image.new("RGB", (w, h), (80, 120, 200)).save(path, "PNG")


class TestThumb:
    def test_generates_480_webp_smaller(self, cache, tmp_path):
        src = str(tmp_path / "big.png")
        _make_png(src, 1200, 1600)
        thumb = cache.get_thumb(src)
        assert thumb and os.path.isfile(thumb)
        from PIL import Image
        with Image.open(thumb) as im:
            assert im.format == "WEBP"
            assert im.size == (480, 640)  # 缩到 480 宽，保持 3:4
        assert os.path.getsize(thumb) < os.path.getsize(src)

    def test_no_upscale_below_480(self, cache, tmp_path):
        src = str(tmp_path / "small.png")
        _make_png(src, 300, 400)
        thumb = cache.get_thumb(src)
        from PIL import Image
        with Image.open(thumb) as im:
            assert im.size == (300, 400)  # 不放大，只重编码为 WebP

    def test_second_call_reuses_cache(self, cache, tmp_path):
        src = str(tmp_path / "a.png")
        _make_png(src, 800, 600)
        t1 = cache.get_thumb(src)
        m1 = os.path.getmtime(t1)
        t2 = cache.get_thumb(src)
        assert t2 == t1 and os.path.getmtime(t2) == m1  # 未重新生成

    def test_video_returns_none(self, cache, tmp_path):
        vid = str(tmp_path / "v.mp4")
        with open(vid, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42")
        assert cache.get_thumb(vid) is None

    def test_missing_file_returns_none(self, cache, tmp_path):
        assert cache.get_thumb(str(tmp_path / "nope.png")) is None


class TestRetryAfter:
    """预缓存 429 退避：Retry-After 头解析。"""

    def test_parse_retry_after(self):
        from manager.preview_cache import PreviewCache as P
        assert P._parse_retry_after("10") == 10
        assert P._parse_retry_after(None) == 10              # 默认
        assert P._parse_retry_after("not-a-number") == 10    # HTTP-date 等非数字 → 默认
        assert P._parse_retry_after("9999") == 120           # 上限夹紧
        assert P._parse_retry_after("0") == 1                # 下限夹紧
