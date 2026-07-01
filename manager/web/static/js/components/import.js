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
 * 模型导入 — 拖拽/文件选择（走 HTTP 上传）或粘贴本地路径（后端 shutil）。
 * 默认都落到 <ComfyUI>/models/Unknown/，完成后触发列表刷新。
 */
import * as api from '../api.js';
import { showToast, showOpError } from './toast.js';
import { loadModels } from './card-grid.js';
import { escapeAttr as esc } from '../utils.js';

const ACCEPT_EXTS = ['.safetensors', '.ckpt', '.pt', '.bin', '.gguf'];
let overlay = null;
let busy = false;

export function initImport() {
    const el = document.createElement('div');
    el.className = 'settings-overlay';
    el.id = 'import-overlay';
    el.innerHTML = '<div class="settings-content"></div>';
    document.body.appendChild(el);
    overlay = el;

    el.addEventListener('click', e => {
        if (e.target === el && !busy) closeImport();
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && overlay.classList.contains('show') && !busy) closeImport();
    });

    // Ctrl+V 粘贴剪贴板里的文件（资源管理器 Ctrl+C 过来的文件）
    document.addEventListener('paste', e => {
        if (!overlay.classList.contains('show') || busy) return;
        // 如果焦点在路径输入框里，让它正常粘贴文本
        const active = document.activeElement;
        if (active && active.id === 'im-path-input') return;

        const files = e.clipboardData && e.clipboardData.files;
        if (files && files.length > 0) {
            e.preventDefault();
            handleUpload(files[0]);
        }
    });

    const btn = document.getElementById('btn-import');
    if (btn) btn.addEventListener('click', openImport);
}

function openImport() {
    if (!overlay) return;
    const content = overlay.querySelector('.settings-content');
    content.innerHTML = `
        <div class="settings-header">
            <h2>导入模型</h2>
            <button class="modal-close" id="im-close">&times;</button>
        </div>
        <div class="im-body">
            <div class="im-target-hint">目标：<code>&lt;ComfyUI&gt;/models/Unknown/</code></div>

            <div class="im-dropzone" id="im-dropzone">
                <div class="im-dz-icon">⬇</div>
                <div class="im-dz-main">拖拽模型文件到这里</div>
                <div class="im-dz-sub">或 <button class="btn btn-sm btn-primary" id="im-pick-btn">选择文件</button>，或 Ctrl+V 粘贴文件</div>
                <div class="im-dz-hint">支持 ${ACCEPT_EXTS.join(' / ')}；大文件建议用下方路径粘贴（零拷贝最快）</div>
            </div>
            <input type="file" id="im-file-input" style="display:none" accept="${ACCEPT_EXTS.join(',')}">

            <div class="im-divider"><span>或</span></div>

            <div class="im-path-row">
                <div class="im-path-label">粘贴本地绝对路径（零拷贝，大文件首选）</div>
                <div class="im-path-input-row">
                    <input type="text" id="im-path-input" class="settings-input" placeholder="D:\\Downloads\\model.safetensors">
                    <label class="im-move-label"><input type="checkbox" id="im-move-chk"> 移动（源文件会被删除）</label>
                    <button class="btn btn-primary" id="im-path-btn">导入</button>
                </div>
            </div>

            <div class="im-progress" id="im-progress" style="display:none">
                <div class="im-progress-label" id="im-progress-label">准备中...</div>
                <div class="im-progress-bar"><div class="im-progress-fill" id="im-progress-fill"></div></div>
            </div>
        </div>
    `;

    content.querySelector('#im-close').addEventListener('click', closeImport);
    content.querySelector('#im-pick-btn').addEventListener('click', () => {
        content.querySelector('#im-file-input').click();
    });
    content.querySelector('#im-file-input').addEventListener('change', (e) => {
        const f = e.target.files && e.target.files[0];
        if (f) handleUpload(f);
    });
    content.querySelector('#im-path-btn').addEventListener('click', handlePathImport);
    content.querySelector('#im-path-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') handlePathImport();
    });

    const dz = content.querySelector('#im-dropzone');
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag-over'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
    dz.addEventListener('drop', e => {
        e.preventDefault();
        dz.classList.remove('drag-over');
        const f = e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) handleUpload(f);
    });

    overlay.classList.add('show');
    setTimeout(() => content.querySelector('#im-path-input')?.focus(), 100);
}

function closeImport() {
    if (!overlay || busy) return;
    overlay.classList.remove('show');
}

function validateExt(name) {
    const lower = (name || '').toLowerCase();
    return ACCEPT_EXTS.some(ext => lower.endsWith(ext));
}

function showProgress(label) {
    const box = document.getElementById('im-progress');
    const lab = document.getElementById('im-progress-label');
    const fill = document.getElementById('im-progress-fill');
    if (!box) return;
    box.style.display = 'block';
    lab.textContent = label;
    fill.style.width = '0%';
}

function updateProgress(loaded, total, prefix = '上传中') {
    const lab = document.getElementById('im-progress-label');
    const fill = document.getElementById('im-progress-fill');
    if (!lab || !fill) return;
    const pct = total > 0 ? Math.round(loaded * 100 / total) : 0;
    fill.style.width = pct + '%';
    lab.textContent = `${prefix} ${pct}% (${fmtSize(loaded)} / ${fmtSize(total)})`;
}

function hideProgress() {
    const box = document.getElementById('im-progress');
    if (box) box.style.display = 'none';
}

function fmtSize(n) {
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${units[i]}`;
}

async function handleUpload(file) {
    if (busy) return;
    if (!validateExt(file.name)) {
        showToast(`不支持的扩展名，仅支持 ${ACCEPT_EXTS.join('/')}`, 'error');
        return;
    }
    busy = true;
    showProgress(`准备上传 ${file.name}`);
    try {
        const res = await api.importUpload(file, (loaded, total) => updateProgress(loaded, total));
        if (res.success) {
            showToast(`导入成功：${res.file_name}`, 'success');
            closeImportForce();
            await loadModels();
            window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        } else {
            showOpError(res, '导入失败');
            hideProgress();
        }
    } catch (e) {
        showToast('上传异常：' + (e?.message || e), 'error');
        hideProgress();
    } finally {
        busy = false;
    }
}

async function handlePathImport() {
    if (busy) return;
    const input = document.getElementById('im-path-input');
    const moveChk = document.getElementById('im-move-chk');
    const path = (input?.value || '').trim();
    if (!path) {
        showToast('请填写本地路径', 'warn');
        return;
    }
    if (!validateExt(path)) {
        showToast(`不支持的扩展名，仅支持 ${ACCEPT_EXTS.join('/')}`, 'error');
        return;
    }

    busy = true;
    showProgress(moveChk.checked ? '移动中...' : '复制中...');
    try {
        const res = await api.importFromPath(path, !!moveChk.checked);
        if (res.success) {
            showToast(`导入成功：${res.file_name}`, 'success');
            closeImportForce();
            await loadModels();
            window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        } else {
            showOpError(res, '导入失败');
            hideProgress();
        }
    } catch (e) {
        showToast('请求异常：' + (e?.message || e), 'error');
        hideProgress();
    } finally {
        busy = false;
    }
}

function closeImportForce() {
    busy = false;
    if (overlay) overlay.classList.remove('show');
}
