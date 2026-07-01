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
 * 右键上下文菜单
 */
import * as api from '../api.js';
import { escapeHtml as escHtml, escapeAttr as escAttr, resolveSourceUrl, isSafeUrl } from '../utils.js';
import { state } from '../state.js';
import { openDetailModal } from './modal.js';
import { showToast, showOpError } from './toast.js';
import { showConfirm } from './dialog.js';
import { loadModels } from './card-grid.js';

const SVG = (d) => `<svg class="ctx-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICONS = {
    detail: SVG('<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="14" y1="9" x2="19" y2="9"/><line x1="14" y1="13" x2="19" y2="13"/>'),
    starOn: SVG('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" fill="currentColor"/>'),
    starOff: SVG('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>'),
    copyName: SVG('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M16 13H8"/><path d="M16 17H8"/>'),
    copyPath: SVG('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'),
    hash: SVG('<line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/>'),
    match: SVG('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'),
    organize: SVG('<path d="M3 7h6l2 3h10v10a2 2 0 0 1-2 2H3z"/>'),
    reveal: SVG('<path d="M3 7h6l2 3h10v10a2 2 0 0 1-2 2H3z"/><circle cx="14" cy="15" r="3"/>'),
    softDelete: SVG('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="9" y1="14" x2="15" y2="14"/>'),
    restore: SVG('<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'),
    integrity: SVG('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>'),
    redownload: SVG('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'),
    trash: SVG('<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>'),
};

let menuEl = null;
let currentModel = null;

export function initContextMenu() {
    menuEl = document.getElementById('context-menu');
    if (!menuEl) return;

    const grid = document.getElementById('card-grid');
    if (grid) {
        grid.addEventListener('contextmenu', onContextMenu);
    }

    document.addEventListener('click', hide);
    document.addEventListener('contextmenu', e => {
        if (!e.target.closest('#card-grid .model-card')) hide();
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') hide();
    });
    window.addEventListener('scroll', hide, true);
}

function onContextMenu(e) {
    const card = e.target.closest('.model-card');
    if (!card) return;

    e.preventDefault();

    const filePath = card.dataset.filePath;
    currentModel = state.models.find(m => m.file_path === filePath);
    if (!currentModel) return;

    renderMenu();
    positionMenu(e.clientX, e.clientY);
    menuEl.classList.add('show');
}

function renderMenu() {
    const m = currentModel;
    const isFav = m.favorite;
    const hasSource = m.source_url;
    const isCivitai = m.source === 'civitai';
    const isHF = m.source === 'huggingface' || m.hf_repo_id;

    let items = `
        <button class="context-menu-item" data-action="detail">
            ${ICONS.detail}查看详情
        </button>
        <button class="context-menu-item" data-action="favorite">
            ${isFav ? ICONS.starOn : ICONS.starOff}${isFav ? '取消收藏' : '添加收藏'}
        </button>
        <div class="context-menu-separator"></div>
        <button class="context-menu-item" data-action="copy-name">
            ${ICONS.copyName}复制模型名
        </button>
        <button class="context-menu-item" data-action="copy-path">
            ${ICONS.copyPath}复制文件路径
        </button>`;

    if (m.sha256) {
        items += `
        <button class="context-menu-item" data-action="copy-hash">
            ${ICONS.hash}复制 SHA256
        </button>`;
    }

    if (m.sha256) {
        items += `
        <div class="context-menu-separator"></div>
        <button class="context-menu-item" data-action="match-single">
            ${ICONS.match}在线匹配（CivitAI + HF）
        </button>
        <button class="context-menu-item" data-action="match-civitai">
            <img class="ctx-icon ctx-icon-img" src="/noctyra_static/images/civitai-logo.svg" alt="">仅匹配 CivitAI
        </button>
        <button class="context-menu-item" data-action="match-hf">
            <img class="ctx-icon ctx-icon-img" src="/noctyra_static/images/hf-logo.svg" alt="">仅匹配 HuggingFace
        </button>`;
    }

    items += `
        <div class="context-menu-separator"></div>
        <button class="context-menu-item" data-action="reveal-in-explorer">
            ${ICONS.reveal}在资源管理器中显示
        </button>
        <button class="context-menu-item" data-action="organize-single">
            ${ICONS.organize}按设置整理到对应文件夹
        </button>`;

    if (!m.file_deleted) {
        items += `
        <button class="context-menu-item" data-action="check-integrity">
            ${ICONS.integrity}检测是否损坏
        </button>`;
        if (m.civitai_version_id) {
            items += `
        <button class="context-menu-item" data-action="redownload" title="从 CivitAI 重新下载并覆盖（修复损坏/丢失，下载完成前不破坏现有文件）">
            ${ICONS.redownload}重新下载（覆盖）
        </button>`;
        }
    }

    if (isCivitai && hasSource) {
        items += `
        <div class="context-menu-separator"></div>
        <button class="context-menu-item" data-action="open-civitai">
            <img class="ctx-icon ctx-icon-img" src="/noctyra_static/images/civitai-logo.svg" alt="">在 CivitAI 中打开
        </button>`;
    }

    if (isHF) {
        const hfUrl = m.hf_repo_id
            ? `https://huggingface.co/${m.hf_repo_id}`
            : m.source_url;
        if (hfUrl) {
            items += `
        <button class="context-menu-item" data-action="open-hf" data-url="${escAttr(hfUrl)}">
            <img class="ctx-icon ctx-icon-img" src="/noctyra_static/images/hf-logo.svg" alt="">在 HuggingFace 中打开
        </button>`;
        }
    }

    if (!m.file_deleted) {
        items += `
        <div class="context-menu-separator"></div>
        <button class="context-menu-item danger" data-action="soft-delete" title="把文件移到存档夹（保留记录，便于上传网盘备份）">
            ${ICONS.softDelete}存档（移到存档夹）
        </button>`;
    } else {
        items += `
        <div class="context-menu-separator"></div>
        <button class="context-menu-item" data-action="restore" title="文件还在存档夹则移回原位；否则把文件放回任意模型目录、扫描自动归位">
            ${ICONS.restore}恢复（从存档夹取回）
        </button>`;
    }
    items += `
        <div class="context-menu-separator"></div>
        <button class="context-menu-item danger" data-action="delete">
            ${ICONS.trash}彻底删除
        </button>`;

    menuEl.innerHTML = items;

    menuEl.querySelectorAll('.context-menu-item').forEach(item => {
        item.addEventListener('click', () => handleAction(item.dataset.action, item));
    });
}

