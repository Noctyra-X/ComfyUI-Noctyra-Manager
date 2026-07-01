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
 * 工作流图库渲染器 —— 从 workflows/app.js 拆出。
 * 负责生成 HTML 字符串：生成参数 / ComfyUI 工作流 / A1111 内嵌 / 资源列表。
 */

import { escapeHtml, escapeAttr } from '../utils.js';
import { wfState } from './state.js';


export function parseComfyPrompt(comfy) {
    if (!comfy || !comfy.prompt) return null;
    const nodes = comfy.prompt;
    const result = {};

    const resolveInput = (v) => {
        if (Array.isArray(v)) {
            const srcNode = nodes[String(v[0])];
            return srcNode ? srcNode : null;
        }
        return v;
    };

    for (const [, node] of Object.entries(nodes)) {
        const cls = (node.class_type || '').toLowerCase();
        const inp = node.inputs || {};

        if (cls.includes('ksampler') || cls.includes('sampler')) {
            if (inp.sampler_name && !result.sampler) result.sampler = inp.sampler_name;
            if (inp.steps && !result.steps) result.steps = inp.steps;
            if (inp.cfg && !result.cfg) result.cfg = inp.cfg;
            if (inp.scheduler && !result.scheduler) result.scheduler = inp.scheduler;
            if (inp.denoise !== undefined && inp.denoise !== 1.0) result.denoise = inp.denoise;
            const seedVal = resolveInput(inp.seed);
            if (typeof seedVal === 'number' && !result.seed) result.seed = seedVal;
            if (typeof seedVal === 'object' && seedVal?.inputs?.seed && !result.seed) {
                result.seed = seedVal.inputs.seed;
            }
        }

        if (cls.includes('checkpointloader') || cls.includes('checkpoint_loader') || cls.includes('efficient loader')) {
            if (inp.ckpt_name && !result.model) result.model = inp.ckpt_name;
            if (inp.vae_name && inp.vae_name !== 'Baked VAE' && !result.vae) result.vae = inp.vae_name;
            if (inp.clip_skip !== undefined && !result.clip_skip) result.clip_skip = inp.clip_skip;
        }

        if (cls.includes('cliptextencode') || cls === 'cliptextencodeflux') {
            const text = inp.text || inp.clip_l || '';
            if (typeof text === 'string' && text.length > 10) {
                const title = (node._meta?.title || '').toLowerCase();
                if (title.includes('neg') || title.includes('反')) {
                    if (!result.negative) result.negative = text;
                } else if (!result.prompt || text.length > (result.prompt || '').length) {
                    result.prompt = text;
                }
            }
        }

        if (cls.includes('emptylatent') || cls.includes('empty_latent')) {
            if (inp.width && inp.height && !result.size) {
                result.size = `${inp.width} × ${inp.height}`;
            }
        }

        if (cls.includes('loraloadermodel') || cls === 'loraloader' || cls === 'power lora loader' || cls.includes('lora_loader')) {
            const name = inp.lora_name || inp.lora || '';
            if (name && name !== 'None') {
                if (!result.loras) result.loras = [];
                const strength = inp.strength_model ?? inp.strength ?? 1;
                result.loras.push(`${name} (${strength})`);
            }
        }

        if (cls.includes('lora stacker') || cls.includes('lorastacker') || cls.includes('lora_stacker')) {
            for (const [k, v] of Object.entries(inp)) {
                if (k.startsWith('lora_name') && typeof v === 'string' && v && v !== 'None') {
                    if (!result.loras) result.loras = [];
                    const idx = k.replace('lora_name_', '').replace('lora_name', '');
                    const wKey = idx ? `lora_wt_${idx}` : 'lora_wt';
                    const w = inp[wKey] ?? 1;
                    result.loras.push(`${v} (${w})`);
                }
            }
        }

        if (cls.includes('vaeloader')) {
            if (inp.vae_name && !result.vae) result.vae = inp.vae_name;
        }
    }

    return Object.keys(result).length > 0 ? result : null;
}

// 渲染单行 key/value
function _renderParamRow(label, value, isLong) {
    const text = String(value);
    const cls = isLong ? ' prompt' : '';
    return `<div class="wf-param-row">
        <span class="wf-param-label">${label}</span>
        <span class="wf-param-value${cls}">${escapeHtml(text)}</span>
        <button class="wf-copy-btn" data-text="${escapeAttr(text)}">复制</button>
    </div>`;
}

