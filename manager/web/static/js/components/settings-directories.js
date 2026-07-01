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
 * Settings - 目录 Section（合并了原"库"的路径模板 + 基础模型映射）
 *   - 模型目录
 *   - 每类型默认下载目录
 *   - 自动整理路径模板
 *   - 已识别的 Base Model（只读）
 *   - 基础模型路径映射
 *   - 视频 / 纯 UNet 基础模型（覆盖 CivitAI 的 Checkpoint 分类）
 */
import * as api from '../api.js';
import { showToast } from './toast.js';
import { showConfirm } from './dialog.js';
import { escapeHtml as escHtml, escapeAttr as esc } from '../utils.js';
import { ctx, saveSetting } from './settings-helpers.js';

// ===== 每类型默认下载目录 =====
const DEFAULT_ROOT_TYPES = [
    { key: 'lora',       label: 'LoRA' },
    { key: 'checkpoint', label: 'Checkpoint' },
    { key: 'unet',       label: 'Diffusion Model / UNet' },
    { key: 'embedding',  label: 'Embedding' },
    { key: 'vae',        label: 'VAE' },
    { key: 'controlnet', label: 'ControlNet' },
    { key: 'text_encoder', label: 'Text Encoder' },
    { key: 'clip_vision', label: 'CLIP Vision' },
];

// ===== 自动整理路径模板预设 =====
const TEMPLATE_PRESETS = [
    { value: '',                                      label: '不分子目录',                 example: 'model.safetensors' },
    { value: '{base_model}',                          label: '按基础模型',                 example: 'Flux.1 D/model.safetensors' },
    { value: '{first_tag}',                           label: '按首标签',                   example: 'style/model.safetensors' },
    { value: '{author}',                              label: '按作者',                     example: 'authorname/model.safetensors' },
    { value: '{base_model}/{first_tag}',              label: '基础模型 + 首标签',          example: 'Flux.1 D/style/model.safetensors' },
    { value: '{base_model}/{author}',                 label: '基础模型 + 作者',            example: 'Flux.1 D/authorname/model.safetensors' },
    { value: '{author}/{first_tag}',                  label: '作者 + 首标签',              example: 'authorname/style/model.safetensors' },
    { value: '{base_model}/{author}/{first_tag}',     label: '基础模型 + 作者 + 首标签',   example: 'Flux.1 D/authorname/style/model.safetensors' },
    { value: '{base_model}/{model_name}',             label: '基础模型 + 模型名',          example: 'Flux.1 D/ExampleModel/model.safetensors' },
    { value: '{author}/{model_name}',                 label: '作者 + 模型名',              example: 'authorname/ExampleModel/model.safetensors' },
    { value: '{base_model}/{model_name}/{version_name}', label: '基础模型 + 模型名 + 版本', example: 'Flux.1 D/ExampleModel/v1/model.safetensors' },
    { value: '{base_model}/{author}/{model_name}',    label: '基础模型 + 作者 + 模型名',   example: 'Flux.1 D/authorname/ExampleModel/model.safetensors' },
];

// 每类型在模板里的归一化 key（与后端 _TEMPLATE_TYPE_ALIAS 对齐）
const LIBRARY_TYPES = [
    { key: 'lora',         label: 'LoRA' },
    { key: 'checkpoint',   label: 'Checkpoint' },
    { key: 'embedding',    label: 'Embedding' },
    { key: 'vae',          label: 'VAE' },
    { key: 'controlnet',   label: 'ControlNet' },
    { key: 'unet',         label: 'UNet' },
    { key: 'upscale',      label: 'Upscaler' },
    { key: 'clip',         label: 'CLIP' },
    { key: 'text_encoder', label: 'Text Encoder' },
    { key: 'clip_vision',  label: 'CLIP Vision' },
    { key: 'motion',       label: 'Motion' },
    { key: 'detection',    label: 'Detection' },
    { key: 'hypernetwork', label: 'Hypernetwork' },
];

