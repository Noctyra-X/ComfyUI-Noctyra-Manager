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
 * Settings - 关于 Section: 版本信息 + 检查更新
 *
 * 更新检查策略：
 *   - 切到"关于"Tab 时懒触发一次（非打开设置就查）
 *   - 24h localStorage 缓存，命中直接渲染，不打 GitHub API
 *   - 手动点"检查更新"按钮 = 强制刷新，绕过缓存
 */
import * as api from '../api.js';
import { escapeHtml as escHtml, escapeAttr as esc } from '../utils.js';

const CACHE_KEY = 'noctyra_update_check_cache';
const CACHE_TTL_MS = 24 * 60 * 60 * 1000;  // 24 小时

let _checkedThisOpen = false;

export function renderAboutSection() {
    return `
    <div class="settings-section-panel" id="section-about">
        <div class="settings-subsection">
            <h3>ComfyUI-Noctyra</h3>
            <div class="about-info">
                <div class="setting-row">
                    <div class="setting-label"><span>当前版本</span></div>
                    <div class="setting-control">
                        <span id="about-current-version">加载中...</span>
                    </div>
                </div>
                <div class="setting-row">
                    <div class="setting-label"><span>最新版本</span></div>
                    <div class="setting-control">
                        <span id="about-latest-version">—</span>
                    </div>
                </div>
                <div class="setting-row">
                    <div class="setting-label"></div>
                    <div class="setting-control">
                        <button class="btn btn-sm" id="about-check-update-btn">检查更新</button>
                    </div>
                </div>
                <div id="about-update-info" class="update-info" style="display:none"></div>
            </div>
        </div>
        <div class="settings-subsection">
            <h3>链接</h3>
            <div class="about-links">
                <a href="https://github.com/Noctyra-X/ComfyUI-Noctyra-Manager" target="_blank" rel="noopener" class="about-link">GitHub</a>
            </div>
        </div>
    </div>`;
}

export function bindAboutEvents(content) {
    // 每次打开设置重置标志，让 Tab 激活时还能触发一次
    _checkedThisOpen = false;

    const checkBtn = content.querySelector('#about-check-update-btn');
    if (checkBtn) {
        // 手动点击 = 强制刷新，绕过缓存
        checkBtn.addEventListener('click', () => doCheckUpdate(content, true));
    }
}

// 供 settings.js 在切到"关于"Tab 时调用（懒触发）
export function onAboutTabActivated(content) {
    if (_checkedThisOpen) return;
    _checkedThisOpen = true;
    doCheckUpdate(content, false);
}

async function doCheckUpdate(content, force) {
    const btn = content.querySelector('#about-check-update-btn');
    const latestEl = content.querySelector('#about-latest-version');

    // 非强制 → 尝试读缓存
    if (!force) {
        const cached = readCache();
        if (cached) {
            renderResult(content, cached);
            return;
        }
    }

    if (btn) { btn.disabled = true; btn.textContent = '检查中...'; }

    try {
        const res = await api.checkUpdate();
        if (!res.success) {
            const currentEl = content.querySelector('#about-current-version');
            if (currentEl) currentEl.textContent = '未知';
            if (latestEl) latestEl.textContent = '检查失败';
            if (btn) { btn.disabled = false; btn.textContent = '重试'; }
            return;
        }
        writeCache(res);
        renderResult(content, res);
    } catch (e) {
        if (latestEl) latestEl.textContent = '网络错误';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '检查更新'; }
    }
}

function renderResult(content, res) {
    const currentEl = content.querySelector('#about-current-version');
    const latestEl = content.querySelector('#about-latest-version');
    const infoEl = content.querySelector('#about-update-info');

    if (currentEl) currentEl.textContent = 'v' + res.current_version;
    if (latestEl) latestEl.textContent = res.latest_version ? 'v' + res.latest_version : '—';

    if (!infoEl) return;
    if (res.has_update) {
        infoEl.style.display = 'block';
        infoEl.innerHTML = `
            <div class="update-available">
                <span class="update-badge">有新版本</span>
                <a href="${esc(res.release_url)}" target="_blank" rel="noopener" class="btn btn-sm btn-primary">查看更新</a>
            </div>
            ${res.release_notes ? `<div class="update-notes">${escHtml(res.release_notes).substring(0, 500)}</div>` : ''}
        `;
    } else {
        infoEl.style.display = 'block';
        infoEl.innerHTML = '<span class="update-current">已是最新版本</span>';
    }
}

function readCache() {
    try {
        const raw = localStorage.getItem(CACHE_KEY);
        if (!raw) return null;
        const entry = JSON.parse(raw);
        if (!entry || !entry.ts || !entry.data) return null;
        if (Date.now() - entry.ts > CACHE_TTL_MS) return null;
        return entry.data;
    } catch (e) {
        return null;
    }
}

function writeCache(data) {
    try {
        localStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), data }));
    } catch (e) {
        // 配额满 / 禁用 localStorage — 静默忽略
    }
}
