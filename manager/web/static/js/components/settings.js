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
 * 设置弹窗 — 分栏导航 + 自动保存（orchestrator）
 *
 * 按 Section 拆分到 settings-*.js：
 *   - settings-helpers.js     : 共享 ctx / renderToggle / bindToggle / bindSelect / saveSetting
 *   - settings-general.js     : API 密钥 / 扫描 / 缓存 / 导入导出 / 代理
 *   - settings-interface.js   : 主题 / NSFW / 视频 / 布局
 *   - settings-directories.js : 模型目录 / 默认下载目录 / 自动整理路径模板 / 基础模型映射
 *   - settings-about.js       : 版本 / 检查更新
 */
import * as api from '../api.js';
import { showToast } from './toast.js';
import { ctx } from './settings-helpers.js';
import { renderGeneralSection,     bindGeneralEvents     } from './settings-general.js';
import { renderInterfaceSection,   bindInterfaceEvents   } from './settings-interface.js';
import { renderDirectoriesSection, bindDirectoriesEvents } from './settings-directories.js';
import { renderGallerySection,     bindGalleryEvents     } from './settings-gallery.js';
import { renderAboutSection,       bindAboutEvents, onAboutTabActivated } from './settings-about.js';

let overlay = null;
let activeSection = 'general';

export function initSettings() {
    overlay = document.getElementById('settings-overlay');
    if (!overlay) return;

    // 主 / 工作流两页都可用同一个 ID 触发；工作流页可用 data-section 预选 Tab
    document.querySelectorAll('#btn-settings, [data-open-settings]').forEach(btn => {
        btn.addEventListener('click', () => {
            const section = btn.dataset.section;
            openSettings(section);
        });
    });

    overlay.addEventListener('click', e => {
        if (e.target === overlay) closeSettings();
    });

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && overlay.classList.contains('show')) closeSettings();
    });

    // 预缓存进度 — 即使设置弹窗被关闭也要正确处理完成/失败
    api.onWsEvent('prewarm_progress', (msg) => {
        const btn = document.getElementById('set-prewarm-previews');
        if (msg.stage === 'complete') {
            if (btn) { btn.disabled = false; btn.textContent = '开始预缓存'; }
            const s = msg.stats || {};
            const toastType = s.failed > 0 ? 'warning' : 'success';
            showToast(
                `预缓存完成：共 ${s.total || 0} 个，命中 ${s.cached || 0}，新下载 ${s.downloaded || 0}，失败 ${s.failed || 0}`,
                toastType,
                6000,
            );
        } else if (msg.stage === 'error') {
            if (btn) { btn.disabled = false; btn.textContent = '开始预缓存'; }
            showToast('预缓存出错: ' + (msg.error || ''), 'error');
        } else if (btn && msg.total > 0) {
            btn.disabled = true;
            btn.textContent = `预缓存 ${msg.current}/${msg.total}`;
        }
    });
}

export async function openSettings(initialSection) {
    if (!overlay) return;

    const res = await api.getSettings();
    if (!res.success) {
        showToast('获取设置失败', 'error');
        return;
    }
    ctx.settings = res.settings;

    if (initialSection) activeSection = initialSection;

    const content = overlay.querySelector('.settings-content');
    content.innerHTML = `
        <div class="settings-header">
            <h2>设置</h2>
            <button class="modal-close" id="settings-close-btn">&times;</button>
        </div>
        <div class="settings-body">
            <nav class="settings-nav">
                <button class="settings-nav-item" data-section="general">常规</button>
                <button class="settings-nav-item" data-section="interface">界面</button>
                <button class="settings-nav-item" data-section="directories">目录</button>
                <button class="settings-nav-item" data-section="gallery">图库</button>
                <button class="settings-nav-item" data-section="about">关于</button>
            </nav>
            <div class="settings-main">
                ${renderGeneralSection()}
                ${renderInterfaceSection()}
                ${renderDirectoriesSection()}
                ${renderGallerySection()}
                ${renderAboutSection()}
            </div>
        </div>
    `;

    // 导航切换
    content.querySelectorAll('.settings-nav-item').forEach(btn => {
        btn.addEventListener('click', () => {
            content.querySelectorAll('.settings-nav-item').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            content.querySelectorAll('.settings-section-panel').forEach(s => s.classList.remove('active'));
            const target = content.querySelector(`#section-${btn.dataset.section}`);
            if (target) target.classList.add('active');
            activeSection = btn.dataset.section;
            // 切到"关于"Tab 时懒触发更新检查（带 24h 缓存）
            if (activeSection === 'about') {
                onAboutTabActivated(content);
            }
        });
    });

    // 显示当前活跃的 section
    const activeNav = content.querySelector(`.settings-nav-item[data-section="${activeSection}"]`);
    if (activeNav) activeNav.click();

    // 关闭
    content.querySelector('#settings-close-btn').addEventListener('click', closeSettings);

    // 绑定所有事件（general 需要 closeSettings 以便"重建缓存"后关弹窗）
    bindGeneralEvents(content, closeSettings);
    bindInterfaceEvents(content);
    bindDirectoriesEvents(content);
    bindGalleryEvents(content);
    bindAboutEvents(content);

    overlay.classList.add('show');
}

function closeSettings() {
    if (overlay) overlay.classList.remove('show');
}