function positionMenu(x, y) {
    menuEl.style.left = '0px';
    menuEl.style.top = '0px';
    menuEl.classList.add('show');

    const rect = menuEl.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let left = x;
    let top = y;

    if (x + rect.width > vw - 8) left = vw - rect.width - 8;
    if (y + rect.height > vh - 8) top = vh - rect.height - 8;
    if (left < 8) left = 8;
    if (top < 8) top = 8;

    menuEl.style.left = left + 'px';
    menuEl.style.top = top + 'px';
}

function hide() {
    if (menuEl) menuEl.classList.remove('show');
    currentModel = null;
}

async function handleAction(action, itemEl) {
    const m = currentModel;
    if (!m) return;
    hide();

    switch (action) {
        case 'detail': {
            // file_path 优先：同 sha256 的重复文件按哈希会解析到另一个副本
            const id = m.file_path || m.sha256;
            openDetailModal(id);
            break;
        }
        case 'favorite': {
            const newState = !m.favorite;
            m.favorite = newState;
            await api.toggleFavorite(m.file_path, newState);
            const card = document.querySelector(`.model-card[data-file-path="${CSS.escape(m.file_path)}"]`);
            if (card) {
                const btn = card.querySelector('.card-fav-btn');
                if (btn) btn.classList.toggle('active', newState);
            }
            showToast(newState ? '已收藏' : '已取消收藏', 'success');
            break;
        }
        case 'copy-name': {
            const name = m.model_name || m.file_name;
            await copyText(name);
            showToast('已复制模型名', 'success');
            break;
        }
        case 'copy-path': {
            await copyText(m.file_path);
            showToast('已复制文件路径', 'success');
            break;
        }
        case 'copy-hash': {
            await copyText(m.sha256);
            showToast('已复制 SHA256', 'success');
            break;
        }
        case 'open-civitai': {
            const url = resolveSourceUrl(m.source_url, m.nsfw);
            // isSafeUrl 拦 javascript:/vbscript: 等，防被投毒的 source_url 在新窗口执行脚本
            if (isSafeUrl(url)) window.open(url, '_blank', 'noopener');
            else showToast('链接无效', 'error');
            break;
        }
        case 'open-hf': {
            const url = itemEl?.dataset.url || m.source_url;
            if (url && isSafeUrl(url)) window.open(url, '_blank', 'noopener');
            else if (url) showToast('链接无效', 'error');
            break;
        }
        case 'reveal-in-explorer': {
            const res = await api.revealInExplorer(m.file_path);
            if (res.success) {
                showToast('已在文件管理器中显示', 'success');
            } else {
                showToast('打开失败: ' + (res.error || ''), 'error');
            }
            break;
        }
        case 'match-single':    { await runMatch(m, '');            break; }
        case 'match-civitai':   { await runMatch(m, 'civitai');     break; }
        case 'match-hf':        { await runMatch(m, 'huggingface'); break; }
        case 'organize-single': {
            const name = m.model_name || m.file_name;
            showToast(`正在整理: ${name}...`, 'info');
            try {
                const res = await api.organizeSingle(m.file_path);
                if (res.success && res.moved) {
                    const reasonMap = {
                        type_mismatch: '迁到正确类型目录',
                        base_model: '按 Base Model 分组',
                        uncategorized: '缺少整理信息，归入 Unknown/',
                        unknown_type: '模型类型未知，归入 models/Unknown/',
                    };
                    const reasonText = reasonMap[res.reason] || '已整理';
                    showToast(`整理完成: ${res.target_folder || ''} (${reasonText})`, 'success');
                    loadModels();
                    window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
                } else if (res.success) {
                    showToast('已在目标位置，无需移动', 'info');
                } else {
                    showOpError(res, '整理失败');
                }
            } catch (e) {
                showToast('整理出错: ' + e.message, 'error');
            }
            break;
        }
        case 'check-integrity': {
            showToast('检测中…', 'info');
            const r = await api.checkIntegrity(m.file_path);
            if (!r.success) { showOpError(r, '检测失败'); break; }
            if (r.ok) {
                showToast(`✓ ${m.file_name} 完好（${r.detail || ''}）`, 'success', 5000);
            } else {
                // 损坏：弹详情，并直接问要不要从 CivitAI 重下覆盖
                const detail = r.detail ? `\n${r.detail}` : '';
                if (m.civitai_version_id) {
                    if (await showConfirm({ title: '模型损坏', okText: '重新下载', message: `${r.error}${detail}\n\n现在从 CivitAI 重新下载覆盖吗？（下载完成前不破坏现有文件）` })) {
                        const rr = await api.redownloadModel(m.file_path);
                        if (rr.success) showToast('已开始重新下载，进度见右下任务条', 'success');
                        else showOpError(rr, '重下失败');
                    }
                } else {
                    showToast(`⚠ 损坏：${r.error}（无 CivitAI 来源，需手动重下）`, 'error', 7000);
                }
            }
            break;
        }
        case 'redownload': {
            if (!await showConfirm({ title: '重新下载', okText: '重新下载', message: `从 CivitAI 重新下载并覆盖 "${m.file_name}"？\n（下载完成前不会破坏现有文件）` })) break;
            const rr = await api.redownloadModel(m.file_path);
            if (rr.success) showToast('已开始重新下载，进度见右下任务条', 'success');
            else showOpError(rr, '重下失败');
            break;
        }
        case 'soft-delete': {
            if (!await showConfirm({ title: '存档模型', okText: '存档', message: `确认存档 "${m.file_name}"？\n文件将移到存档夹（不是删除），模型信息保留在管理器中。` })) break;
            const sdRes = await api.softDeleteModel(m.file_path);
            if (sdRes.success) {
                showToast('已移到存档夹，记录保留', 'success');
                loadModels();
                window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
            } else {
                showOpError(sdRes, '存档失败');
            }
            break;
        }
        case 'restore': {
            const rRes = await api.restoreModel(m.file_path);
            if (rRes.success) {
                showToast('已恢复', 'success');
                loadModels();
                window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
            } else {
                showOpError(rRes, '文件不在存档夹，请放回任意模型目录后扫描');
            }
            break;
        }
        case 'delete': {
            showDeleteFromContext(m);
            break;
        }
    }
}