// 基础模型路径映射下拉的候选（CivitAI 官方名，与后端 base_models 规范集对齐）
const MAPPABLE_BASE_MODELS = [
    'SD 1.5', 'SD 2.0', 'SD 3', 'SD 3.5',
    'SDXL 1.0', 'SDXL Lightning', 'SDXL Hyper', 'Stable Cascade',
    'Pony', 'Illustrious', 'NoobAI', 'Anima',
    'Flux.1 D', 'Flux.1 S', 'Flux.1 Kontext', 'Flux.1 Krea',
    'Flux.2 D', 'Flux.2 Klein 9B', 'Flux.2 Klein 4B',
    'Qwen', 'Chroma', 'AuraFlow', 'Kolors', 'HiDream', 'Lumina',
    'ZImageTurbo', 'ZImageBase', 'PixArt a',
    'Wan Video', 'Hunyuan Video', 'LTXV', 'LTXV2', 'CogVideoX', 'Mochi',
];

// ===== 存储位置（项目文件夹 / data_root） =====
function renderDataRootStatus(s) {
    if (s._data_root_missing) {
        return `<div class="dr-warn">⚠ 项目文件夹当前不可用（外置盘未挂载？），正用插件本地目录。请检查路径后重启。</div>`;
    }
    if (s._data_root_active) {
        return `<div class="dr-ok">当前项目文件夹：<code>${escHtml(s._data_root || '')}</code></div>`;
    }
    return `<div class="dr-muted">传统模式：数据在插件目录 <code>${escHtml(s._plugin_dir || '')}</code></div>`;
}

function bindDataRootEvents(content) {
    const setBtn = content.querySelector('#data-root-set-btn');
    if (setBtn) setBtn.addEventListener('click', async () => {
        const input = content.querySelector('#data-root-input');
        const path = (input.value || '').trim();
        if (!path) { showToast('请输入项目文件夹路径', 'error'); return; }
        if (!await showConfirm({ title: '迁移项目文件夹', okText: '继续', message: '将把现有数据复制到：\n' + path + '\n\n完成后需重启 ComfyUI 生效。继续？' })) return;
        setBtn.disabled = true;
        const old = setBtn.textContent;
        setBtn.textContent = '迁移中…';
        try {
            const res = await fetch('/api/noctyra/data-root', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path }),
            }).then(r => r.json());
            if (res.success) {
                const msg = res.mode === 'adopt' ? '已采用该文件夹现有数据库' : '数据已复制到新位置';
                showToast(msg + '，请重启 ComfyUI 生效', 'success');
            } else {
                showToast(res.error || '迁移失败', 'error');
            }
        } catch (e) {
            showToast('迁移失败：' + (e.message || '网络错误'), 'error');
        } finally {
            setBtn.disabled = false;
            setBtn.textContent = old;
        }
    });

    const clearBtn = content.querySelector('#data-root-clear-btn');
    if (clearBtn) clearBtn.addEventListener('click', async () => {
        if (!await showConfirm({ title: '清除项目文件夹设置', okText: '清除', message: '清除项目文件夹设置？\n下次启动回退到插件目录（不会删除项目文件夹里的数据）。' })) return;
        try {
            const res = await fetch('/api/noctyra/data-root/clear', { method: 'POST' }).then(r => r.json());
            if (res.success) showToast('已清除，请重启 ComfyUI 生效', 'success');
            else showToast('操作失败', 'error');
        } catch (e) {
            showToast('操作失败：' + (e.message || '网络错误'), 'error');
        }
    });
}

