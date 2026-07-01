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
 * Settings - 界面 Section: 主题、NSFW 过滤、视频、布局
 */
import { state } from '../state.js';
import { ctx, renderToggle, bindToggle, bindSelect } from './settings-helpers.js';

export function renderInterfaceSection() {
    const s = ctx.settings;
    return `
    <div class="settings-section-panel" id="section-interface">
        <div class="settings-subsection">
            <h3>主题</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>颜色主题</span>
                    <span class="setting-hint">切换深色/浅色外观</span>
                </div>
                <div class="setting-control">
                    <select id="set-theme" class="settings-select">
                        <option value="dark"${s.theme === 'dark' || !s.theme ? ' selected' : ''}>深色</option>
                        <option value="light"${s.theme === 'light' ? ' selected' : ''}>浅色</option>
                        <option value="auto"${s.theme === 'auto' ? ' selected' : ''}>跟随系统</option>
                    </select>
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>内容过滤</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>模糊 NSFW 内容</span>
                    <span class="setting-hint">对达到阈值的预览图进行模糊处理，鼠标悬停解除</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-blur-nsfw', s.blur_nsfw)}
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>仅显示 SFW 结果</span>
                    <span class="setting-hint">从列表和搜索中隐藏 CivitAI 标记为 NSFW 的模型</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-show-only-sfw', s.show_only_sfw)}
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>成人内容模糊阈值</span>
                    <span class="setting-hint">根据 CivitAI 分级模糊预览图：越低越严格</span>
                </div>
                <div class="setting-control">
                    <select id="set-nsfw-threshold" class="settings-select">
                        <option value="2"${String(s.nsfw_blur_threshold) === '2' ? ' selected' : ''}>PG13 及以上</option>
                        <option value="4"${(String(s.nsfw_blur_threshold) === '4' || s.nsfw_blur_threshold == null) ? ' selected' : ''}>R 及以上（默认）</option>
                        <option value="8"${String(s.nsfw_blur_threshold) === '8' ? ' selected' : ''}>X 及以上</option>
                        <option value="16"${String(s.nsfw_blur_threshold) === '16' ? ' selected' : ''}>仅 XXX</option>
                    </select>
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>视频设置</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>悬停时自动播放视频</span>
                    <span class="setting-hint">鼠标悬停在视频预览卡片上时自动开始播放，移开后暂停</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-autoplay-video', s.autoplay_video_on_hover !== false)}
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>画布</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>模型选择器悬浮按钮</span>
                    <span class="setting-hint">在 ComfyUI 节点图右下角显示 Noctyra 悬浮按钮，选中模型节点后点它可视化挑模型（改后刷新 ComfyUI 页生效）</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-canvas-picker', s.canvas_picker_enabled !== false)}
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>布局设置</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>显示密度</span>
                    <span class="setting-hint">卡片网格的显示密度</span>
                </div>
                <div class="setting-control">
                    <select id="set-display-density" class="settings-select">
                        <option value="default"${s.display_density === 'default' ? ' selected' : ''}>默认</option>
                        <option value="compact"${s.display_density === 'compact' ? ' selected' : ''}>紧凑</option>
                    </select>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>预览图比例</span>
                    <span class="setting-hint">卡片上方预览图的宽高比（不含底部信息条）</span>
                </div>
                <div class="setting-control">
                    <select id="set-card-aspect" class="settings-select">
                        <option value="3/4"${(s.card_aspect || '3/4') === '3/4' ? ' selected' : ''}>3:4 纵向</option>
                        <option value="1/1"${s.card_aspect === '1/1' ? ' selected' : ''}>1:1 方形</option>
                        <option value="4/3"${s.card_aspect === '4/3' ? ' selected' : ''}>4:3 横向</option>
                        <option value="2/3"${s.card_aspect === '2/3' ? ' selected' : ''}>2:3 高窄</option>
                        <option value="16/9"${s.card_aspect === '16/9' ? ' selected' : ''}>16:9 宽屏</option>
                    </select>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>卡片信息显示</span>
                    <span class="setting-hint">模型卡片上的信息何时显示</span>
                </div>
                <div class="setting-control">
                    <select id="set-card-info" class="settings-select">
                        <option value="always"${s.card_info_display === 'always' ? ' selected' : ''}>始终显示</option>
                        <option value="hover"${s.card_info_display === 'hover' ? ' selected' : ''}>悬浮显示</option>
                    </select>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>模型名称显示</span>
                    <span class="setting-hint">卡片上显示的名称来源</span>
                </div>
                <div class="setting-control">
                    <select id="set-name-display" class="settings-select">
                        <option value="model_name"${s.model_name_display === 'model_name' ? ' selected' : ''}>模型名</option>
                        <option value="file_name"${s.model_name_display === 'file_name' ? ' selected' : ''}>文件名</option>
                    </select>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>显示侧边栏</span>
                    <span class="setting-hint">是否显示文件夹侧边栏</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-show-sidebar', s.show_sidebar)}
                </div>
            </div>
        </div>
    </div>`;
}

