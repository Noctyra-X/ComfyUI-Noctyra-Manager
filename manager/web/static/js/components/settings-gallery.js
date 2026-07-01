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
 * Settings - 图库 Section: 工作流图库的每页数量 / 缩略图大小 / 导入区折叠 / 存储目录
 */
import { ctx, renderToggle, bindToggle, bindSelect, saveSetting } from './settings-helpers.js';
import { escapeAttr } from '../utils.js';
import * as api from '../api.js';
import { showToast } from './toast.js';

export function renderGallerySection() {
    const s = ctx.settings;
    // 图库 NSFW 用独立键；未显式设置时回退到界面的全局值（?? 仅在 undefined/null 时回退）
    const gBlur = s.gallery_blur_nsfw ?? s.blur_nsfw;
    const gSfw = s.gallery_show_only_sfw ?? s.show_only_sfw;
    const gThr = s.gallery_nsfw_blur_threshold ?? s.nsfw_blur_threshold;
    return `
    <div class="settings-section-panel" id="section-gallery">
        <div class="settings-subsection">
            <h3>布局</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>每页数量</span>
                    <span class="setting-hint">工作流页图库每页显示的图片数（刷新图库后生效）</span>
                </div>
                <div class="setting-control">
                    <select id="set-gallery-page-size" class="settings-select">
                        <option value="20"${s.gallery_page_size == 20 ? ' selected' : ''}>20</option>
                        <option value="40"${s.gallery_page_size == 40 || !s.gallery_page_size ? ' selected' : ''}>40</option>
                        <option value="60"${s.gallery_page_size == 60 ? ' selected' : ''}>60</option>
                        <option value="80"${s.gallery_page_size == 80 ? ' selected' : ''}>80</option>
                        <option value="100"${s.gallery_page_size == 100 ? ' selected' : ''}>100</option>
                    </select>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>缩略图大小</span>
                    <span class="setting-hint">图库卡片缩略图的最小宽度</span>
                </div>
                <div class="setting-control">
                    <select id="set-gallery-thumb-size" class="settings-select">
                        <option value="small"${s.gallery_thumb_size === 'small' ? ' selected' : ''}>小 (140px)</option>
                        <option value="medium"${(s.gallery_thumb_size || 'medium') === 'medium' ? ' selected' : ''}>中 (200px)</option>
                        <option value="large"${s.gallery_thumb_size === 'large' ? ' selected' : ''}>大 (260px)</option>
                    </select>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>显示卡片文件名</span>
                    <span class="setting-hint">关闭时卡片下方只留徽章，更清爽（自动生成的文件名通常是 hash，可读性差）</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-gallery-show-filename', !!s.gallery_show_filename)}
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>NSFW 模糊</h3>
            <p class="settings-desc">图库独立设置，未调整时跟随"界面"Tab 的全局 NSFW 设置。</p>
            <div class="setting-row">
                <div class="setting-label">
                    <span>仅显示 SFW</span>
                    <span class="setting-hint">直接隐藏达到阈值的图片 / 视频和手动标 NSFW 的条目，比模糊更彻底</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-gallery-show-only-sfw', !!gSfw)}
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>模糊 NSFW 图片</span>
                    <span class="setting-hint">达到阈值的图片 / 视频封面模糊，悬停解除</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-gallery-blur-nsfw', gBlur !== false)}
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>成人内容模糊阈值</span>
                    <span class="setting-hint">越低越严格；低于阈值的图片不模糊</span>
                </div>
                <div class="setting-control">
                    <select id="set-gallery-nsfw-threshold" class="settings-select">
                        <option value="2"${String(gThr) === '2' ? ' selected' : ''}>PG13 及以上</option>
                        <option value="4"${(String(gThr) === '4' || gThr == null) ? ' selected' : ''}>R 及以上（默认）</option>
                        <option value="8"${String(gThr) === '8' ? ' selected' : ''}>X 及以上</option>
                        <option value="16"${String(gThr) === '16' ? ' selected' : ''}>仅 XXX</option>
                    </select>
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>存储</h3>
            <div class="setting-row setting-row-wide">
                <div class="setting-label">
                    <span>图库存储目录</span>
                    <span class="setting-hint">新保存的图片落地位置；留空=使用默认 <code>${escapeAttr(s._workflow_gallery_dir_resolved || '')}</code>。已在库内的旧图保持原位置不动。</span>
                </div>
                <div class="setting-control">
                    <input type="text" id="set-gallery-dir" class="settings-input"
                           value="${escapeAttr(s.workflow_gallery_dir || '')}"
                           placeholder="${escapeAttr(s._workflow_gallery_dir_resolved || '默认路径')}">
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>清理失效记录</span>
                    <span class="setting-hint">删除图库中 file_path 已不存在于磁盘的记录（如插件被移动后的旧数据）</span>
                </div>
                <div class="setting-control">
                    <button class="btn btn-sm" id="set-gallery-cleanup-missing">清理</button>
                </div>
            </div>
        </div>
    </div>`;
}

