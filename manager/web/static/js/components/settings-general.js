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
 * Settings - 常规 Section: API 密钥、扫描、缓存管理、导入导出、代理
 */
import * as api from '../api.js';
import { showToast } from './toast.js';
import { showConfirm } from './dialog.js';
import { escapeHtml as escHtml, escapeAttr as esc } from '../utils.js';
import { ctx, renderToggle, bindToggle, bindSelect, saveSetting, updateKeyStatus } from './settings-helpers.js';

export function renderGeneralSection() {
    const s = ctx.settings;
    return `
    <div class="settings-section-panel active" id="section-general">
        <div class="settings-subsection">
            <h3>API 密钥</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>CivitAI API Key</span>
                    <span class="setting-hint">用于获取模型信息和预览图</span>
                </div>
                <div class="setting-control">
                    <div class="api-key-input">
                        <input type="text" id="set-civitai-key" value="" placeholder="${s.civitai_api_key === '***' ? '已设置（输入新值可覆盖）' : '未设置'}" class="settings-input">
                        ${s.civitai_api_key === '***' ? '<span class="key-status set">已设置</span>' : ''}
                    </div>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>HuggingFace Token</span>
                    <span class="setting-hint">用于访问 HuggingFace 模型信息</span>
                </div>
                <div class="setting-control">
                    <div class="api-key-input">
                        <input type="text" id="set-hf-token" value="" placeholder="${s.huggingface_token === '***' ? '已设置（输入新值可覆盖）' : '未设置'}" class="settings-input">
                        ${s.huggingface_token === '***' ? '<span class="key-status set">已设置</span>' : ''}
                    </div>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>CivitAI 来源站点</span>
                    <span class="setting-hint">"打开来源页面"等跳转链接用的域名。.com 是 SFW 前门，.red 是 NSFW 前门；两站共用账号与模型库，API 始终走 .com</span>
                </div>
                <div class="setting-control">
                    <select id="set-civitai-source-host" class="settings-select">
                        <option value="civitai.com"${(s.civitai_source_host || 'civitai.com') === 'civitai.com' ? ' selected' : ''}>civitai.com（SFW 前门）</option>
                        <option value="civitai.red"${s.civitai_source_host === 'civitai.red' ? ' selected' : ''}>civitai.red（NSFW 前门）</option>
                    </select>
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>在线匹配</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>CivArchive 兜底</span>
                    <span class="setting-hint">CivitAI 返回 404（模型已从站点删除）时，自动改查 civarchive.com 获取元数据。关闭则只用 CivitAI</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-civarchive-fallback', s.enable_civarchive_fallback !== false)}
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>启动自动查模型更新</span>
                    <span class="setting-hint">启动后台自动检查已匹配模型有无新版本（24h 一次、限流安全），头部"检查更新"按钮上常驻可更新数量。关闭则只在手动点击时检查</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-auto-check-updates', s.auto_check_updates !== false)}
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>抢先体验版不算更新</span>
                    <span class="setting-hint">最新版仍处 Early Access 抢先期时不提示更新，取最近的公开版本比较。关闭则抢先体验版也计入"有更新"</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-hide-early-access', s.hide_early_access_updates !== false)}
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>扫描设置</h3>
            <div class="setting-row setting-row-wide">
                <div class="setting-label">
                    <span>文件扩展名</span>
                    <span class="setting-hint">需要扫描的模型文件格式</span>
                </div>
                <div class="setting-control">
                    <div id="ext-tags" class="ext-tag-list">
                        ${(s.scan_extensions || []).map(ext => `<span class="ext-tag">${escHtml(ext)}<button class="ext-remove" data-ext="${esc(ext)}">&times;</button></span>`).join('')}
                    </div>
                    <div class="ext-add-row">
                        <input type="text" id="new-ext-input" placeholder=".safetensors" class="settings-input settings-input-sm">
                        <button id="add-ext-btn" class="btn btn-sm">添加</button>
                    </div>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>跳过隐藏目录</span>
                    <span class="setting-hint">忽略以 . 开头的文件夹</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-skip-hidden', s.skip_hidden_dirs)}
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>缓存管理</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>缓存占用</span>
                    <span class="setting-hint">预览图（原图）与列表卡片缩略图各自占用</span>
                </div>
                <div class="setting-control">
                    <span id="cache-stats-line" class="setting-hint">统计中…</span>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>预缓存全部图片</span>
                    <span class="setting-hint">将所有模型的封面、头像、描述图提前下载到本地，便于离线浏览（耗时较长）</span>
                </div>
                <div class="setting-control">
                    <button class="btn btn-sm" id="set-prewarm-previews">开始预缓存</button>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>清理预览图缓存</span>
                    <span class="setting-hint">删除不再被任何模型引用的预览图缓存文件</span>
                </div>
                <div class="setting-control">
                    <button class="btn btn-sm" id="set-cleanup-previews">清理</button>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>清空缩略图缓存</span>
                    <span class="setting-hint">删除全部列表卡片缩略图。缩略图可再生，下次浏览会自动重建</span>
                </div>
                <div class="setting-control">
                    <button class="btn btn-sm" id="set-clear-thumbs">清空</button>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>重建缓存</span>
                    <span class="setting-hint">清空数据库并重新扫描所有模型（用于数据损坏时恢复）</span>
                </div>
                <div class="setting-control">
                    <button class="btn btn-sm btn-danger" id="set-rebuild-cache">重建缓存</button>
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>导入导出</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>导出数据</span>
                    <span class="setting-hint">将所有模型元数据导出为 JSON 文件</span>
                </div>
                <div class="setting-control">
                    <button class="btn btn-sm" id="set-export">导出</button>
                </div>
            </div>
            <div class="setting-row">
                <div class="setting-label">
                    <span>导入数据</span>
                    <span class="setting-hint">从 JSON 文件导入元数据（按 SHA256 匹配本地模型）</span>
                </div>
                <div class="setting-control">
                    <input type="file" id="set-import-file" accept=".json" style="display:none">
                    <button class="btn btn-sm" id="set-import">导入</button>
                </div>
            </div>
        </div>

        <div class="settings-subsection">
            <h3>代理设置</h3>
            <div class="setting-row">
                <div class="setting-label">
                    <span>启用代理</span>
                    <span class="setting-hint">通过代理访问 CivitAI / HuggingFace（关闭时回退系统代理 / 环境变量）</span>
                </div>
                <div class="setting-control">
                    ${renderToggle('set-proxy-enabled', s.proxy_enabled)}
                </div>
            </div>
            <div class="setting-row proxy-detail${s.proxy_enabled ? '' : ' hidden'}">
                <div class="setting-label">
                    <span>类型</span>
                    <span class="setting-hint">Clash / v2ray 的混合端口选 HTTP 即可；SOCKS5 经 aiohttp_socks 支持</span>
                </div>
                <div class="setting-control">
                    <select id="set-proxy-type" class="settings-select">
                        <option value="http"${(s.proxy_type || 'http') === 'http' ? ' selected' : ''}>HTTP</option>
                        <option value="https"${s.proxy_type === 'https' ? ' selected' : ''}>HTTPS</option>
                        <option value="socks5"${s.proxy_type === 'socks5' ? ' selected' : ''}>SOCKS5</option>
                    </select>
                </div>
            </div>
            <div class="setting-row proxy-detail${s.proxy_enabled ? '' : ' hidden'}">
                <div class="setting-label"><span>代理地址</span></div>
                <div class="setting-control proxy-addr-row">
                    <input type="text" id="set-proxy-host" value="${esc(s.proxy_host)}" placeholder="127.0.0.1" class="settings-input settings-input-sm">
                    <span class="proxy-colon">:</span>
                    <input type="text" id="set-proxy-port" value="${esc(s.proxy_port)}" placeholder="7897" class="settings-input settings-input-xs">
                </div>
            </div>
            <div class="setting-row proxy-detail${s.proxy_enabled ? '' : ' hidden'}">
                <div class="setting-label">
                    <span>账号密码</span>
                    <span class="setting-hint">需要认证的代理才填，留空 = 无认证</span>
                </div>
                <div class="setting-control proxy-addr-row">
                    <input type="text" id="set-proxy-username" value="${esc(s.proxy_username)}" placeholder="用户名（可选）" class="settings-input settings-input-sm" autocomplete="off">
                    <input type="password" id="set-proxy-password" value="" placeholder="${s.proxy_password === '***' ? '已设置（输入可覆盖）' : '密码（可选）'}" class="settings-input settings-input-sm" autocomplete="new-password">
                </div>
            </div>
        </div>
    </div>`;
}