export function renderDirectoriesSection() {
    const s = ctx.settings;
    const dirs = s.model_roots || [];

    return `
    <div class="settings-section-panel" id="section-directories">
        <div class="settings-subsection">
            <h3>存储位置（项目文件夹）</h3>
            <p class="settings-desc">把数据库、预览缓存、图库集中到一个你指定的「项目文件夹」，迁移/换机时打包这一个文件夹即可。留空 = 传统模式（数据在插件目录内）。</p>
            <div class="data-root-status" id="data-root-status">${renderDataRootStatus(s)}</div>
            <div class="dir-add-row">
                <input type="text" id="data-root-input" class="settings-input" placeholder="项目文件夹绝对路径，如 D:\\NoctyraData" value="${esc(s._data_root || '')}">
                <button id="data-root-set-btn" class="btn btn-sm">设为项目文件夹</button>
                ${s._data_root_active ? '<button id="data-root-clear-btn" class="btn btn-sm" title="清除指针，下次启动回退插件目录（不删数据）">恢复插件目录</button>' : ''}
            </div>
            <p class="settings-desc"><span class="settings-hint-inline">设置后会把现有数据<b>复制</b>到新位置（目标已有库则直接采用），校验通过才切换。<b>需重启 ComfyUI 生效</b>；旧本地数据在下次启动确认无误后自动清理。</span></p>
        </div>

        <div class="settings-subsection">
            <h3>模型目录</h3>
            <p class="settings-desc">配置需要扫描的模型文件夹。每个目录会根据文件夹名称自动推断模型类型（如 loras、checkpoints 等）。</p>
            <div id="dir-list" class="dir-list"></div>
            <div class="dir-add-row">
                <input type="text" id="new-dir-input" placeholder="输入模型目录路径" class="settings-input">
                <button id="add-dir-btn" class="btn btn-sm">添加</button>
                <button id="detect-dir-btn" class="btn btn-sm" title="自动检测 ComfyUI/models 子目录">自动检测</button>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>存档目录</h3>
            <p class="settings-desc">右键「存档（移到存档夹）」时，模型文件会<b>移到</b>这里（不是删除），便于你打包上传网盘备份、用完自行清理。下次把文件放回任意模型目录、扫描即按 sha256 自动归位。<br><span class="settings-hint-inline"><b>必须在模型目录之外</b>，否则扫描会把存档当成在库模型重新收录（设在模型目录内会拒绝保存）。留空 = 默认 <code>${esc(s._archive_dir_resolved || '')}</code></span></p>
            <div class="dir-add-row">
                <input type="text" id="archive-dir-input" class="settings-input" placeholder="${esc(s._archive_dir_resolved || '')}" value="${esc(s.archive_dir || '')}">
                <button id="archive-dir-save-btn" class="btn btn-sm">保存</button>
            </div>
        </div>

        <div class="settings-subsection" id="default-roots-subsection">
            <h3>每类型默认下载目录</h3>
            <p class="settings-desc">下载模型时按 CivitAI/HuggingFace 类型自动选中对应目录。"自动"= 按目录名关键字匹配（loras/checkpoints 等）。</p>
            ${renderDefaultRootsRows(dirs, s.default_roots || {})}
        </div>

        <div class="settings-subsection">
            <h3>自动整理路径模板</h3>
            <p class="settings-desc">按模型类型定义目标子目录格式。可用占位符：<code>{base_model}</code> <code>{first_tag}</code> <code>{author}</code>（或 <code>{creator}</code>） <code>{model_name}</code> <code>{version_name}</code> <code>{source}</code>。无法识别占位符的模型会归入 <code>Unknown/</code> 子目录。<br><span class="settings-hint-inline">HuggingFace 模型通常没有 <code>{first_tag}</code> 和 <code>{version_name}</code>。</span></p>
            <div class="lib-tpl-list">${renderTemplateRows()}</div>
        </div>

        <div class="settings-subsection" id="base-model-stats-subsection">
            <h3>已识别的 Base Model</h3>
            <p class="settings-desc">
                展示当前扫描结果里出现过的 <code>base_model</code> 分布（只读）。
                如果 CivitAI 后续调整了条目的 base_model，点击"刷新 base_model"可重新从 CivitAI API 拉取并更新本地记录。
                <br><span class="settings-hint-inline">仅处理已匹配到 CivitAI 的模型；HuggingFace / 未匹配项不动。</span>
            </p>
            <div class="bm-stats-toolbar">
                <button id="bm-refresh-btn" class="btn btn-sm">刷新 base_model</button>
                <span class="bm-stats-meta" id="bm-stats-meta"></span>
            </div>
            <div id="bm-stats-list" class="bm-stats-list">
                <div class="bm-stats-empty">加载中…</div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>基础模型路径映射</h3>
            <p class="settings-desc">把 <code>{base_model}</code> 替换为自定义文件夹名。例：把 "SD 1.5" 的模型统一放进 <code>sd15/</code>。</p>
            <div id="mapping-list" class="mapping-list">${renderMappingRowsHtml(s.base_model_path_mappings || {})}</div>
            <div class="mapping-add-row">
                <select id="mapping-new-bm" class="settings-select">
                    <option value="">选择基础模型…</option>
                    ${renderMappingDropdown(s.base_model_path_mappings || {})}
                </select>
                <input type="text" id="mapping-new-folder" class="settings-input" placeholder="文件夹名">
                <button id="mapping-add-btn" class="btn btn-sm">添加</button>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>视频 / 纯 UNet 基础模型</h3>
            <p class="settings-desc">
                CivitAI 把这些 base_model 标为 "Checkpoint" 时，整理 / 下载会强制按 <code>unet</code> 类型处理（落到 <code>diffusion_models/</code> 或 <code>unet/</code>）。
                匹配为"子串、大小写不敏感"，比如写 "Wan Video" 就能命中 "Wan Video 14B t2v"。
                <br><span class="settings-hint-inline">Flux 默认不在此列（Flux 既有 UNet-only 也有 full checkpoint）。若你的 Flux 一律按 UNet 使用，手动加进来即可。</span>
            </p>
            <div id="dmbm-tags" class="ext-tag-list">
                ${(s.diffusion_model_base_models || []).map(v => `<span class="ext-tag">${escHtml(v)}<button class="ext-remove" data-dmbm="${esc(v)}">&times;</button></span>`).join('')}
            </div>
            <div class="ext-add-row">
                <input type="text" id="new-dmbm-input" placeholder="如 Wan Video / Flux.1 D" class="settings-input settings-input-sm">
                <button id="add-dmbm-btn" class="btn btn-sm">添加</button>
            </div>
        </div>
    </div>`;
}