// 1) 渲染 CivitAI API 返回的扁平字段（A1111 原生图才有）
export function renderCivitaiParamsHtml(meta) {
    if (!meta) return '';
    const fields = [
        ['prompt',               'Prompt',       true],
        ['negativePrompt',       'Negative',     true],
        ['Model',                'Model'],
        ['Model hash',           'Model Hash'],
        ['sampler',              'Sampler'],
        ['Schedule type',        'Schedule'],
        ['steps',                'Steps'],
        ['cfgScale',             'CFG'],
        ['seed',                 'Seed'],
        ['Size',                 'Size'],
        ['Clip skip',            'Clip Skip'],
        ['Denoising strength',   'Denoising'],
        ['Hires upscaler',       'Hires Upscaler'],
        ['Hires upscale',        'Hires Upscale'],
        ['Hires steps',          'Hires Steps'],
        ['VAE',                  'VAE'],
        ['VAE hash',             'VAE Hash'],
        ['Refiner',              'Refiner'],
        ['Refiner switch at',    'Refiner Switch'],
        ['Token merging ratio',  'Token Merging'],
        ['Face restoration',     'Face Restoration'],
        ['Version',              'Version'],
    ];
    const skip = new Set([
        'resources', 'civitaiResources', 'comfy_workflow', 'comfyWorkflow',
        'workflow', 'hashes', 'comfy', 'extra', 'override_settings',
        'override_settings_restore_afterwards', 'is_using_inpainting_conditioning',
        'id', 'meta',
    ]);
    const rendered = new Set();
    let html = '';
    for (const [key, label, isLong] of fields) {
        const value = meta[key];
        if (value === undefined || value === null || value === '') continue;
        rendered.add(key);
        html += _renderParamRow(label, value, isLong);
    }
    // 兜底：其它非对象字段
    for (const [key, val] of Object.entries(meta)) {
        if (rendered.has(key) || skip.has(key)) continue;
        if (key.startsWith('_comfy_')) continue;
        if (val === null || val === undefined || val === '') continue;
        if (typeof val === 'object') continue;
        html += _renderParamRow(escapeHtml(key), val, false);
    }
    return html;
}

// 提取节点图：优先使用下载文件 api_prompt_json，其次 meta.comfy
function _getComfyNodes(img) {
    // img 可能是 {meta, api_prompt_json, workflow_json, ...} 或纯 meta
    const meta = img?.meta ?? img ?? {};

    // 1) 下载文件的 api_prompt（ComfyUI 执行格式）
    if (img?.api_prompt_json) {
        try {
            const data = typeof img.api_prompt_json === 'string'
                ? JSON.parse(img.api_prompt_json)
                : img.api_prompt_json;
            if (data && typeof data === 'object') return data;
        } catch (e) {}
    }

    // 2) CivitAI API 的 meta.comfy
    let c = meta.comfy || (meta.meta && meta.meta.comfy);
    if (typeof c === 'string') {
        try { c = JSON.parse(c); } catch (e) { c = null; }
    }
    if (c && typeof c === 'object') {
        return c.prompt && typeof c.prompt === 'object' ? c.prompt : c;
    }
    return null;
}

// 2) 渲染 ComfyUI 工作流解析结果
export function renderComfyParamsHtml(img) {
    const nodes = _getComfyNodes(img);
    if (!nodes) return '';

    const parsed = parseComfyPrompt({ prompt: nodes });
    if (!parsed) return '';

    const fields = [
        ['prompt',     'Prompt',   true],
        ['negative',   'Negative', true],
        ['model',      'Model'],
        ['sampler',    'Sampler'],
        ['scheduler',  'Scheduler'],
        ['steps',      'Steps'],
        ['cfg',        'CFG'],
        ['seed',       'Seed'],
        ['size',       'Size'],
        ['clip_skip',  'Clip Skip'],
        ['denoise',    'Denoising'],
        ['loras',      'LoRAs'],
        ['vae',        'VAE'],
    ];
    let html = '';
    for (const [key, label, isLong] of fields) {
        let val = parsed[key];
        if (val === undefined || val === null || val === '') continue;
        if (Array.isArray(val)) val = val.join(', ');
        html += _renderParamRow(label, val, isLong);
    }
    return html;
}