function showDeleteFromContext(model) {
    const overlay = document.createElement('div');
    overlay.className = 'delete-confirm-overlay';
    overlay.innerHTML = `
        <div class="delete-confirm">
            <h3>删除模型</h3>
            <p>确认要从管理器中移除 <strong>${escHtml(model.model_name || model.file_name)}</strong> 吗？</p>
            <label class="delete-file-option">
                <input type="checkbox" id="ctx-delete-file-check">
                同时删除本地文件（不可恢复）
            </label>
            <div class="delete-confirm-actions">
                <button class="btn" id="ctx-delete-cancel">取消</button>
                <button class="btn btn-danger" id="ctx-delete-confirm">删除</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    overlay.querySelector('#ctx-delete-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    overlay.querySelector('#ctx-delete-confirm').addEventListener('click', async () => {
        const deleteFile = overlay.querySelector('#ctx-delete-file-check').checked;
        const res = await api.deleteModel(model.file_path, deleteFile);
        overlay.remove();
        if (res.success) {
            showToast('模型已删除', 'success');
            loadModels();
            window.dispatchEvent(new Event('noctyra-refresh-sidebar'));
        } else {
            showOpError(res, '删除失败');
        }
    });
}

async function runMatch(m, source) {
    const name = m.model_name || m.file_name;
    const label = source === 'civitai' ? 'CivitAI' : source === 'huggingface' ? 'HuggingFace' : '在线';
    showToast(`正在${label}匹配: ${name}...`, 'info');
    try {
        const res = await api.matchSingle(m.file_path, source);
        if (res.success && res.matched) {
            const d = res.detail;
            const parts = [];
            if (d.civitai) parts.push('CivitAI');
            if (d.huggingface) parts.push('HuggingFace');
            showToast(`匹配成功: ${parts.join(' + ')}`, 'success');
            loadModels();
        } else if (res.success) {
            showToast(`未找到 ${label} 匹配结果`, 'warning');
        } else {
            showOpError(res, '匹配失败');
        }
    } catch (e) {
        showToast('匹配出错: ' + e.message, 'error');
    }
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
    }
}
