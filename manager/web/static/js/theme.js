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
 * 顶栏明暗主题切换（工作流页 / 统计页共用，和模型页同一套 localStorage 'noctyra_theme'）。
 * dark = 不设 dataset.theme；light = dataset.theme='light'（与各页首屏内联脚本一致）。
 */
const ICON_MOON = '<svg class="theme-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
const ICON_SUN = '<svg class="theme-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>';

function applyTheme(mode) {
    if (mode === 'light') document.documentElement.dataset.theme = 'light';
    else delete document.documentElement.dataset.theme;   // dark = 默认
}

export function initThemeToggle(btnId = 'btn-theme-toggle') {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    const setIcon = () => {
        const isLight = document.documentElement.dataset.theme === 'light';
        btn.innerHTML = isLight ? ICON_SUN : ICON_MOON;
    };
    setIcon();
    btn.addEventListener('click', async () => {
        const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
        applyTheme(next);
        try { localStorage.setItem('noctyra_theme', next); } catch (e) {}
        setIcon();
        // 同步后端（设置页的主题项 + 跨设备）；失败不影响本地 localStorage 已生效
        try {
            await fetch('/api/noctyra/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: next }),
            });
        } catch (e) { /* 忽略 */ }
    });
    // 跨标签页同步
    window.addEventListener('storage', (e) => {
        if (e.key === 'noctyra_theme') { applyTheme(e.newValue || 'dark'); setIcon(); }
    });
}
