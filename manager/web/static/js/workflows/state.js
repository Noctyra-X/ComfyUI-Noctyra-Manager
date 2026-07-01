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
 * 工作流页共享状态 —— 跨文件（app.js / renderers.js / detail.js）读写的运行时配置。
 * 用对象字段而非 export let，避免 ES module live binding 的跨文件修改陷阱。
 */

export const wfState = {
    // CivitAI 来源站点：civitai.com / civitai.red（.green 已不常见但也接受）
    // app.js::loadRuntimeInfo() 启动时从后端设置读取并赋值
    civitaiHost: 'civitai.com',

    // 与模型管理器共享的 NSFW 设置（loadRuntimeInfo 从 /api/noctyra/settings 读）
    blurNsfw: true,
    nsfwBlurThreshold: 4,
};

export const API_BASE = '/api/noctyra/workflow';