export function bindGeneralEvents(content, closeSettings) {
    // API key 保存 (blur) — 只有输入了新值才保存
    const civitaiKey = content.querySelector('#set-civitai-key');
    if (civitaiKey) {
        civitaiKey.addEventListener('blur', () => {
            const val = civitaiKey.value.trim();
            if (val) {
                saveSetting('civitai_api_key', val);
                civitaiKey.value = '';
                civitaiKey.placeholder = '已设置（输入新值可覆盖）';
                updateKeyStatus(content, '#set-civitai-key');
                showToast('CivitAI API Key 已保存', 'success');
            }
        });
    }

    const hfToken = content.querySelector('#set-hf-token');
    if (hfToken) {
        hfToken.addEventListener('blur', () => {
            const val = hfToken.value.trim();
            if (val) {
                saveSetting('huggingface_token', val);
                hfToken.value = '';
                hfToken.placeholder = '已设置（输入新值可覆盖）';
                updateKeyStatus(content, '#set-hf-token');
                showToast('HuggingFace Token 已保存', 'success');
            }
        });
    }

    // 扫描扩展名
    const extTags = content.querySelector('#ext-tags');
    if (extTags) {
        extTags.addEventListener('click', e => {
            const removeBtn = e.target.closest('.ext-remove');
            if (removeBtn) {
                const ext = removeBtn.dataset.ext;
                ctx.settings.scan_extensions = ctx.settings.scan_extensions.filter(e => e !== ext);
                removeBtn.parentElement.remove();
                saveSetting('scan_extensions', ctx.settings.scan_extensions);
            }
        });
    }

    const addExtBtn = content.querySelector('#add-ext-btn');
    if (addExtBtn) {
        addExtBtn.addEventListener('click', () => {
            const input = content.querySelector('#new-ext-input');
            let ext = input.value.trim().toLowerCase();
            if (!ext) return;
            if (!ext.startsWith('.')) ext = '.' + ext;
            if (ctx.settings.scan_extensions.includes(ext)) return;
            ctx.settings.scan_extensions.push(ext);
            const tag = document.createElement('span');
            tag.className = 'ext-tag';
            tag.innerHTML = `${escHtml(ext)}<button class="ext-remove" data-ext="${esc(ext)}">&times;</button>`;
            extTags.appendChild(tag);
            input.value = '';
            saveSetting('scan_extensions', ctx.settings.scan_extensions);
        });
    }

    // 预缓存全部图片
    const prewarmBtn = content.querySelector('#set-prewarm-previews');
    if (prewarmBtn) {
        prewarmBtn.addEventListener('click', async () => {
            if (prewarmBtn.disabled) return;
            prewarmBtn.disabled = true;
            prewarmBtn.textContent = '入队中...';
            try {
                const res = await api.prewarmPreviews();
                if (!res.success) {
                    showToast(res.error === 'busy' ? '有操作正在进行中，请稍后再试' : '启动失败: ' + (res.error || ''),
                        res.error === 'busy' ? 'warning' : 'error');
                } else {
                    const q = res.queued || 0, c = res.cached || 0, d = res.dead || 0;
                    if (q === 0) {
                        showToast(`预览图已全部就绪：${c} 张已缓存${d ? `，${d} 张死链已跳过` : ''}`, 'success', 5000);
                    } else {
                        showToast(`已加入后台预缓存：待下载 ${q} 张（已缓存 ${c}${d ? `，死链 ${d}` : ''}）。后台慢慢下，可关闭设置`, 'info', 6000);
                    }
                }
            } catch (e) {
                showToast('预缓存出错: ' + e.message, 'error');
            } finally {
                prewarmBtn.disabled = false;
                prewarmBtn.textContent = '开始预缓存';
            }
        });
    }

    // 清理预览图缓存
    const cleanupBtn = content.querySelector('#set-cleanup-previews');
    if (cleanupBtn) {
        cleanupBtn.addEventListener('click', async () => {
            cleanupBtn.disabled = true;
            cleanupBtn.textContent = '清理中...';
            try {
                const res = await api.cleanupPreviews();
                if (res.success) {
                    const n = res.removed || 0;
                    showToast(n > 0 ? `已清理 ${n} 个未引用的缓存文件` : '没有需要清理的缓存', 'success');
                } else if (res.error === 'unsafe') {
                    const pct = Math.round(res.matched / res.total * 100);
                    if (await showConfirm({ title: '强制清理缓存', danger: true, okText: '强制清理', message: `当前只有 ${pct}% 的模型已匹配（${res.matched}/${res.total}），大部分预览图引用尚未建立。\n现在清理可能会删除仍需要的缓存，匹配后需重新下载。\n\n确定要强制清理吗？` })) {
                        const forceRes = await api.cleanupPreviews(true);
                        if (forceRes.success) {
                            const n = forceRes.removed || 0;
                            showToast(n > 0 ? `已强制清理 ${n} 个缓存文件` : '没有需要清理的缓存', 'success');
                        } else {
                            showToast('清理失败: ' + (forceRes.error || ''), 'error');
                        }
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

    // 缓存占用统计（异步填充）
    const fmtBytes = (b) => {
        b = b || 0;
        if (b < 1024) return b + ' B';
        const u = ['KB', 'MB', 'GB'];
        let i = -1;
        do { b /= 1024; i++; } while (b >= 1024 && i < u.length - 1);
        return b.toFixed(b < 10 ? 1 : 0) + ' ' + u[i];
    };
    const statsLine = content.querySelector('#cache-stats-line');
    const loadCacheStats = async () => {
        if (!statsLine) return;
        try {
            const res = await api.getCacheStats();
            const st = (res && res.stats) || {};
            statsLine.textContent =
                `预览图 ${fmtBytes(st.preview_bytes)}（${st.preview_count || 0}）· 缩略图 ${fmtBytes(st.thumb_bytes)}（${st.thumb_count || 0}）`;
        } catch (_) {
            statsLine.textContent = '统计失败';
        }
    };
    loadCacheStats();

    // 清空缩略图缓存
    const clearThumbsBtn = content.querySelector('#set-clear-thumbs');
    if (clearThumbsBtn) {
        clearThumbsBtn.addEventListener('click', async () => {
            clearThumbsBtn.disabled = true;
            clearThumbsBtn.textContent = '清空中...';
            try {
                const res = await api.clearThumbs();
                const n = (res && res.removed) || 0;
                showToast(n > 0 ? `已清空 ${n} 个缩略图` : '没有缩略图需要清空', 'success');
                loadCacheStats();
            } catch (e) {
                showToast('清空出错: ' + e.message, 'error');
            } finally {
                clearThumbsBtn.disabled = false;
                clearThumbsBtn.textContent = '清空';
            }
        });
    }

    // CivArchive 兜底开关
    bindToggle(content, 'set-civarchive-fallback', 'enable_civarchive_fallback');
    // 模型更新检查开关
    bindToggle(content, 'set-auto-check-updates', 'auto_check_updates');
    bindToggle(content, 'set-hide-early-access', 'hide_early_access_updates');

    // 重建缓存
    const rebuildBtn = content.querySelector('#set-rebuild-cache');
    if (rebuildBtn) {
        rebuildBtn.addEventListener('click', async () => {
            if (!await showConfirm({ title: '重建缓存', danger: true, okText: '清空重建', message: '确认要清空缓存并重新扫描所有模型？\n已有的匹配信息将全部丢失，需要重新在线匹配。' })) return;
            rebuildBtn.disabled = true;
            rebuildBtn.textContent = '重建中...';
            try {
                const res = await api.triggerRebuild();
                if (res.success) {
                    showToast('重建缓存已启动，请等待扫描完成', 'info');
                    if (closeSettings) closeSettings();
                } else {
                    showToast(res.error === 'busy' ? '有操作进行中，请稍后再试' : '重建失败: ' + (res.error || ''),
                        res.error === 'busy' ? 'warning' : 'error');
                    rebuildBtn.disabled = false;
                    rebuildBtn.textContent = '重建缓存';
                }
            } catch (e) {
                showToast('重建出错: ' + e.message, 'error');
                rebuildBtn.disabled = false;
                rebuildBtn.textContent = '重建缓存';
            }
        });
    }

    // 导出
    const exportBtn = content.querySelector('#set-export');
    if (exportBtn) {
        exportBtn.addEventListener('click', async () => {
            exportBtn.disabled = true;
            exportBtn.textContent = '导出中...';
            try {
                const data = await api.exportData();
                if (!data.models) {
                    showToast('导出失败', 'error');
                    return;
                }
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `noctyra-export-${new Date().toISOString().slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(url);
                showToast(`已导出 ${data.count} 个模型`, 'success');
            } catch (e) {
                showToast('导出出错: ' + e.message, 'error');
            } finally {
                exportBtn.disabled = false;
                exportBtn.textContent = '导出';
            }
        });
    }

    // 导入
    const importBtn = content.querySelector('#set-import');
    const importFile = content.querySelector('#set-import-file');
    if (importBtn && importFile) {
        importBtn.addEventListener('click', () => importFile.click());
        importFile.addEventListener('change', async () => {
            const file = importFile.files[0];
            if (!file) return;
            importBtn.disabled = true;
            importBtn.textContent = '导入中...';
            try {
                const text = await file.text();
                const data = JSON.parse(text);
                const models = data.models || [];
                if (models.length === 0) {
                    showToast('文件中没有模型数据', 'warning');
                    return;
                }
                const res = await api.importData(models, 'merge');
                if (res.success) {
                    showToast(`导入完成: 更新 ${res.updated} 个, 跳过 ${res.skipped} 个`, 'success');
                } else {
                    showToast('导入失败: ' + (res.error || ''), 'error');
                }
            } catch (e) {
                showToast('导入出错: ' + e.message, 'error');
            } finally {
                importBtn.disabled = false;
                importBtn.textContent = '导入';
                importFile.value = '';
            }
        });
    }

    // 隐藏目录开关
    bindToggle(content, 'set-skip-hidden', 'skip_hidden_dirs');

    // CivitAI 来源站点
    bindSelect(content, 'set-civitai-source-host', 'civitai_source_host');

    // 代理开关
    const proxyToggle = content.querySelector('#set-proxy-enabled');
    if (proxyToggle) {
        proxyToggle.addEventListener('change', () => {
            const on = proxyToggle.checked;
            saveSetting('proxy_enabled', on);
            content.querySelectorAll('.proxy-detail').forEach(el => el.classList.toggle('hidden', !on));
        });
    }

    // 代理类型
    bindSelect(content, 'set-proxy-type', 'proxy_type');

    // 代理地址
    const proxyHost = content.querySelector('#set-proxy-host');
    const proxyPort = content.querySelector('#set-proxy-port');
    if (proxyHost) proxyHost.addEventListener('blur', () => saveSetting('proxy_host', proxyHost.value.trim()));
    if (proxyPort) proxyPort.addEventListener('blur', () => saveSetting('proxy_port', proxyPort.value.trim()));

    // 代理认证：用户名空值可清除；密码按密钥处理，只有输入了新值才覆盖（'***' 表示不改）
    const proxyUser = content.querySelector('#set-proxy-username');
    if (proxyUser) proxyUser.addEventListener('blur', () => saveSetting('proxy_username', proxyUser.value.trim()));
    const proxyPass = content.querySelector('#set-proxy-password');
    if (proxyPass) {
        proxyPass.addEventListener('blur', () => {
            const val = proxyPass.value;
            if (val) {
                saveSetting('proxy_password', val);
                proxyPass.value = '';
                proxyPass.placeholder = '已设置（输入可覆盖）';
            }
        });
    }
}
