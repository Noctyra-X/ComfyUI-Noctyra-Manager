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
 * Trigger Words 聚合弹窗 — 跨全库汇总 trained_words，点词跳到主列表搜索。
 */
import { state } from '../state.js';
import { showToast } from './toast.js';
import { loadModels } from './card-grid.js';
import { escapeHtml, escapeAttr } from '../utils.js';

let overlay = null;
let escHandler = null;

function closeTriggerWords() {
    // .delete-confirm-overlay 是"创建即显示"的 flex 蒙层（modal.css 里 display:flex
    // 常驻，没有 .show 显隐），classList.remove('show') 不起作用。直接从 DOM 摘除。
    if (overlay) {
        overlay.remove();
        overlay = null;
    }
    if (escHandler) {
        document.removeEventListener('keydown', escHandler);
        escHandler = null;
    }
}

export function initTriggerWords() {
    const btn = document.getElementById('btn-trigger-words');
    if (btn) btn.addEventListener('click', openTriggerWords);
}

async function openTriggerWords() {
    if (overlay) return;  // 已打开，忽略重复点击
    overlay = document.createElement('div');
    overlay.className = 'tw-overlay delete-confirm-overlay';  // 复用遮罩样式
    overlay.innerHTML = `
        <div class="tw-modal">
            <div class="tw-header">
                <h3>Trigger Words · 触发词聚合</h3>
                <button class="tw-close" type="button">&times;</button>
            </div>
            <div class="tw-toolbar">
                <input type="text" class="tw-search" placeholder="过滤... (在聚合结果里本地过滤，不重新查后端)">
                <span class="tw-meta"></span>
            </div>
            <div class="tw-body" tabindex="0"><div class="tw-empty">加载中...</div></div>
            <div class="tw-footer">
                <span class="tw-hint">点击任一词 → 主列表按该词搜索</span>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    overlay.querySelector('.tw-close').addEventListener('click', closeTriggerWords);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeTriggerWords();
    });
    escHandler = (e) => { if (e.key === 'Escape') closeTriggerWords(); };
    document.addEventListener('keydown', escHandler);

    const body = overlay.querySelector('.tw-body');
    const searchInput = overlay.querySelector('.tw-search');
    const metaEl = overlay.querySelector('.tw-meta');

    let allWords = [];

    try {
        const resp = await fetch('/api/noctyra/trigger-words?limit=2000');
        const res = await resp.json();
        if (!res.success) {
            body.innerHTML = `<div class="tw-empty">加载失败: ${escapeHtml(res.error || '')}</div>`;
            return;
        }
        allWords = res.words || [];
    } catch (e) {
        body.innerHTML = `<div class="tw-empty">网络错误: ${escapeHtml(e.message || '')}</div>`;
        return;
    }

    if (allWords.length === 0) {
        body.innerHTML = `<div class="tw-empty">库里还没有任何 trigger word（先扫描/匹配 LoRA）</div>`;
        return;
    }

    const render = (filter = '') => {
        const q = filter.trim().toLowerCase();
        const filtered = q ? allWords.filter(w => w.word.toLowerCase().includes(q)) : allWords;
        metaEl.textContent = q
            ? `${filtered.length} / ${allWords.length} 个词`
            : `${allWords.length} 个词`;
        if (filtered.length === 0) {
            body.innerHTML = `<div class="tw-empty">没有匹配的词</div>`;
            return;
        }
        // 排序已在后端做好（按 count 降序）
        body.innerHTML = filtered.map(w => `
            <div class="tw-chip" data-word="${escapeAttr(w.word)}" title="在 ${w.count} 个模型里出现（${w.model_types.join('/')}）">
                <span class="tw-chip-word">${escapeHtml(w.word)}</span>
                <span class="tw-chip-count">${w.count}</span>
            </div>
        `).join('');

        body.querySelectorAll('.tw-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const word = chip.dataset.word;
                // 把词填进主搜索框并触发加载
                state.currentSearch = word;
                state.page = 1;
                const searchBox = document.getElementById('search-input');
                if (searchBox) searchBox.value = word;
                closeTriggerWords();
                loadModels();
                showToast(`按 "${word}" 搜索`, 'info');
            });
        });
    };
    render();

    let debounceTimer = null;
    searchInput.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => render(searchInput.value), 120);
    });
    searchInput.focus();
}