export function bindGalleryEvents(content) {
    const pageSizeEl = content.querySelector('#set-gallery-page-size');
    if (pageSizeEl) {
        pageSizeEl.addEventListener('change', () => {
            const val = parseInt(pageSizeEl.value) || 40;
            saveSetting('gallery_page_size', val);
            // 立刻通知工作流页重新加载
            window.dispatchEvent(new CustomEvent('noctyra-gallery-settings-changed', {
                detail: { key: 'gallery_page_size', value: val },
            }));
        });
    }

    bindSelect(content, 'set-gallery-thumb-size', 'gallery_thumb_size', (val) => {
        window.dispatchEvent(new CustomEvent('noctyra-gallery-settings-changed', {
            detail: { key: 'gallery_thumb_size', value: val },
        }));
    });

    bindToggle(content, 'set-gallery-show-filename', 'gallery_show_filename', (val) => {
        window.dispatchEvent(new CustomEvent('noctyra-gallery-settings-changed', {
            detail: { key: 'gallery_show_filename', value: val },
        }));
    });

    // NSFW 模糊（图库独立 key：gallery_*；变更时通知工作流页刷新模糊态）
    bindToggle(content, 'set-gallery-show-only-sfw', 'gallery_show_only_sfw', (val) => {
        window.dispatchEvent(new CustomEvent('noctyra-gallery-settings-changed', {
            detail: { key: 'gallery_show_only_sfw', value: val },
        }));
    });
    bindToggle(content, 'set-gallery-blur-nsfw', 'gallery_blur_nsfw', (val) => {
        window.dispatchEvent(new CustomEvent('noctyra-gallery-settings-changed', {
            detail: { key: 'gallery_blur_nsfw', value: val },
        }));
    });
    const thresholdEl = content.querySelector('#set-gallery-nsfw-threshold');
    if (thresholdEl) {
        thresholdEl.addEventListener('change', () => {
            const val = parseInt(thresholdEl.value);
            saveSetting('gallery_nsfw_blur_threshold', val);
            window.dispatchEvent(new CustomEvent('noctyra-gallery-settings-changed', {
                detail: { key: 'gallery_nsfw_blur_threshold', value: val },
            }));
        });
    }

    const dirEl = content.querySelector('#set-gallery-dir');
    if (dirEl) {
        dirEl.addEventListener('blur', () => {
            const val = dirEl.value.trim();
            if ((ctx.settings.workflow_gallery_dir || '') !== val) {
                saveSetting('workflow_gallery_dir', val);
            }
        });
    }

    const cleanupBtn = content.querySelector('#set-gallery-cleanup-missing');
    if (cleanupBtn) {
        cleanupBtn.addEventListener('click', async () => {
            cleanupBtn.disabled = true;
            cleanupBtn.textContent = '清理中...';
            try {
                const res = await api.cleanupMissingWorkflowImages();
                if (res.success) {
                    const n = res.removed || 0;
                    showToast(n > 0 ? `已清理 ${n} 条失效图库记录` : '没有需要清理的记录', 'success');
                    if (n > 0) {
                        // 通知工作流页重新加载图库
                        window.dispatchEvent(new CustomEvent('noctyra-wf-reload-gallery'));
                    }
                } else {
                    showToast('清理失败: ' + (res.error || ''), 'error');
                }
            } catch (e) {
                showToast('清理出错: ' + e.message, 'error');
            } finally {
                cleanupBtn.disabled = false;
                cleanupBtn.textContent = '清理';
            }
        });
    }
}
