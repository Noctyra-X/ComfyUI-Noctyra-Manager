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
 * 顶栏搜索
 */
import { state } from '../state.js';
import { loadModels } from './card-grid.js';

let searchInput = null;
let searchTimer = null;

export function initHeader() {
    searchInput = document.getElementById('search-input');
    if (!searchInput) return;

    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            state.currentSearch = searchInput.value.trim();
            loadModels();
        }, 300);
    });

    // 键盘快捷键：
    //   Ctrl+F / Cmd+F   → 聚焦搜索框（已有）
    //   /                → 聚焦搜索框（当前不在输入元素里）
    //   J / K            → 滚动卡片列表（一行一行，方便快速浏览）
    document.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
            e.preventDefault();
            searchInput.focus();
            searchInput.select();
            return;
        }

        // 以下快捷键只在用户没聚焦在输入框/编辑区时才生效
        const ae = document.activeElement;
        const inInput = ae && (
            ae.tagName === 'INPUT' ||
            ae.tagName === 'TEXTAREA' ||
            ae.tagName === 'SELECT' ||
            ae.isContentEditable
        );
        if (inInput) return;
        // 有 modifier 时不拦截（避免和浏览器快捷键冲突）
        if (e.ctrlKey || e.metaKey || e.altKey) return;

        if (e.key === '/') {
            e.preventDefault();
            searchInput.focus();
            searchInput.select();
            return;
        }

        // J/K 卡片列表滚动（Reddit 式），一行一屏的 40%
        const grid = document.getElementById('card-grid');
        if (!grid) return;
        const scrollHost = grid.closest('.content-area, .main-content, body') || window;
        const stepSign = e.key === 'j' || e.key === 'J' ? 1 : (e.key === 'k' || e.key === 'K' ? -1 : 0);
        if (stepSign !== 0) {
            e.preventDefault();
            const step = Math.max(200, Math.round(window.innerHeight * 0.4)) * stepSign;
            if (scrollHost === window) {
                window.scrollBy({ top: step, behavior: 'smooth' });
            } else {
                scrollHost.scrollBy({ top: step, behavior: 'smooth' });
            }
        }
    });
}
