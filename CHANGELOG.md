# Changelog

本项目的显著改动记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0]

首次公开发布。

- **模型管理**：扫描 checkpoints / LoRA / VAE / ControlNet / embeddings 等，按 sha256 匹配 CivitAI / HuggingFace 元数据（封面、触发词、基础模型、说明），网页界面浏览
- **下载**：粘贴 CivitAI / HuggingFace 链接下载，断点续传、暂停 / 继续 / 重试，完成后自动归类入库
- **更新提醒**：后台检查已匹配模型是否有新版本
- **整理与校验**：按基础模型自动分文件夹；safetensors 完整性检测、一键重下
- **工作流图库**：本地工作流连同预览图 / 视频一起管理
- **画布模型选择器**：ComfyUI 节点上带预览图选择模型，双击看触发词，右键跳详情 / 复制触发词
- **浏览器扩展（Chrome）**：CivitAI 页面标记已下载、一键推送到本地下载
- 可作为 ComfyUI 插件使用，也能脱离 ComfyUI 单独运行