export function bindDirectoriesEvents(content) {
    bindDataRootEvents(content);
    renderDirList(content);
    bindDefaultRootSelects(content);
    bindTemplateRows(content);
    bindMappingSection(content);
    bindBaseModelStatsSection(content);
    bindDmbmSection(content);

    content.querySelector('#add-dir-btn').addEventListener('click', () => {
        const input = content.querySelector('#new-dir-input');
        const dir = input.value.trim();
        if (dir && !ctx.settings.model_roots.includes(dir)) {
            ctx.settings.model_roots.push(dir);
            renderDirList(content);
            rerenderDefaultRoots(content);
            input.value = '';
            saveSetting('model_roots', ctx.settings.model_roots);
        }
    });

    content.querySelector('#new-dir-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') content.querySelector('#add-dir-btn').click();
    });

    // 存档目录：直接调 saveSettings 以便回显服务端"不能在模型目录内"的具体报错
    const saveArchiveDir = async () => {
        const inp = content.querySelector('#archive-dir-input');
        const val = inp.value.trim();
        const res = await api.saveSettings({ archive_dir: val });
        if (res.success) {
            ctx.settings.archive_dir = val;
            showToast('存档目录已保存', 'success');
        } else {
            showToast(res.error || '保存失败', 'error');
            inp.value = ctx.settings.archive_dir || '';
        }
    };
    content.querySelector('#archive-dir-save-btn').addEventListener('click', saveArchiveDir);
    content.querySelector('#archive-dir-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') saveArchiveDir();
    });

    content.querySelector('#detect-dir-btn').addEventListener('click', async () => {
        const res = await api.detectDirs();
        if (res.success && res.dirs && res.dirs.length > 0) {
            let added = 0;
            for (const dir of res.dirs) {
                if (!ctx.settings.model_roots.includes(dir)) {
                    ctx.settings.model_roots.push(dir);
                    added++;
                }
            }
            if (added > 0) {
                renderDirList(content);
                rerenderDefaultRoots(content);
                saveSetting('model_roots', ctx.settings.model_roots);
                showToast(`检测到 ${added} 个新目录`, 'success');
            } else {
                showToast('没有检测到新目录', 'info');
            }
        } else {
            showToast('未检测到 ComfyUI models 目录', 'warning');
        }
    });
}

