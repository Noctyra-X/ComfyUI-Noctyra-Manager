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

"""Generate Noctyra Companion extension icons (16/48/128 PNG).

Drawn with PIL directly to avoid SVG converter dependency.
Run: python gen_icons.py
"""
from PIL import Image, ImageDraw, ImageFilter
import os

OUT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'browser-extension', 'icons'
))


def make_icon(size: int) -> Image.Image:
    # Supersample 3x then downsample for smoother edges
    S = size * 3
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square blue gradient background (approx via layered rects)
    radius = int(S * 0.22)
    # Flat fill first (base color)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=(45, 127, 249, 255))

    # Top-left highlight overlay (subtle)
    highlight = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(highlight)
    hd.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius,
                         fill=(107, 168, 253, 255))
    mask = Image.new('L', (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-S // 3, -S // 3, int(S * 0.8), int(S * 0.8)], fill=110)
    mask = mask.filter(ImageFilter.GaussianBlur(S * 0.15))
    img.paste(highlight, (0, 0), mask)

    # Crescent moon: outer white circle minus inner (masked)
    cx, cy = S // 2, S // 2
    r_out = int(S * 0.25)
    # Inner offset slightly up-right
    ix, iy = cx + int(S * 0.09), cy - int(S * 0.09)
    r_in = int(S * 0.225)

    # Draw moon as two layers: outer white, then inner bg-colored circle
    # Easier: composite with mask
    moon = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    mdraw = ImageDraw.Draw(moon)
    mdraw.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out],
                  fill=(255, 255, 255, 245))
    # Cut by drawing an ellipse with the background (transparent via mask)
    cut = Image.new('L', (S, S), 255)
    cd = ImageDraw.Draw(cut)
    cd.ellipse([ix - r_in, iy - r_in, ix + r_in, iy + r_in], fill=0)
    moon.putalpha(Image.eval(cut, lambda p: p).resize((S, S)))
    # Apply moon alpha AND restrict to existing moon alpha
    r, g, b, a = moon.split()
    # Re-apply the outer circle mask combined with cut
    outer_mask = Image.new('L', (S, S), 0)
    od = ImageDraw.Draw(outer_mask)
    od.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=245)
    # final alpha = outer_mask AND cut
    final_a = Image.new('L', (S, S))
    for y in range(S):
        pass
    final_a = Image.eval(outer_mask, lambda v: v)
    # Multiply masks pixel-wise by compositing
    combined = Image.new('L', (S, S), 0)
    combined_pixels = []
    om = outer_mask.load()
    cm = cut.load()
    for y in range(S):
        for x in range(S):
            combined.putpixel((x, y), min(om[x, y], cm[x, y]))
    moon.putalpha(combined)
    img = Image.alpha_composite(img, moon)

    # Lyra constellation — three stars with thin lines
    d2 = ImageDraw.Draw(img)
    pts = [(int(S * 0.14), int(S * 0.16)),
           (int(S * 0.23), int(S * 0.24)),
           (int(S * 0.16), int(S * 0.33))]
    # Lines
    for i in range(len(pts) - 1):
        d2.line([pts[i], pts[i + 1]], fill=(255, 255, 255, 130), width=max(1, S // 80))
    # Dots
    for i, (x, y) in enumerate(pts):
        r = max(1, int(S * (0.022 - i * 0.004)))
        d2.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 255))

    # Corner stars
    d2.ellipse([int(S * 0.78) - 3, int(S * 0.13) - 3, int(S * 0.78) + 3, int(S * 0.13) + 3],
               fill=(255, 255, 255, 220))
    d2.ellipse([int(S * 0.2) - 2, int(S * 0.77) - 2, int(S * 0.2) + 2, int(S * 0.77) + 2],
               fill=(255, 255, 255, 190))

    return img.resize((size, size), Image.LANCZOS)


for sz in (16, 48, 128):
    icon = make_icon(sz)
    icon.save(os.path.join(OUT_DIR, f'icon-{sz}.png'))
    print(f'wrote icon-{sz}.png')
