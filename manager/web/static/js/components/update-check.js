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
 * 模型更新检查 — 检测 CivitAI 模型是否有新版本
 */
import * as api from '../api.js';
import { showToast } from './toast.js';
import { loadModels } from './card-grid.js';
import { escapeAttr as esc, formatSize, isSafeUrl } from '../utils.js';

let overlay = null;
let lastUpdates = [];

export function initUpdateCheck() {
    const el = document.createElement('div');
    el.className = 'settings-overlay';
    el.id = 'update-overlay';
    el.innerHTML = '<div class="settings-content"></div>';
    document.body.appendChild(el);
    overlay = el;

    el.addEventListener('click', e => {
        if (e.target === el) closeUpdate();
    });

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && overlay.classList.contains('show')) closeUpdate();
    });

    const btn = document.getElementById('btn-check-updates');
    if (btn) btn.addEventListener('click', openUpdateCheck);

    // 启动即按持久化的"可更新"数显示徽章（无需手动点检查 = "有更新就提醒"），
    // 并监听后台自动检查完成的广播，检查跑完即时刷新徽章
    refreshBadgeFromStatus();
    api.onWsEvent('updates_checked', msg => updateBadge((msg && msg.updatable) || 0));
}

async function refreshBadgeFromStatus() {
    try {
        const res = await api.fetchStatus();
        if (res && res.success) updateBadge(res.updatable || 0);
    } catch { /* 状态接口偶发失败，忽略 */ }
}

async function openUpdateCheck() {
    if (!overlay) return;
    const content = overlay.querySelector('.settings-content');
    content.innerHTML = `
        <div class="settings-header">
            <h2>检查模型更新</h2>
            <button class="modal-close" id="upd-close">&times;</button>
        </div>
        <div class="upd-body">
            <div class="upd-status" id="upd-status">
                <div class="upd-loading">正在检查模型更新...</div>
            </div>
        </div>
    `;

    content.querySelector('#upd-close').addEventListener('click', closeUpdate);
    overlay.classList.add('show');

    await doCheck(content);
}

async function doCheck(content) {
    const statusEl = content.querySelector('#upd-status');
    try {
        const res = await api.checkModelUpdates();
        if (!res.success) {
            statusEl.innerHTML = `<div class="upd-error">检查失败: ${esc(res.error || '')}</div>`;
            return;
        }

        lastUpdates = res.updates || [];

        if (lastUpdates.length === 0) {
            statusEl.innerHTML = `
                <div class="upd-empty">
                    <div class="upd-empty-icon">✓</div>
                    <div>所有模型已是最新版本</div>
                </div>`;
            updateBadge(0);   // 清掉旧徽章：本次检查过的都已是最新
            loadModels();
            return;
        }

        statusEl.innerHTML = `
            <div class="upd-summary">${lastUpdates.length} 个模型有新版本</div>
            <div class="upd-list">
                ${lastUpdates.map(u => {
                    const isHF = u.source === 'huggingface';
                    const versionLine = isHF
                        ? `HF 更新: ${esc((u.current_modified || '').slice(0, 10))} → <strong>${esc((u.latest_modified || '').slice(0, 10))}</strong>`
                        : `${esc(u.current_version_name)} → <strong>${esc(u.latest_version_name)}</strong>${u.latest_base_model ? ` · ${esc(u.latest_base_model)}` : ''}${u.latest_file_size ? ` · ${formatSize(u.latest_file_size)}` : ''}`;
                    return `
                    <div class="upd-item">
                        <div class="upd-item-info">
                            <div class="upd-item-name">
                                <span class="upd-item-source upd-item-source-${isHF ? 'hf' : 'civitai'}">${isHF ? 'HF' : 'CivitAI'}</span>
                                ${esc(u.model_name || u.file_name)}
                            </div>
                            <div class="upd-item-version">${versionLine}</div>
                        </div>
                        <div class="upd-item-actions">
                            ${isSafeUrl(u.source_url) ? `<a href="${esc(u.source_url)}" target="_blank" rel="noopener" class="btn btn-sm">查看</a>` : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>
        `;

        // 更新 header 上的徽章，刷新列表使筛选生效
        updateBadge(lastUpdates.length);
        loadModels();

    } catch (e) {
        statusEl.innerHTML = `<div class="upd-error">网络错误: ${esc(e.message)}</div>`;
    }
}

function updateBadge(count) {
    const btn = document.getElementById('btn-check-updates');
    if (!btn) return;
    let badge = btn.querySelector('.update-count-badge');
    if (count > 0) {
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'update-count-badge';
            btn.appendChild(badge);
        }
        badge.textContent = count;
        badge.style.display = '';
    } else if (badge) {
        badge.style.display = 'none';
    }
}

function closeUpdate() {
    if (overlay) overlay.classList.remove('show');
}