// ========== 模型目录列表 ==========

function renderDirList(content) {
    const list = content.querySelector('#dir-list');
    if (!list || !ctx.settings) return;

    if (ctx.settings.model_roots.length === 0) {
        list.innerHTML = '<div class="dir-empty">暂无目录（点击"自动检测"或手动添加）</div>';
        return;
    }

    list.innerHTML = ctx.settings.model_roots.map((dir, i) => `
        <div class="dir-item">
            <span class="dir-icon">📁</span>
            <span class="dir-path" title="${esc(dir)}">${escHtml(dir)}</span>
            <button class="dir-remove-btn" data-index="${i}" title="移除">&times;</button>
        </div>
    `).join('');

    list.querySelectorAll('.dir-remove-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.index);
            const removed = ctx.settings.model_roots[idx];
            ctx.settings.model_roots.splice(idx, 1);
            renderDirList(content);

            // 清理指向已删除目录的 default_roots 项
            const defaults = { ...(ctx.settings.default_roots || {}) };
            let defaultsChanged = false;
            for (const k of Object.keys(defaults)) {
                if (defaults[k] === removed) {
                    defaults[k] = '';
                    defaultsChanged = true;
                }
            }
            if (defaultsChanged) {
                ctx.settings.default_roots = defaults;
                saveSetting('default_roots', defaults);
            }
            rerenderDefaultRoots(content);

            saveSetting('model_roots', ctx.settings.model_roots);
        });
    });
}

// ========== 每类型默认下载目录 ==========

function renderDefaultRootsRows(dirs, defaults) {
    if (dirs.length === 0) {
        return '<div class="dir-empty">请先在上方添加模型目录</div>';
    }
    return DEFAULT_ROOT_TYPES.map(t => {
        const selected = defaults[t.key] || '';
        const opts = [`<option value=""${!selected ? ' selected' : ''}>自动（按名称匹配）</option>`]
            .concat(dirs.map(d => `<option value="${esc(d)}"${d === selected ? ' selected' : ''}>${escHtml(d)}</option>`))
            .join('');
        return `
            <div class="setting-row">
                <div class="setting-label">
                    <span>${escHtml(t.label)}</span>
                </div>
                <div class="setting-control">
                    <select class="settings-select default-root-select" data-type="${esc(t.key)}">${opts}</select>
                </div>
            </div>`;
    }).join('');
}

function bindDefaultRootSelects(content) {
    content.querySelectorAll('.default-root-select').forEach(sel => {
        sel.addEventListener('change', () => {
            const type = sel.dataset.type;
            const value = sel.value;
            const next = { ...(ctx.settings.default_roots || {}) };
            next[type] = value;
            ctx.settings.default_roots = next;
            saveSetting('default_roots', next);
        });
    });
}

function rerenderDefaultRoots(content) {
    const targetSub = content.querySelector('#default-roots-subsection');
    if (!targetSub) return;
    const dirs = ctx.settings.model_roots || [];
    const defaults = ctx.settings.default_roots || {};

    const h3 = targetSub.querySelector('h3');
    const desc = targetSub.querySelector('.settings-desc');
    targetSub.innerHTML = '';
    if (h3) targetSub.appendChild(h3);
    if (desc) targetSub.appendChild(desc);
    targetSub.insertAdjacentHTML('beforeend', renderDefaultRootsRows(dirs, defaults));

    bindDefaultRootSelects(targetSub);
}

