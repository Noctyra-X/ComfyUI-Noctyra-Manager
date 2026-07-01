# ComfyUI-Noctyra-Manager

本地模型管理器：扫描本地模型，从 CivitAI / HuggingFace 匹配封面、触发词、基础模型、说明等信息，通过网页界面浏览和管理。可作为 ComfyUI 插件使用，也能脱离 ComfyUI 单独运行。附带 Chrome 扩展和画布上的模型选择器。

## 功能

### 模型管理

扫描 checkpoints / LoRA / VAE / ControlNet / embeddings 等目录，按 sha256 匹配 CivitAI 和 HuggingFace，匹配成功的模型会带上封面、作者、触发词、基础模型和说明。匹配不到的保留为 Unknown，可手动粘贴 CivitAI 或 HF 链接绑定。模型信息支持手动编辑，编辑过的字段会被锁定，重新匹配时不会被线上数据覆盖。

### 下载

粘贴 CivitAI 模型页或 HuggingFace 仓库链接，选择版本和保存目录下载。支持断点续传、暂停 / 恢复 / 重试。下载完成后自动计算哈希、按文件结构归类并匹配元数据（标着 Checkpoint 但实为 UNet 的模型会归入 unet 目录）。

### 更新提醒

启动后在后台检查已匹配模型在 CivitAI 是否有新版本，有更新时在顶部按钮标出数量。抢先体验（Early Access）版本默认不计入更新。

### 整理与校验

- 按基础模型自动分类到子文件夹。
- 扫描时校验 safetensors 完整性，损坏或截断的文件标记出来，可一键重新下载。
- 预览图按 CivitAI 分级模糊，可一键隐藏全部 NSFW 内容。

### 画布模型选择器
在 ComfyUI 节点上选择模型时，弹出带预览图的选择器。双击查看触发词，右键卡片可跳转到管理器详情或复制触发词，LoRA 多个槽位可一次填写。

### 工作流图库

管理本地工作流及其预览图、视频，瀑布流布局浏览。

### 浏览器扩展（Chrome）

浏览 CivitAI 时标出已下载的模型，并显示该模型的版本总数和本地已下载数量。可直接从 CivitAI 页面推送到本地下载。

### 其他

全库触发词汇总、重复模型查找、统计页；批量修改基础模型 / 打标签 / 移动；模型存档（软删除，保留记录，可恢复）。


## 安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Noctyra-X/ComfyUI-Noctyra-Manager.git
cd ComfyUI-Noctyra-Manager
pip install -r requirements.txt
```

重启 ComfyUI，控制台出现 `[ComfyUI-Noctyra-Manager] vX.X.X Loaded` 即表示成功。界面地址为 `http://127.0.0.1:8188/noctyra-manager`，所有配置在右上角设置弹窗中调整。

**单独运行（不依赖 ComfyUI）**

- Windows 便携版：双击 `run_standalone.bat`
- 命令行：`python manager/__main__.py --port 8199`

访问 `http://127.0.0.1:8199/noctyra-manager`。除 ComfyUI 用量统计外，其余功能均可用。

**浏览器扩展**：在 Chrome 打开 `chrome://extensions/`，开启"开发者模式"，加载 `browser-extension/` 目录。


## 更新计划
1. 工作流功能完善。
2. 浏览器拓展功能完善，功能增加。
3. 单独启动器，废弃web端UI。

## 致谢

项目部分思路，参考 [ComfyUI-Lora-Manager](https://github.com/willmiao/ComfyUI-Lora-Manager)。

## 许可证

[GPL-3.0-or-later](LICENSE)