// 提取给主 app.js 和 settings-interface.js 共用的主题应用逻辑
export function applyThemeMode(mode) {
    let effective = mode;
    if (mode === 'auto') {
        effective = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    // 切换瞬间禁用所有过渡：否则部分元素(带 transition)渐变、部分瞬变，观感不一致
    const root = document.documentElement;
    root.classList.add('theme-switching');
    if (effective === 'light') {
        root.dataset.theme = 'light';
    } else {
        delete root.dataset.theme;
    }
    // 颜色变更应用后再下一帧恢复过渡（双 rAF 确保本次切换是瞬时的）
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('theme-switching')));
}

export function bindInterfaceEvents(content) {
    bindSelect(content, 'set-theme', 'theme', (val) => {
        applyThemeMode(val);
    });

    bindToggle(content, 'set-blur-nsfw', 'blur_nsfw', () => {
        window.dispatchEvent(new CustomEvent('noctyra-refresh-cards'));
    });
    bindToggle(content, 'set-show-only-sfw', 'show_only_sfw', () => {
        window.dispatchEvent(new CustomEvent('noctyra-refresh-list'));
    });
    bindSelect(content, 'set-nsfw-threshold', 'nsfw_blur_threshold', (val) => {
        state.settings.nsfw_blur_threshold = parseInt(val);
        window.dispatchEvent(new CustomEvent('noctyra-refresh-cards'));
    });
    bindToggle(content, 'set-autoplay-video', 'autoplay_video_on_hover', () => {
        window.dispatchEvent(new CustomEvent('noctyra-refresh-cards'));
    });
    // 画布选择器悬浮按钮开关（在 ComfyUI 画布页刷新后生效）
    bindToggle(content, 'set-canvas-picker', 'canvas_picker_enabled');
    // 工作流页没有 .sidebar / #card-grid，跳过 live-apply 即可（设置仍已保存）
    bindToggle(content, 'set-show-sidebar', 'show_sidebar', (val) => {
        const sidebar = document.querySelector('.sidebar');
        if (sidebar) sidebar.style.display = val ? '' : 'none';
    });

    bindSelect(content, 'set-display-density', 'display_density', (val) => {
        const grid = document.getElementById('card-grid');
        if (grid) grid.dataset.density = val;
        window.dispatchEvent(new CustomEvent('noctyra-refresh-cards'));  // 触发虚拟网格按新密度重排
    });
    bindSelect(content, 'set-card-aspect', 'card_aspect', (val) => {
        const grid = document.getElementById('card-grid');
        if (grid) grid.style.setProperty('--card-aspect', val.replace('/', ' / '));
        window.dispatchEvent(new CustomEvent('noctyra-refresh-cards'));  // 虚拟网格按新比例重算行高
    });
    bindSelect(content, 'set-card-info', 'card_info_display', (val) => {
        const grid = document.getElementById('card-grid');
        if (grid) grid.dataset.cardInfo = val;
        window.dispatchEvent(new CustomEvent('noctyra-refresh-cards'));  // 信息模式变了，虚拟网格重排
    });
    bindSelect(content, 'set-name-display', 'model_name_display');
}