// ========== 自动整理路径模板 ==========

function renderTemplateRows() {
    const templates = ctx.settings.organize_path_templates || {};
    return LIBRARY_TYPES.map(t => {
        const tpl = templates[t.key] ?? '';
        const matching = TEMPLATE_PRESETS.find(p => p.value === tpl);
        const selectedValue = matching ? matching.value : '__custom__';
        const presetOptions = TEMPLATE_PRESETS.map(p =>
            `<option value="${esc(p.value)}"${p.value === selectedValue ? ' selected' : ''}>${escHtml(p.label)}</option>`
        ).join('') + `<option value="__custom__"${selectedValue === '__custom__' ? ' selected' : ''}>自定义模板</option>`;

        return `
        <div class="lib-tpl-row" data-type="${esc(t.key)}">
            <div class="lib-tpl-head">
                <span class="lib-tpl-type">${escHtml(t.label)}</span>
                <select class="settings-select lib-tpl-preset">${presetOptions}</select>
            </div>
            <input type="text" class="settings-input lib-tpl-input${selectedValue === '__custom__' ? '' : ' hidden'}"
                   value="${esc(tpl)}" placeholder="不分子目录 = 留空">
            <div class="lib-tpl-preview">预览：<code class="lib-preview-text"></code></div>
        </div>`;
    }).join('');
}

function bindTemplateRows(content) {
    content.querySelectorAll('.lib-tpl-row').forEach(row => {
        const type = row.dataset.type;
        const select = row.querySelector('.lib-tpl-preset');
        const input = row.querySelector('.lib-tpl-input');
        const preview = row.querySelector('.lib-preview-text');

        const currentTemplate = () => select.value === '__custom__' ? input.value : select.value;

        const persist = () => {
            const tpls = { ...(ctx.settings.organize_path_templates || {}) };
            tpls[type] = currentTemplate();
            saveSetting('organize_path_templates', tpls);
            updatePreview();
        };

        const updatePreview = () => {
            const tpl = currentTemplate();
            preview.textContent = tpl
                ? renderPreview(tpl) + '/model.safetensors'
                : 'model.safetensors（不分子目录）';
        };

        select.addEventListener('change', () => {
            if (select.value === '__custom__') {
                input.classList.remove('hidden');
                input.focus();
            } else {
                input.classList.add('hidden');
                input.value = select.value;
                persist();
            }
            updatePreview();
        });
        input.addEventListener('blur', persist);
        input.addEventListener('input', updatePreview);

        updatePreview();
    });
}