// 渲染 A1111 内嵌参数（附加在 CivitAI 数据下方，琥珀色块区分）
export function renderA1111EmbedHtml(img) {
    if (!img || img.embed_source !== 'a1111') return '';
    const parsed = img.parsed_params || {};
    const rawText = img.parameters_text || '';
    if (!rawText && Object.keys(parsed).length === 0) return '';

    const fieldLabels = {
        prompt: 'Prompt', negative_prompt: 'Negative', model: 'Model',
        model_hash: 'Model Hash', sampler: 'Sampler', steps: 'Steps',
        cfg_scale: 'CFG', seed: 'Seed', size: 'Size',
        clip_skip: 'Clip Skip', denoising: 'Denoising',
    };
    let rows = '';
    for (const [key, label] of Object.entries(fieldLabels)) {
        const val = parsed[key];
        if (val === undefined || val === null || val === '') continue;
        const isLong = key === 'prompt' || key === 'negative_prompt';
        rows += _renderParamRow(label, val, isLong);
    }
    if (!rows && rawText) {
        rows = _renderParamRow('原文', rawText, true);
    }
    if (!rows) return '';
    // 和 ComfyUI 工作流区统一成 .wf-section（去掉原来的琥珀色块）；外层 tab 已写明"图片文件内嵌"
    return `<section class="wf-section">
        <header class="wf-section-header">
            <div class="wf-section-header-title">A1111 参数</div>
        </header>
        <div class="wf-section-body">
            <div class="wf-params-content">${rows}</div>
        </div>
    </section>`;
}

// 获取工作流 JSON 原文（用于查看/复制）
export function getWorkflowJsonText(img) {
    if (!img) return '';
    // 优先 workflow_json（editor 格式，更完整）
    if (img.workflow_json) {
        try {
            const data = typeof img.workflow_json === 'string' ? JSON.parse(img.workflow_json) : img.workflow_json;
            return JSON.stringify(data, null, 2);
        } catch (e) {}
    }
    if (img.api_prompt_json) {
        try {
            const data = typeof img.api_prompt_json === 'string' ? JSON.parse(img.api_prompt_json) : img.api_prompt_json;
            return JSON.stringify(data, null, 2);
        } catch (e) {}
    }
    const meta = img.meta || {};
    const c = meta.comfy || (meta.meta && meta.meta.comfy);
    if (c) {
        try {
            const data = typeof c === 'string' ? JSON.parse(c) : c;
            return JSON.stringify(data, null, 2);
        } catch (e) {}
    }
    return '';
}

export function renderResourcesHtml(resources, checkedResults) {
    if (!resources || resources.length === 0) return '';
    return resources.map((r, i) => {
        const weight = r.weight !== undefined ? `权重: ${escapeHtml(String(r.weight))}` : '';
        const checked = checkedResults ? checkedResults[i] : null;
        let statusHtml = '';
        if (checked) {
            if (checked.found) {
                statusHtml = `<span class="wf-res-status wf-res-found" title="${escapeAttr(checked.local_name)}"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>已有</span>`;
            } else {
                const mid = r.modelId || checked.model_id || '';
                const vid = r.modelVersionId || checked.version_id || '';
                statusHtml = `<span class="wf-res-status wf-res-missing"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>缺失</span>`;
                if (mid || vid) {
                    statusHtml += `<button class="wf-btn-sm wf-btn-download-res" data-model-id="${escapeAttr(String(mid))}" data-version-id="${escapeAttr(String(vid))}">下载</button>`;
                }
            }
        }
        const mid = r.modelId || (checked && checked.model_id) || '';
        const vid = r.modelVersionId || (checked && checked.version_id) || '';
        let nameHtml;
        if (r.name) {
            nameHtml = escapeHtml(r.name);
        } else if (vid) {
            nameHtml = `<span style="color:var(--text-muted)">未知名称 · v${escapeHtml(String(vid))}</span>`;
        } else {
            nameHtml = `<span style="color:var(--text-muted)">(无名称)</span>`;
        }

        // 本地命中：名字深链到管理器详情（基于 sha256 精确定位）
        // 未命中：名字链到 CivitAI 源页（走 wfState.civitaiHost 动态域名）
        if (checked && checked.found && checked.local_sha256) {
            nameHtml = `<a href="/noctyra-manager?model=${encodeURIComponent(checked.local_sha256)}"
                           target="_blank" rel="noopener" style="color:inherit;"
                           title="在 Noctyra 管理器打开">${nameHtml}</a>`;
        } else if (mid) {
            nameHtml = `<a href="https://${wfState.civitaiHost}/models/${encodeURIComponent(mid)}${vid ? `?modelVersionId=${encodeURIComponent(vid)}` : ''}" target="_blank" rel="noopener" style="color:inherit;">${nameHtml}</a>`;
        }
        return `<div class="wf-resource-item">
            <span class="wf-resource-type">${escapeHtml(r.type || 'model')}</span>
            <span class="wf-resource-name">${nameHtml}</span>
            ${weight ? `<span class="wf-resource-weight">${weight}</span>` : ''}
            ${statusHtml}
        </div>`;
    }).join('');
}

// 复制文本（优先 Clipboard API，回退 execCommand）
async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
    }
}

export function bindCopyButtons(container) {
    container.querySelectorAll('.wf-copy-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await copyText(btn.dataset.text);
            btn.textContent = '✓';
            btn.classList.add('copied');
            setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('copied'); }, 1200);
        });
    });
}
