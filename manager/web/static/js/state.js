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
 * 共享状态管理
 */
export const state = {
    // 模型数据
    models: [],
    total: 0,
    page: 1,
    totalPages: 0,

    // 过滤/排序
    currentSearch: '',
    currentFolder: '',
    currentBaseModel: '',
    currentSource: '',
    currentModelType: '',
    currentLoraSubtype: '',  // '' / 'lora' / 'lycoris' / 'dora'（LoRA 家族细分筛选）
    currentTag: '',
    currentLicense: '',
    currentPreviewStatus: '',  // '' / 'missing' / 'failed' / 'complete'
    currentSort: 'file_name',
    currentSortDir: '',

    // 元数据
    folders: [],
    baseModels: [],
    tags: [],

    // 统计
    stats: { total: 0, matched: 0, unmatched: 0 },

    // UI 状态
    isLoading: false,
    isBusy: false,

    // 批量选择
    selectMode: false,
    selectedModels: new Set(),

    // 设置
    settings: {},

    // 侧栏展开状态（文件夹路径 Set）
    expandedFolders: new Set(),
};