// 前端仅做模板预览用的静态代入
function renderPreview(template) {
    const mappings = ctx.settings.base_model_path_mappings || {};
    const bm = 'Flux.1 D';
    const sample = {
        base_model: mappings[bm] || bm,
        first_tag: 'style',
        author: 'authorname',
        creator: 'authorname',
        model_name: 'ExampleModel',
        version_name: 'v1',
        source: 'civitai',
    };
    return template.split('/').map(seg => {
        let out = seg.trim();
        if (!out) return '';
        for (const [k, v] of Object.entries(sample)) {
            out = out.replace('{' + k + '}', v);
        }
        return out.replace(/[<>:"\\|?*]/g, '_');
    }).filter(Boolean).join('/');
}

// ========== 基础模型路径映射 ==========

function renderMappingRowsHtml(mappings) {
    return Object.entries(mappings).map(([bm, folder]) => `
        <div class="mapping-item" data-bm="${esc(bm)}">
            <span class="mapping-bm">${escHtml(bm)}</span>
            <span class="mapping-arrow">→</span>
            <input type="text" class="settings-input mapping-folder" value="${esc(folder)}" placeholder="自定义文件夹名">
            <button class="mapping-remove" title="删除">&times;</button>
        </div>
    `).join('') || '<div class="mapping-empty">暂无映射（保留 base_model 原名）</div>';
}

function renderMappingDropdown(mappings) {
    return MAPPABLE_BASE_MODELS
        .filter(bm => !(bm in mappings))
        .map(bm => `<option value="${esc(bm)}">${escHtml(bm)}</option>`).join('');
}

function bindMappingSection(content) {
    const section = content.querySelector('#section-directories');
    if (!section) return;

    const mappingList = section.querySelector('#mapping-list');
    const addBtn = section.querySelector('#mapping-add-btn');
    const newBmSelect = section.querySelector('#mapping-new-bm');
    const newFolderInput = section.querySelector('#mapping-new-folder');

    const persistMappings = () => {
        const mappings = {};
        mappingList.querySelectorAll('.mapping-item').forEach(item => {
            const bm = item.dataset.bm;
            const folder = item.querySelector('.mapping-folder').value.trim();
            if (bm && folder) mappings[bm] = folder;
        });
        saveSetting('base_model_path_mappings', mappings);
    };

    if (addBtn) {
        addBtn.addEventListener('click', () => {
            const bm = newBmSelect.value;
            const folder = newFolderInput.value.trim();
            if (!bm || !folder) {
                showToast('请选择基础模型并输入文件夹名', 'warning');
                return;
            }
            ctx.settings.base_model_path_mappings = {
                ...(ctx.settings.base_model_path_mappings || {}),
                [bm]: folder,
            };
            rerenderMappings(section);
            persistMappings();
            newFolderInput.value = '';
        });
    }

    rebindMappingRows(section);
}

function rebindMappingRows(section) {
    const list = section.querySelector('#mapping-list');
    if (!list) return;

    list.querySelectorAll('.mapping-remove').forEach(btn => {
        btn.addEventListener('click', () => {
            const item = btn.closest('.mapping-item');
            const bm = item.dataset.bm;
            const mappings = { ...(ctx.settings.base_model_path_mappings || {}) };
            delete mappings[bm];
            ctx.settings.base_model_path_mappings = mappings;
            rerenderMappings(section);
            saveSetting('base_model_path_mappings', mappings);
        });
    });

    list.querySelectorAll('.mapping-folder').forEach(input => {
        input.addEventListener('blur', () => {
            const item = input.closest('.mapping-item');
            const bm = item.dataset.bm;
            const folder = input.value.trim();
            if (!folder) return;
            const mappings = { ...(ctx.settings.base_model_path_mappings || {}) };
            mappings[bm] = folder;
            ctx.settings.base_model_path_mappings = mappings;
            saveSetting('base_model_path_mappings', mappings);
        });
    });
}

function rerenderMappings(section) {
    const mappings = ctx.settings.base_model_path_mappings || {};
    const list = section.querySelector('#mapping-list');
    const select = section.querySelector('#mapping-new-bm');
    if (!list || !select) return;

    list.innerHTML = renderMappingRowsHtml(mappings);
    select.innerHTML = `<option value="">选择基础模型…</option>${renderMappingDropdown(mappings)}`;

    rebindMappingRows(section);
}

// ========== 已识别 base_model 展示 + 刷新 ==========

let _bmStatsWsBound = false;

function renderBmStatsList(stats) {
    if (!stats || stats.length === 0) {
        return '<div class="bm-stats-empty">暂无数据，请先扫描模型</div>';
    }
    return stats.map(s => `
        <div class="bm-stats-item" title="${esc(s.name)}">
            <span class="bm-stats-name">${escHtml(s.name)}</span>
            <span class="bm-stats-count">${s.count}</span>
        </div>
    `).join('');
}

async function loadBaseModelStats(content) {
    const listEl = content.querySelector('#bm-stats-list');
    const metaEl = content.querySelector('#bm-stats-meta');
    if (!listEl) return;
    try {
        const res = await api.fetchBaseModelStats();
        if (!res.success) {
            listEl.innerHTML = `<div class="bm-stats-empty">加载失败：${escHtml(res.error || '')}</div>`;
            return;
        }
        const stats = res.stats || [];
        listEl.innerHTML = renderBmStatsList(stats);
        if (metaEl) {
            const total = stats.reduce((a, b) => a + (b.count || 0), 0);
            metaEl.textContent = `${stats.length} 种 · 共 ${total} 个模型`;
        }
    } catch (e) {
        listEl.innerHTML = `<div class="bm-stats-empty">加载失败：${escHtml(e.message || '')}</div>`;
    }
}

function bindBaseModelStatsSection(content) {
    const btn = content.querySelector('#bm-refresh-btn');
    if (!btn) return;

    // 首次载入数据
    loadBaseModelStats(content);

    btn.addEventListener('click', async () => {
        if (btn.disabled) return;
        btn.disabled = true;
        btn.textContent = '刷新中…';
        try {
            const res = await api.refreshBaseModels();
            if (!res.success) {
                showToast(res.error === 'busy' ? '有操作正在进行中，请稍后再试' : '启动刷新失败: ' + (res.error || ''),
                          res.error === 'busy' ? 'warning' : 'error');
                btn.disabled = false;
                btn.textContent = '刷新 base_model';
                return;
            }
            showToast('已开始刷新，处理过程较慢，请耐心等待', 'info');
        } catch (e) {
            showToast('启动刷新失败: ' + e.message, 'error');
            btn.disabled = false;
            btn.textContent = '刷新 base_model';
        }
    });

    // 进度事件只绑一次（整个会话期间共享）
    if (_bmStatsWsBound) return;
    _bmStatsWsBound = true;

    api.onWsEvent('refresh_base_models_progress', (msg) => {
        const liveBtn = document.getElementById('bm-refresh-btn');
        if (msg.stage === 'complete') {
            if (liveBtn) { liveBtn.disabled = false; liveBtn.textContent = '刷新 base_model'; }
            showToast(`刷新完成：更新 ${msg.updated || 0}，未变 ${msg.unchanged || 0}，失败 ${msg.failed || 0}`, 'success', 5000);
            const panel = document.getElementById('section-directories');
            if (panel) loadBaseModelStats(panel);
        } else if (msg.stage === 'error') {
            if (liveBtn) { liveBtn.disabled = false; liveBtn.textContent = '刷新 base_model'; }
            showToast('刷新出错: ' + (msg.error || ''), 'error');
        } else if (liveBtn && msg.total > 0) {
            liveBtn.textContent = `刷新中 ${msg.current}/${msg.total}`;
        }
    });
}

// ===== 视频 / 纯 UNet 基础模型列表 =====

function bindDmbmSection(content) {
    const tagBox = content.querySelector('#dmbm-tags');
    const input = content.querySelector('#new-dmbm-input');
    const addBtn = content.querySelector('#add-dmbm-btn');
    if (!tagBox || !addBtn) return;

    if (!Array.isArray(ctx.settings.diffusion_model_base_models)) {
        ctx.settings.diffusion_model_base_models = [];
    }

    tagBox.addEventListener('click', (e) => {
        const btn = e.target.closest('.ext-remove');
        if (!btn) return;
        const val = btn.dataset.dmbm;
        ctx.settings.diffusion_model_base_models =
            ctx.settings.diffusion_model_base_models.filter(v => v !== val);
        btn.parentElement.remove();
        saveSetting('diffusion_model_base_models', ctx.settings.diffusion_model_base_models);
    });

    const addEntry = () => {
        const val = input.value.trim();
        if (!val) return;
        if (ctx.settings.diffusion_model_base_models.includes(val)) return;
        ctx.settings.diffusion_model_base_models.push(val);
        const tag = document.createElement('span');
        tag.className = 'ext-tag';
        tag.innerHTML = `${escHtml(val)}<button class="ext-remove" data-dmbm="${esc(val)}">&times;</button>`;
        tagBox.appendChild(tag);
        input.value = '';
        saveSetting('diffusion_model_base_models', ctx.settings.diffusion_model_base_models);
    };

    addBtn.addEventListener('click', addEntry);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); addEntry(); }
    });
}
