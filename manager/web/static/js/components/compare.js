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
 * 模型对比弹窗 — 两个模型并排比较
 */
import * as api from '../api.js';
import { escapeHtml as esc, formatSize } from '../utils.js';
const { previewUrl } = api;

export async function openCompare(filePath1, filePath2) {
    const [r1, r2] = await Promise.all([
        api.fetchModelDetail(filePath1),
        api.fetchModelDetail(filePath2),
    ]);
    if (!r1.success || !r2.success) return;
    showCompareModal(r1.model, r2.model);
}

function showCompareModal(a, b) {
    let overlay = document.getElementById('compare-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'compare-overlay';
        overlay.className = 'modal-overlay';
        document.body.appendChild(overlay);
    }

    overlay.innerHTML = `
        <div class="modal-content compare-modal">
            <div class="modal-header">
                <h2>模型对比</h2>
                <button class="modal-close" id="cmp-close">&times;</button>
            </div>
            <div class="compare-body">
                <div class="compare-col">${renderSide(a)}</div>
                <div class="compare-col">${renderSide(b)}</div>
            </div>
        </div>
    `;

    overlay.querySelector('#cmp-close').addEventListener('click', () => overlay.classList.remove('show'));
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('show'); });
    overlay.classList.add('show');
}

function renderSide(m) {
    const name = m.model_name || m.file_name;
    const imgUrl = previewUrl(m.preview_url);
    const source = m.source === 'civitai' ? 'CivitAI' : m.source === 'huggingface' ? 'HuggingFace' : '未匹配';
    const words = Array.isArray(m.trained_words) ? m.trained_words : [];
    const wordsText = words.length > 0 ? words.slice(0, 15).join(', ') : '—';

    return `
        <div class="cmp-preview">
            <img src="${esc(imgUrl)}" alt="${esc(name)}" onerror="this.src='/noctyra_static/images/placeholder.svg'">
        </div>
        <div class="cmp-name">${esc(name)}</div>
        ${m.version_name ? `<div class="cmp-version">${esc(m.version_name)}</div>` : ''}
        <table class="cmp-table">
            <tr><td>文件名</td><td>${esc(m.file_name)}</td></tr>
            <tr><td>大小</td><td>${formatSize(m.file_size)}</td></tr>
            <tr><td>Base Model</td><td>${esc(m.base_model || 'Unknown')}</td></tr>
            <tr><td>类型</td><td>${esc(m.civitai_model_type || m.model_type || '—')}</td></tr>
            <tr><td>来源</td><td>${source}</td></tr>
            <tr><td>作者</td><td>${esc(m.creator || m.hf_author || '—')}</td></tr>
            ${m.downloads ? `<tr><td>下载量</td><td>${formatNum(m.downloads)}</td></tr>` : ''}
            ${m.thumbs_up ? `<tr><td>点赞</td><td>${formatNum(m.thumbs_up)}</td></tr>` : ''}
            ${m.rating ? `<tr><td>评分</td><td>${m.rating.toFixed(2)} (${formatNum(m.rating_count)})</td></tr>` : ''}
            ${m.hf_likes ? `<tr><td>HF Likes</td><td>${formatNum(m.hf_likes)}</td></tr>` : ''}
            ${m.usage_count ? `<tr><td>使用次数</td><td>${m.usage_count}</td></tr>` : ''}
            <tr><td>触发词</td><td class="cmp-words">${esc(wordsText)}</td></tr>
            ${m.published_at ? `<tr><td>发布日期</td><td>${m.published_at.substring(0, 10)}</td></tr>` : ''}
            <tr><td>SHA256</td><td class="cmp-hash">${esc((m.sha256 || '').substring(0, 16))}...</td></tr>
        </table>
    `;
}


function formatNum(n) {
    if (!n) return '0';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

