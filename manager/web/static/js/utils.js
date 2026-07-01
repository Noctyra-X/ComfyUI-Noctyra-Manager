// ComfyUI-Noctyra-Manager
// Copyright (C) 2026 Noctyra
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

/**
 * 共享工具函数
 * HTML / 属性转义，统一用 DOM textContent 实现，避免重复定义。
 */

export function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

export function escapeAttr(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/** URL 协议安全校验：拦 javascript:/vbscript:/data:（data:image 除外）。
 *  把不可信的 source_url 放进 href/src 前先过一遍，防点击执行脚本。 */
export function isSafeUrl(url) {
    if (!url) return false;
    const t = String(url).trim().toLowerCase();
    if (/^(javascript|vbscript):/i.test(t)) return false;
    if (/^data:/i.test(t) && !/^data:image\//i.test(t)) return false;
    return true;
}

/** 字节数人类可读："1.5 GB" / "23.4 MB"；0/NaN 返回 "0 B" */
export function formatSize(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, size = bytes;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return `${size.toFixed(1)} ${units[i]}`;
}

/** 大数人类可读："1.2M" / "3.4K"；空值返回空串 */
export function formatNumber(n) {
    if (!n) return '';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

/** 全局弹窗 focus trap：弹窗打开时把 Tab 限制在最上层 overlay 内，键盘用户不会跑到背景。
 *  覆盖持久弹窗(.modal-overlay/.settings-overlay 用 .show)与动态创建弹窗(存在即可见)。幂等。 */
let _focusTrapInstalled = false;
export function initFocusTrap() {
    if (_focusTrapInstalled) return;
    _focusTrapInstalled = true;
    const SEL = '.modal-overlay.show, .settings-overlay.show, .wf-detail-overlay.show,' +
        '.delete-confirm-overlay, .st-overlay, .tw-overlay, .duplicates-overlay,' +
        '.organize-overlay, .compare-overlay, .img-zoom-overlay, .upd-overlay';
    const isVisible = (el) => el.getClientRects().length > 0;
    function topOverlay() {
        let top = null;
        document.querySelectorAll(SEL).forEach(el => { if (isVisible(el)) top = el; });
        return top;
    }
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Tab') return;
        const overlay = topOverlay();
        if (!overlay) return;
        const list = Array.from(overlay.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]),' +
            ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter(isVisible);
        if (!list.length) return;
        const first = list[0], last = list[list.length - 1];
        if (!overlay.contains(document.activeElement)) {
            e.preventDefault(); first.focus();
        } else if (e.shiftKey && document.activeElement === first) {
            e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault(); first.focus();
        }
    });
}

/**
 * CivitAI 链接的 NSFW 感知重写：若目标是 NSFW 且 URL 指向 civitai.com，
 * 改写为 civitai.red（NSFW 前门）避免用户从 .com 再手动跳一次 .red。
 * 非 CivitAI 链接 / 非 NSFW / 已是 .red 的不动。
 */
export function resolveSourceUrl(url, isNsfw) {
    if (!url || !isNsfw) return url || '';
    // 仅替换 host 部分（避免误伤 path 或 query 里恰好出现 civitai.com 的场景）
    return url.replace(/^(https?:\/\/)(?:www\.)?civitai\.com\b/i, '$1civitai.red');
}
