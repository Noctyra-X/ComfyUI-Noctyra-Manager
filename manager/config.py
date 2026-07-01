# ComfyUI-Noctyra-Manager
# Copyright (C) 2026 Noctyra
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
配置管理 — JSON 文件读写 + 全局单例

配置文件路径：插件根目录/manager_config.json
支持 ComfyUI models 子目录自动检测。
"""

import os
import json
import logging

logger = logging.getLogger("noctyra.config")

# 默认配置
DEFAULT_CONFIG = {
    # 模型扫描目录（可配置多个，扫描所有类型的模型）
    "model_roots": [],

    # 每类型默认下载根目录（空串 = 按文件夹名自动匹配）
    # key 为内部类型名: lora / checkpoint / unet(含 diffusion_models) / embedding / vae / controlnet
    "default_roots": {
        "lora": "",
        "checkpoint": "",
        "unet": "",
        "embedding": "",
        "vae": "",
        "controlnet": "",
    },

    # API 配置
    "civitai_api_key": "",
    "huggingface_token": "",

    # CivitAI 来源站点：civitai.com（SFW 前门）/ civitai.red（NSFW 前门）
    # 两站共用同一数据库和 API，仅控制"打开来源页面"等跳转链接的域名
    # .green 是早期单独的 SFW 站，此处保留枚举但默认仍用 .com
    "civitai_source_host": "civitai.com",

    # 缓存
    "cache_dir": "",  # 空则使用默认路径

    # 扫描设置
    "scan_extensions": [".safetensors", ".ckpt", ".pt", ".bin", ".gguf"],
    "skip_hidden_dirs": True,

    # 界面设置
    "theme": "dark",  # dark / light
    "blur_nsfw": True,
    "show_only_sfw": False,  # 仅显示 SFW 模型（过滤掉 nsfw=1 的）
    # 模糊阈值（CivitAI nsfwLevel）: 2=PG13+, 4=R+, 8=X+, 16=XXX only
    "nsfw_blur_threshold": 4,
    "autoplay_video_on_hover": True,  # 悬停时自动播放视频预览
    "canvas_picker_enabled": True,  # ComfyUI 节点图右下角的 Noctyra 模型选择器悬浮按钮
    # 图库 NSFW 独立设置（未设置时回退到全局 blur_nsfw / show_only_sfw / nsfw_blur_threshold）：
    # gallery_blur_nsfw / gallery_show_only_sfw / gallery_nsfw_blur_threshold —— 故意不放默认值，
    # 缺省即"跟随全局"，用户在图库 Tab 显式设置后才独立生效
    "display_density": "default",  # default / compact
    "card_aspect": "3/4",  # 3/4 / 1/1 / 4/3 / 2/3
    "card_info_display": "always",  # always / hover
    "model_name_display": "model_name",  # model_name / file_name
    "show_sidebar": True,
    "sidebar_width": 230,  # 侧栏宽度 px（180-480）
    "sidebar_collapsed": False,  # 侧栏折叠状态
    "page_size": 40,  # 每页加载模型数量

    # 工作流图库设置
    "gallery_page_size": 40,  # 图库每页数量
    "gallery_thumb_size": "medium",  # 缩略图大小: small / medium / large
    "gallery_show_filename": False,  # 卡片下是否显示文件名（默认隐藏，因为自动生成的名字通常是哈希串）
    "workflow_gallery_dir": "",  # 图库图片存储目录（下载/导入落点）；空 = <plugin_dir>/gallery/
    "archive_dir": "",  # 存档/回收目录：软删除时模型文件移到这里（便于打包上传网盘，自行清理）；空 = <plugin_dir>/archive/
    # Billfish 文件夹模型：注册的真实文件夹列表，原地索引不拷贝。
    # 每项 {path, name, enabled}；内置的 workflow_gallery_dir 由 gallery_folders 属性自动并入。
    "gallery_folders": [],

    # 代理设置
    "proxy_enabled": False,
    "proxy_host": "",
    "proxy_port": "",
    "proxy_type": "http",       # http / https / socks5 / socks5h（socks 走 aiohttp_socks）
    "proxy_username": "",        # 代理认证用户名（留空=无认证）
    "proxy_password": "",        # 代理认证密码（设置接口按密钥脱敏）

    # 服务端口（独立运行时）
    "server_port": 8199,

    # 独立模式：检测到 ComfyUI（端口 8188 上的 Noctyra 路由）启动后自动退出，
    # 避免两个进程同时跑引起困惑（CLI --no-auto-shutdown 可关闭）
    "auto_shutdown_on_comfyui": True,

    # CivArchive 兜底：CivitAI 返回 404 时自动查 civarchive.com，用于已从
    # CivitAI 删除的模型仍能取到元数据。关闭则只用 CivitAI
    "enable_civarchive_fallback": True,

    # 启动后台自动查模型更新：开启后启动会自动检查一遍(24h TTL，限流安全)，
    # 头部"检查更新"按钮上常驻显示可更新数量。关闭则只在手动点击时检查
    "auto_check_updates": True,
    # 抢先体验(Early Access)版本不计入"有更新"：最新版仍在抢先期时不提醒，
    # 取最近的一个公开版本比较。关闭则抢先体验版也算更新
    "hide_early_access_updates": True,

    # 自动整理：按模型类型分别定义路径模板
    # 支持占位符: {base_model} {first_tag} {author} {creator} {model_name} {version_name} {source}
    # {creator} 是 {author} 的别名，便于与 CivitAI 字段名对齐
    # 空字符串 = 不分子目录（直接放在类型根目录下）
    "organize_path_templates": {
        "lora": "{base_model}",
        "checkpoint": "",
        "embedding": "{base_model}",
        "vae": "",
        "controlnet": "{base_model}",
        "upscale": "",
        "clip": "",
        "unet": "{base_model}",
        "hypernetwork": "{base_model}",
    },
    # base_model 名 -> 自定义文件夹名（优先于模板里的 {base_model}）
    # 例: {"SD 1.5": "sd15", "Flux.1 D": "flux"}
    "base_model_path_mappings": {},

    # 以下 base_model 在 CivitAI 上被分类为 "Checkpoint" 时，整理/下载时强制按 UNet 处理
    # （视频模型、纯 transformer 等和传统 Checkpoint 结构不同，应该落在 diffusion_models/unet 下）
    # 借鉴 ComfyUI-Lora-Manager 的 DIFFUSION_MODEL_BASE_MODELS；匹配为"子串、大小写不敏感"
    # Flux 默认不在此列（Flux 既有 UNet-only 也有 full checkpoint），用户按需添加
    "diffusion_model_base_models": [
        "Wan Video", "Wan 2.1", "Wan 2.2",
        "CogVideo", "CogVideoX",
        "Mochi",
        "Qwen Image",
        "ZImage", "ZImageTurbo",
        "Hunyuan Video",
        "LTXV",
        "HiDream",
        "Krea",   # Flux.1 Krea / Krea 2：CivitAI 标 Checkpoint，但发布形态基本是 UNet-only 扩散模型
    ],
}


def _detect_comfyui_model_dirs() -> list:
    """自动检测 ComfyUI models 目录下所有子目录"""
    # 从插件目录推算: custom_nodes/ComfyUI-Noctyra-Manager -> ComfyUI/models
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    comfyui_dir = os.path.dirname(os.path.dirname(plugin_dir))
    models_dir = os.path.join(comfyui_dir, "models")

    if not os.path.isdir(models_dir):
        return []

    dirs = []
    for name in sorted(os.listdir(models_dir)):
        full_path = os.path.join(models_dir, name)
        if os.path.isdir(full_path) and not name.startswith("."):
            dirs.append(full_path)
    return dirs


class Config:
    """全局配置"""

    def __init__(self, config_path: str = None, plugin_dir: str = None):
        # 是否在 __init__ 中检测到"插件被整体拷到新位置"的场景
        # 由 ModelManager 读取：若为 True 会顺带清理图库里 file_path 失效的老记录
        self.stale_install_reset = False
        self.data_root = ""             # 外部「项目文件夹」绝对路径（空=传统插件目录模式）
        self.data_root_missing = False  # 指针指向的项目文件夹当前不可用（如外置盘未挂）

        # 生产路径（config_path=None）：读插件目录下的指针文件 .noctyra_data_root 决定数据落点。
        # 指针存在且可用 → 配置/库/预览/图库全在该项目文件夹；否则退回传统插件目录。
        if config_path is None:
            self._plugin_dir = plugin_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            pointed = self._read_data_root_pointer(self._plugin_dir)
            if pointed and self._ensure_dir(pointed):
                self.data_root = pointed
                self._config_dir = pointed
                config_path = os.path.join(pointed, "manager_config.json")
            elif pointed:
                # 项目文件夹用不了（外置盘未挂载？）→ 退回插件本地目录，高声警告、绝不清数据
                self.data_root_missing = True
                logger.error(
                    "[Noctyra-MM] 项目文件夹 %s 当前不可用，暂用插件本地目录；"
                    "请检查后重启，期间改动不会写入项目文件夹", pointed,
                )
                self._config_dir = self._plugin_dir
                config_path = os.path.join(self._plugin_dir, "manager_config.json")
            else:
                self._config_dir = self._plugin_dir
                config_path = os.path.join(self._plugin_dir, "manager_config.json")
        else:
            self._config_dir = os.path.dirname(config_path)
            self._plugin_dir = self._config_dir

        self._config_path = config_path
        self._data = dict(DEFAULT_CONFIG)

        # 缓存目录默认值：外部项目文件夹模式直接平铺其下（库 + previews/，取消 .cache 隐藏层）；
        # 传统模式仍是 <插件>/.cache
        if self.data_root:
            self._data["cache_dir"] = self.data_root
        else:
            self._data["cache_dir"] = os.path.join(self._config_dir, ".cache")

        self._load()

        if self.data_root:
            # 外部项目文件夹是用户主动设定 → cache_dir 恒等于项目文件夹（忽略库里残留旧值），
            # 并豁免下面「防迁移残留」的重置（那是给传统插件目录模式兜底的）。
            self._data["cache_dir"] = self.data_root
        else:
            # cache_dir 防迁移残留：若指向的不是当前插件目录下的 .cache（典型场景：
            # 插件拆分 / 整个 ComfyUI 目录被移动，老 cache 路径在硬盘上仍存在 isdir=True
            # 检测过不去），重置回默认，避免数据被写到老位置
            configured_cache = (self._data.get("cache_dir") or "").strip()
            expected_cache = os.path.join(self._config_dir, ".cache")
            if configured_cache:
                try:
                    same_root = os.path.normcase(os.path.realpath(configured_cache)).startswith(
                        os.path.normcase(os.path.realpath(self._config_dir)) + os.sep
                    )
                except OSError:
                    same_root = False
                if not same_root:
                    logger.warning(
                        "[Noctyra-MM] cache_dir 指向插件目录外 (%s)，重置为 %s 防止迁移残留",
                        configured_cache, expected_cache,
                    )
                    self._data["cache_dir"] = expected_cache

        # 迁移善后：上次迁移留了清理标记、且现在已进外部模式且新库就绪 → 删迁移前的旧本地数据
        if self.data_root:
            self._run_post_migrate_cleanup()

        # 如果没有配置任何目录，自动检测 ComfyUI models 子目录
        if not self._data.get("model_roots"):
            detected = _detect_comfyui_model_dirs()
            if detected:
                self._data["model_roots"] = detected
                logger.info("[Noctyra-MM] 自动检测到 %d 个模型目录", len(detected))
        else:
            # 已配置但所有根目录都不存在 → 判定 config 陈旧（插件被整体拷到新位置
            # 时常见），备份旧 config 后重置依赖插件位置的所有路径字段
            roots = self._data["model_roots"]
            if all(not os.path.isdir(p) for p in roots):
                detected = _detect_comfyui_model_dirs()
                if detected:
                    try:
                        if os.path.exists(self._config_path):
                            bak = self._config_path + ".bak"
                            import shutil
                            shutil.copy2(self._config_path, bak)
                            logger.warning("[Noctyra-MM] 旧配置已备份到 %s", bak)
                    except OSError as e:
                        logger.warning("[Noctyra-MM] 配置备份失败: %s", e)
                    self._data["model_roots"] = detected
                    self.stale_install_reset = True
                    # 外部项目文件夹模式：库/图库都在项目文件夹里，是权威落点，不参与重置；
                    # 只有传统插件目录模式才把陈旧的 cache_dir / gallery 重置回插件目录。
                    if not self.data_root:
                        # cache_dir 陈旧（数据库文件就在里面）→ 重置到新插件目录下
                        old_cache = self._data.get("cache_dir", "")
                        if old_cache and not os.path.isdir(old_cache):
                            self._data["cache_dir"] = os.path.join(self._config_dir, ".cache")
                        # workflow_gallery_dir 陈旧 → 清空以回退默认
                        old_gallery = (self._data.get("workflow_gallery_dir") or "").strip()
                        if old_gallery and not os.path.isdir(old_gallery):
                            self._data["workflow_gallery_dir"] = ""
                    # default_roots（每类型下载目录）陈旧 → 清空，交回"按文件夹名自动匹配"
                    drs = self._data.get("default_roots") or {}
                    for k, v in list(drs.items()):
                        if v and not os.path.isdir(v):
                            drs[k] = ""
                    logger.warning(
                        "[Noctyra-MM] 已配置的 %d 个模型目录全部不存在（可能是插件被移动位置），"
                        "已自动重新探测到 %d 个新目录，并重置 cache_dir / default_roots / "
                        "workflow_gallery_dir",
                        len(roots), len(detected),
                    )

        # 首次运行：配置文件不存在就写一份默认配置（含自动探测到的模型目录），让不熟悉
        # 配置的用户装完即有现成文件可看/可改，不必先进设置里保存一次才生成
        if not os.path.exists(self._config_path):
            self.save()

    def _load(self):
        """从文件加载配置"""
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
                logger.info("[Noctyra-MM] 配置已加载: %s", self._config_path)
            except Exception as e:
                logger.warning("[Noctyra-MM] 配置加载失败: %s", e)

    def save(self):
        """保存配置到文件（原子写：先写 .tmp 再 os.replace，防断电/中断损坏）"""
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            tmp_path = self._config_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError as e:
                    # 某些文件系统（网络盘 / tmpfs）不支持 fsync，记 warning 便于排查
                    logger.warning("[Noctyra-MM] fsync 失败（可能是网络盘）：%s", e)
            os.replace(tmp_path, self._config_path)
            logger.info("[Noctyra-MM] 配置已保存: %s", self._config_path)
        except Exception as e:
            logger.error("[Noctyra-MM] 配置保存失败: %s", e)
            # 清理残留 .tmp 避免下次读到半成品
            tmp_path = self._config_path + ".tmp"
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    @property
    def model_roots(self) -> list:
        return self._data.get("model_roots", [])

    # ===== 项目文件夹（data_root）指针：插件目录下的 .noctyra_data_root =====
    @staticmethod
    def _pointer_path(plugin_dir: str) -> str:
        return os.path.join(plugin_dir, ".noctyra_data_root")

    def _read_data_root_pointer(self, plugin_dir: str) -> str:
        """读 .noctyra_data_root 指针，返回项目文件夹路径（无/读失败则 ''）。"""
        try:
            p = self._pointer_path(plugin_dir)
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except OSError:
            pass
        return ""

    @staticmethod
    def _ensure_dir(path: str) -> bool:
        try:
            os.makedirs(path, exist_ok=True)
            return os.path.isdir(path)
        except OSError:
            return False

    def write_data_root_pointer(self, data_root: str):
        """写/删指针文件。data_root 为空 → 删除指针（下次启动回退传统插件目录模式）。

        仅改指针，不动数据；实际生效在下次启动（由 __init__ 引导读取）。"""
        p = self._pointer_path(self._plugin_dir)
        if data_root:
            with open(p, "w", encoding="utf-8") as f:
                f.write(data_root)
        elif os.path.isfile(p):
            os.remove(p)

    @staticmethod
    def _norm(p: str) -> str:
        try:
            return os.path.normcase(os.path.normpath(os.path.abspath(p)))
        except (OSError, ValueError):
            return p

    def migrate_to(self, target: str) -> dict:
        """把数据迁到项目文件夹 target，并写指针（下次启动生效，不热切换）。

        target 已有 model_cache.sqlite → 采用模式（只写指针，你已把项目文件夹拷过来）；
        否则 搬入模式：复制 配置+库+previews+thumbs+gallery 到 target，校验库后写指针，
        并记录旧的插件本地数据待下次启动清理（运行时旧库开着，不当场删）。
        返回 {success, mode, restart, ...}。"""
        import shutil
        if not target or not target.strip():
            return {"success": False, "error": "路径为空"}
        target = os.path.abspath(target.strip())
        if not self._ensure_dir(target):
            return {"success": False, "error": "目标文件夹无法创建或访问"}
        if self.data_root and self._norm(self.data_root) == self._norm(target):
            return {"success": False, "error": "已经是当前项目文件夹"}

        target_db = os.path.join(target, "model_cache.sqlite")
        if os.path.isfile(target_db):
            self.write_data_root_pointer(target)
            return {"success": True, "mode": "adopt", "restart": True, "target": target}

        src_cache = self.cache_dir
        src_config = self._config_path
        src_gallery = self.workflow_gallery_dir
        copied = []
        try:
            if os.path.isfile(src_config):
                shutil.copy2(src_config, os.path.join(target, "manager_config.json"))
                copied.append("config")
            for suffix in ("", "-wal", "-shm"):
                sp = os.path.join(src_cache, "model_cache.sqlite" + suffix)
                if os.path.isfile(sp):
                    shutil.copy2(sp, os.path.join(target, "model_cache.sqlite" + suffix))
            if os.path.isfile(target_db):
                copied.append("db")
            for sub in ("previews", "thumbs"):
                sd = os.path.join(src_cache, sub)
                if os.path.isdir(sd):
                    shutil.copytree(sd, os.path.join(target, sub), dirs_exist_ok=True)
                    copied.append(sub)
            if os.path.isdir(src_gallery) and self._norm(src_gallery) != self._norm(os.path.join(target, "gallery")):
                shutil.copytree(src_gallery, os.path.join(target, "gallery"), dirs_exist_ok=True)
                copied.append("gallery")
        except OSError as e:
            return {"success": False, "error": "复制失败：%s（未改指针，数据原样在旧位置）" % e}

        if not os.path.isfile(target_db):
            return {"success": False, "error": "库复制后校验失败，已中止（未改指针，旧数据完好）"}

        # 仅记录"插件目录内"的旧本地数据待清理；写指针让下次启动从 target 读
        self._write_cleanup_marker([src_cache, src_gallery])
        self.write_data_root_pointer(target)
        return {"success": True, "mode": "move", "restart": True, "target": target, "copied": copied}

    def _write_cleanup_marker(self, paths):
        marker = os.path.join(self._plugin_dir, ".noctyra_cleanup_after_migrate")
        try:
            with open(marker, "w", encoding="utf-8") as f:
                json.dump([p for p in paths if p], f)
        except OSError:
            pass

    def _run_post_migrate_cleanup(self):
        """下次启动：确认已进外部模式且新库就绪后，删除迁移前的旧本地数据。

        安全护栏：只删「插件目录内、且不在项目文件夹内」的路径，逐个 try/except。"""
        marker = os.path.join(self._plugin_dir, ".noctyra_cleanup_after_migrate")
        if not os.path.isfile(marker):
            return
        new_db = os.path.join(self.data_root, "model_cache.sqlite") if self.data_root else ""
        if not (self.data_root and os.path.isfile(new_db)):
            return  # 新位置还没就绪，留着标记下次再试，绝不冒进删源
        try:
            with open(marker, "r", encoding="utf-8") as f:
                paths = json.load(f)
        except (OSError, ValueError):
            paths = []
        import shutil
        plugin_n = self._norm(self._plugin_dir)
        droot_n = self._norm(self.data_root)
        for p in (paths or []):
            try:
                pn = self._norm(p)
                if not pn.startswith(plugin_n + os.sep):
                    continue  # 护栏：只清插件目录内的旧本地数据
                if pn == droot_n or pn.startswith(droot_n + os.sep):
                    continue  # 决不删项目文件夹
                if os.path.isfile(p):
                    os.remove(p)
                elif os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
        try:
            os.remove(marker)
        except OSError:
            pass

    @property
    def plugin_dir(self) -> str:
        return self._plugin_dir

    @property
    def cache_dir(self) -> str:
        return self._data.get("cache_dir", "")

    @property
    def workflow_gallery_dir(self) -> str:
        """图库存储目录（下载/导入落点）：用户自定义 > <plugin_dir>/gallery/"""
        custom = (self._data.get("workflow_gallery_dir") or "").strip()
        if custom:
            return custom
        return os.path.join(self._config_dir, "gallery")

    @property
    def archive_dir(self) -> str:
        """存档/回收目录：软删除时把模型文件移到这里（便于打包上传网盘，用完自行清理；
        下次把文件放回任意模型目录、扫描即按 sha256 自动归位）。用户自定义 > <plugin_dir>/archive/。
        必须在模型扫描根之外，否则扫描会把存档当成在库模型重新收录（保存设置时已校验）。"""
        custom = (self._data.get("archive_dir") or "").strip()
        if custom:
            return custom
        return os.path.join(self._config_dir, "archive")

    @property
    def gallery_folders(self) -> list:
        """Billfish 注册文件夹列表，规范化为 [{path, name, enabled, builtin}]。

        始终把「下载/导入落点」(workflow_gallery_dir) 作为内置文件夹排在最前，
        这样旧的托管库数据照常出现、且不可删除（builtin=True）。
        用户注册的文件夹去重（按规范化绝对路径），跳过与内置重复的。"""
        out = []
        seen = set()

        def _norm(p):
            try:
                return os.path.normcase(os.path.normpath(os.path.abspath(p)))
            except Exception:
                return p

        builtin = self.workflow_gallery_dir
        out.append({
            "path": builtin,
            "name": "下载 / 导入",
            "enabled": True,
            "builtin": True,
        })
        seen.add(_norm(builtin))

        for it in (self._data.get("gallery_folders") or []):
            if not isinstance(it, dict):
                continue
            p = (it.get("path") or "").strip()
            if not p:
                continue
            key = _norm(p)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "path": p,
                "name": (it.get("name") or os.path.basename(p.rstrip("\\/")) or p),
                "enabled": it.get("enabled", True) is not False,
                "builtin": False,
            })
        return out

    @property
    def civitai_api_key(self) -> str:
        return self._data.get("civitai_api_key", "")

    @property
    def huggingface_token(self) -> str:
        return self._data.get("huggingface_token", "")

    @property
    def server_port(self) -> int:
        return self._data.get("server_port", 8199)

    @property
    def scan_extensions(self) -> list:
        return self._data.get("scan_extensions", [".safetensors"])

    @property
    def comfyui_models_dir(self) -> str:
        """ComfyUI 的 models 目录（从插件位置推算）"""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        comfyui_dir = os.path.dirname(os.path.dirname(plugin_dir))
        return os.path.join(comfyui_dir, "models")


# 全局配置单例
_config_instance = None


def get_config(config_path: str = None) -> Config:
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(config_path)
    return _config_instance
