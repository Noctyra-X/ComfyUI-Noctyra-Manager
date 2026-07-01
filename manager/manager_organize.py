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
ModelManager 的"自动整理"模块（mixin） —— 从 manager.py 拆出。

提供 organize_single / execute_organize / preview_organize / move_model 等方法，
以及它们的 helper（_find_correct_root / _render_path_template / _compute_organize_move 等）。

设计：作为 mixin 由 ModelManager 继承。依赖 self.config / self.db / self.logger，
不直接创建这些对象。
"""

import errno
import logging
import os
import re
import shutil
from typing import Optional

logger = logging.getLogger("noctyra.manager")


def _safe_move(src: str, dst: str):
    """移动文件：优先 os.replace（同盘原子、不会意外覆盖）；跨物理盘（Windows C:/D:）
    os.replace 抛 OSError(EXDEV / WinError 17)，回退"复制到 dst.tmp → 目标盘内 os.replace
    原子改名 → 删源"。这样跨盘也保持原子语义：中途被杀只会留个 .tmp 半成品（源文件完好、
    目标位置绝不出现半截文件），下次整理会先清掉残留 .tmp。调用方需自行确保 dst 不存在。"""
    try:
        os.replace(src, dst)
        return
    except OSError as e:
        if not (getattr(e, "winerror", None) == 17 or e.errno == errno.EXDEV):
            raise
    # 跨盘路径
    tmp_dst = dst + ".tmp"
    if os.path.exists(tmp_dst):
        try:
            os.remove(tmp_dst)  # 清掉上次中断残留的 .tmp，避免 copy 失败
        except OSError:
            pass
    try:
        shutil.copy2(src, tmp_dst)
        os.replace(tmp_dst, dst)   # 目标盘内改名，原子；此刻 dst 才出现，永不为半截
    except OSError:
        if os.path.exists(tmp_dst):
            try:
                os.remove(tmp_dst)
            except OSError:
                pass
        raise
    # 复制 + 原子改名都成功后才删源（万一此处被杀，最坏是 src/dst 并存的完整副本，不丢数据）
    try:
        os.remove(src)
    except OSError:
        pass


class _OrganizeMixin:
    """自动整理相关方法集合。被 ModelManager 继承。"""

    # CivitAI/内部模型类型 → 目录名关键词（用于判断模型是否在正确的根目录下）
    _TYPE_DIR_KEYWORDS = {
        "lora": ["lora"],
        "locon": ["lora"],
        "dora": ["lora"],
        "lycori": ["lora"],
        "checkpoint": ["checkpoint"],
        "embedding": ["embedding", "textual_inversion"],
        "vae": ["vae"],
        "controlnet": ["controlnet"],
        "upscale": ["upscale", "esrgan"],
        "clip": ["clip"],
        "text_encoder": ["text_encoders", "text_encoder"],
        "clip_vision": ["clip_vision"],
        "motion": ["animatediff_models", "motion"],
        "detection": ["ultralytics", "detection"],
        "unet": ["unet", "diffusion_model"],
        "hypernetwork": ["hypernetwork"],
    }

    # CivitAI 模型类型 -> 模板配置里的归一化 key
    _TEMPLATE_TYPE_ALIAS = {
        "lora": "lora", "locon": "lora", "dora": "lora", "lycori": "lora", "lycoris": "lora",
        "checkpoint": "checkpoint", "checkpointmerge": "checkpoint",
        "embedding": "embedding", "textualinversion": "embedding",
        "vae": "vae",
        "controlnet": "controlnet",
        "upscale": "upscale", "upscaler": "upscale",
        "clip": "clip",
        "text_encoder": "text_encoder", "textencoder": "text_encoder",
        "clip_vision": "clip_vision", "clipvision": "clip_vision",
        "motion": "motion", "motionmodule": "motion",
        "detection": "detection",
        "unet": "unet", "diffusion_model": "unet",
        "hypernetwork": "hypernetwork",
    }

    _PLACEHOLDER_RE = re.compile(r"\{(base_model|first_tag|author|creator|model_name|version_name|source)\}")

    @staticmethod
    def _pick_type_raw(model_type: str, civitai_type: str) -> str:
        """在内部 model_type 和 CivitAI 分类中择一返回归一化前的 raw 字符串。

        优先内部 model_type：它由 scanner 从用户实际目录名推断出来，忠实反映用户
        的分类意图；仅在它为空或为兜底 "other" 时回退到 civitai_type。

        避免 CivitAI 把 Flux / SDXL 等全模型标为 "Checkpoint" 导致放在
        diffusion_models/ 的 UNet 被错判成 Checkpoint 并被整理到 checkpoints/。
        """
        mt = (model_type or "").strip().lower()
        ct = (civitai_type or "").strip().lower()
        if mt and mt != "other":
            return mt
        return ct

    def _apply_diffusion_override(self, type_key: str, base_model: str) -> str:
        """视频模型 / 纯 transformer 的 Checkpoint 强制走 UNet 路线。

        借鉴 ComfyUI-Lora-Manager 的 DIFFUSION_MODEL_BASE_MODELS 机制，列表在
        `config.diffusion_model_base_models` 中，用户可自行增删。

        只对 type_key 为 "checkpoint" 时生效，其它类型（lora/vae/...）不受影响。
        匹配为"子串、大小写不敏感"，以宽容 "Wan Video 14B t2v" / "Wan 2.1" 等变体。
        """
        if type_key != "checkpoint" or not base_model:
            return type_key
        overrides = self.config.get("diffusion_model_base_models") or []
        if not overrides:
            return type_key
        bm = base_model.strip().lower()
        for entry in overrides:
            needle = (entry or "").strip().lower()
            if needle and needle in bm:
                return "unet"
        return type_key

    def _find_correct_root(self, roots: list, current_root: str,
                           type_key: str) -> Optional[str]:
        """根据（已解析的）type_key 在 roots 中找合适的根目录。

        若 current_root 已经是该类型的合理容器（关键字命中），直接返回当前根，
        避免 unet ↔ diffusion_models / embeddings ↔ textual_inversion 这类
        同类多文件夹的误移动。
        """
        if not type_key:
            return None
        keywords = self._TYPE_DIR_KEYWORDS.get(type_key)
        if not keywords:
            return None
        current_name = os.path.basename(current_root or "").lower()
        if current_name and any(kw in current_name for kw in keywords):
            return current_root
        for kw in keywords:
            for r in roots:
                if kw in os.path.basename(r).lower():
                    return r
        return None

    def _template_type_key(self, model: dict) -> str:
        """解析模型最终类型 key。优先级由高到低：
           1. user_model_type（用户手动覆盖；任何时候都绝对优先，不再走视频 UNet 覆盖）
           2. model_type（scanner 的 safetensors 结构判定 + 目录名兜底）
           3. civitai_model_type（CivitAI 分类，常被污染所以最后）
           4. 视频 UNet 覆盖（只影响 2/3 走到 "checkpoint" 但 base_model 命中列表时）
        """
        user_override = (model.get("user_model_type") or "").strip().lower()
        if user_override:
            return self._TEMPLATE_TYPE_ALIAS.get(user_override, user_override)

        raw = self._pick_type_raw(
            model.get("model_type") or "",
            model.get("civitai_model_type") or "",
        )
        type_key = self._TEMPLATE_TYPE_ALIAS.get(raw, raw)
        return self._apply_diffusion_override(type_key, model.get("base_model") or "")

    def _render_path_template(self, template: str, model: dict, base_model: str) -> str:
        """把模板字符串代入模型信息，返回相对目录路径（可能为空=扁平）。

        占位符：{base_model} {first_tag} {author} {creator} {model_name} {version_name} {source}
        {creator} 是 {author} 的别名，与 CivitAI 字段名对齐。
        未知/空值会被替换为 'Unknown'，避免出现空段。
        """
        if not template:
            return ""

        mappings = self.config.get("base_model_path_mappings") or {}
        bm_display = mappings.get(base_model, base_model) or "Unknown"

        # first_tag 来源优先级：civitai_tags > hf_tags > trained_words
        first_tag = ""
        for key in ("civitai_tags", "hf_tags", "trained_words"):
            items = model.get(key) or []
            if items and isinstance(items[0], (str, int, float)):
                first_tag = str(items[0]).strip()
                if first_tag:
                    break
        first_tag = first_tag.lower() or "no-tag"

        author_value = (model.get("creator") or "Anonymous").strip() or "Anonymous"
        values = {
            "base_model": bm_display,
            "first_tag": first_tag,
            "author": author_value,
            "creator": author_value,
            "model_name": (model.get("model_name") or "").strip() or os.path.splitext(
                os.path.basename(model.get("file_path", "")))[0],
            "version_name": (model.get("version_name") or "").strip() or "",
            "source": (model.get("source") or "").strip() or "local",
        }

        # 逐段替换并清理，避免出现 `//` 或 `Unknown/` 这类无效段
        segments = []
        for seg in template.replace("\\", "/").split("/"):
            seg = seg.strip()
            if not seg:
                continue
            rendered = seg
            for k, v in values.items():
                rendered = rendered.replace("{" + k + "}", str(v))
            rendered = self._safe_folder_name(rendered)
            if rendered and rendered.lower() not in ("unknown", ""):
                segments.append(rendered)

        return "/".join(segments)

    def _missing_placeholders(self, template: str, model: dict, base_model: str) -> list:
        """找出模板里真实数据缺失的占位符（不接受 Unknown / Anonymous / 文件名等兜底）。

        返回缺失占位符名列表；空列表 = 所有占位符都有真实数据。
        """
        if not template:
            return []
        names = set(self._PLACEHOLDER_RE.findall(template))
        missing = []

        if "base_model" in names:
            bm = (base_model or "").strip()
            if not bm or bm.lower() == "unknown":
                missing.append("base_model")

        if "first_tag" in names:
            has_tag = False
            for key in ("civitai_tags", "hf_tags", "trained_words"):
                items = model.get(key) or []
                if items and isinstance(items[0], (str, int, float)) and str(items[0]).strip():
                    has_tag = True
                    break
            if not has_tag:
                missing.append("first_tag")

        creator_empty = not (model.get("creator") or "").strip()
        if "author" in names and creator_empty:
            missing.append("author")
        if "creator" in names and creator_empty:
            missing.append("creator")

        if "model_name" in names and not (model.get("model_name") or "").strip():
            missing.append("model_name")

        if "version_name" in names and not (model.get("version_name") or "").strip():
            missing.append("version_name")

        if "source" in names and not (model.get("source") or "").strip():
            missing.append("source")

        return missing

    def _compute_organize_move(self, m: dict, roots: list, templates: dict,
                                uncategorized_root: str):
        """针对单个模型计算整理目标，返回 move dict 或 None（无需移动）"""
        fp = m.get("file_path", "")
        if not fp:
            return None

        # 整理目标文件夹与"显示的 base_model"保持一致：直接用已存的 CivitAI 原值，
        # 不再按文件名细化（学 Lora-Manager：不猜）。未匹配的就归 Unknown/。
        base_model = m.get("base_model") or "Unknown"

        fp_nc = os.path.normcase(os.path.normpath(fp))
        current_root = None
        for r in roots:
            r_nc = os.path.normcase(os.path.normpath(r))
            if fp_nc == r_nc or fp_nc.startswith(r_nc + os.sep):
                current_root = r
                break
        if not current_root:
            return None

        type_key = self._template_type_key(m)
        template = templates.get(type_key, "")

        if not type_key:
            target_root = uncategorized_root
            sub_dir = ""
            reason = "unknown_type"
        else:
            # type_key 已经通过 _template_type_key 解析过（含视频 UNet 覆盖）
            correct_root = self._find_correct_root(roots, current_root, type_key)
            if correct_root and os.path.normcase(os.path.normpath(correct_root)) != os.path.normcase(os.path.normpath(current_root)):
                target_root = correct_root
                reason = "type_mismatch"
            else:
                target_root = current_root
                reason = "base_model"

            if template and self._missing_placeholders(template, m, base_model):
                sub_dir = "Unknown"
                if reason == "base_model":
                    reason = "uncategorized"
            else:
                sub_dir = self._render_path_template(template, m, base_model)

        file_name = os.path.basename(fp)
        if sub_dir:
            target_dir = os.path.normpath(os.path.join(target_root, sub_dir.replace("/", os.sep)))
        else:
            target_dir = target_root
        target_path = os.path.join(target_dir, file_name)

        if os.path.normcase(os.path.normpath(fp)) == os.path.normcase(os.path.normpath(target_path)):
            return None

        if os.path.exists(target_path):
            return None

        rel = os.path.relpath(target_dir, os.path.dirname(target_root))
        new_folder = rel.replace("\\", "/")

        return {
            "file_path": fp,
            "target_path": target_path,
            "file_name": file_name,
            "base_model": base_model,
            "folder": m.get("folder", ""),
            "target_folder": new_folder,
            "reason": reason,
        }

    def preview_organize(self, strategy: str = "type") -> list:
        """预览自动整理结果，不执行实际移动

        整理流程：
        1. 模型类型识别不出（lora/checkpoint 分不清）→ 统一归到 `<ComfyUI>/models/Unknown/`
        2. 类型已知且与当前根目录不符（如 LoRA 在 checkpoints/ 下），迁到正确根
        3. 用 `organize_path_templates[type]` 模板算出子目录
        4. 模板里任一占位符缺失真实数据 → 归入正确根下的 `Unknown/` 子目录

        Returns:
            [{"file_path", "target_path", "file_name", "base_model", "folder", "target_folder", "reason"}, ...]
        """
        roots = list(self.config.model_roots)
        templates = self.config.get("organize_path_templates") or {}
        uncategorized_root = os.path.join(self.config.comfyui_models_dir, "Unknown")
        moves = []
        page = 1
        page_size = 500
        processed = 0

        while True:
            batch, total = self.db.get_all(
                filters={}, sort_by="file_name", page=page, page_size=page_size
            )
            if not batch:
                break
            for m in batch:
                mv = self._compute_organize_move(m, roots, templates, uncategorized_root)
                if mv is not None:
                    moves.append(mv)
            processed += len(batch)
            if processed >= total:
                break
            page += 1

        logger.info("[Noctyra-MM] 整理预览: 扫描 %d 个模型，%d 个需要移动", processed, len(moves))
        return moves

    def organize_single(self, file_path: str) -> dict:
        """对单个模型执行整理：算出目标 → 直接移动。

        Returns:
            {"success", "moved", "reason", "target_path", "error"}
        """
        model = self.db.get_by_path(file_path)
        if not model:
            return {"success": False, "moved": False, "error": "模型不存在"}

        roots = list(self.config.model_roots)
        templates = self.config.get("organize_path_templates") or {}
        uncategorized_root = os.path.join(self.config.comfyui_models_dir, "Unknown")

        mv = self._compute_organize_move(model, roots, templates, uncategorized_root)
        if mv is None:
            # 已在目标位置或存在冲突：区分两种情况给个有用提示
            fp_nc = os.path.normcase(os.path.normpath(file_path))
            if not any(
                fp_nc == os.path.normcase(os.path.normpath(r))
                or fp_nc.startswith(os.path.normcase(os.path.normpath(r)) + os.sep)
                for r in roots
            ):
                return {"success": False, "moved": False, "error": "不在任何扫描根内"}
            return {"success": True, "moved": False, "reason": "already_in_place"}

        result = self.execute_organize([mv])
        if result.get("moved", 0) == 1:
            return {
                "success": True,
                "moved": True,
                "reason": mv["reason"],
                "target_path": mv["target_path"],
                "target_folder": mv["target_folder"],
            }
        return {"success": False, "moved": False, "error": "移动失败（目标已存在或 IO 错误）"}

    def execute_organize(self, moves: list) -> dict:
        """执行整理移动"""
        moved = 0
        failed = 0
        moved_src_dirs = set()
        for mv in moves:
            src = mv["file_path"]
            dst = mv["target_path"]
            target_folder = mv["target_folder"]

            try:
                target_dir = os.path.dirname(dst)
                os.makedirs(target_dir, exist_ok=True)

                if os.path.exists(dst):
                    failed += 1
                    continue

                # _safe_move：同盘走 os.replace（原子）；跨盘回退 shutil.move。
                # 用 replace 而非 rename：exists 检查已过，replace 不会意外覆盖
                _safe_move(src, dst)
                self._move_sidecar_files(src, target_dir)
                self.db.update_file_path(src, dst, target_folder)
                moved += 1
                moved_src_dirs.add(os.path.dirname(src))
            except OSError as e:
                logger.error("[Noctyra-MM] 移动文件失败: %s -> %s: %s", src, dst, e)
                failed += 1

        cleaned = self._cleanup_empty_dirs(moved_src_dirs)
        logger.info("[Noctyra-MM] 整理完成: 移动 %d 个, 失败 %d 个, 清理空目录 %d 个", moved, failed, cleaned)
        return {"moved": moved, "failed": failed, "cleaned_dirs": cleaned}

    def _cleanup_empty_dirs(self, dirs: set) -> int:
        """删除整理后变空的源目录（向上递归到扫描根，不删根本身）。返回删除的目录数。"""
        roots = {os.path.normcase(os.path.normpath(os.path.abspath(r)))
                 for r in (self.config.model_roots or [])}
        removed = 0
        for d in sorted(dirs, key=len, reverse=True):  # 深的目录先处理
            cur = d
            while cur:
                norm = os.path.normcase(os.path.normpath(os.path.abspath(cur)))
                if norm in roots or not os.path.isdir(cur):
                    break
                try:
                    if os.listdir(cur):  # 非空 → 停止上溯
                        break
                    os.rmdir(cur)
                    removed += 1
                except OSError:
                    break
                cur = os.path.dirname(cur)
        return removed

    def move_model(self, file_path: str, target_folder: str) -> dict:
        """将模型移动到目标文件夹"""
        model = self.db.get_by_path(file_path)
        if not model:
            return {"success": False, "error": "模型不存在"}

        if model.get("folder", "") == target_folder:
            return {"success": False, "error": "已在目标文件夹中"}

        # 找到该模型所属的扫描根目录
        current_dir = os.path.dirname(file_path)
        current_folder = model.get("folder", "")

        # 回退到扫描根目录
        if current_folder:
            depth = current_folder.replace("\\", "/").count("/") + 1
            root_dir = current_dir
            for _ in range(depth):
                root_dir = os.path.dirname(root_dir)
        else:
            root_dir = current_dir

        file_name = os.path.basename(file_path)
        if target_folder:
            target_dir = os.path.join(root_dir, target_folder)
        else:
            target_dir = root_dir
        target_path = os.path.join(target_dir, file_name)

        # 防止路径穿越：目标路径必须在扫描根目录内（Windows 大小写不敏感）
        norm_root = os.path.normcase(os.path.normpath(root_dir))
        norm_target = os.path.normcase(os.path.normpath(target_path))
        if not norm_target.startswith(norm_root + os.sep) and norm_target != norm_root:
            logger.warning("[Noctyra-MM] 路径穿越尝试被阻止: %s -> %s", file_path, target_folder)
            return {"success": False, "error": "目标路径无效"}

        if os.path.exists(target_path):
            return {"success": False, "error": "目标位置已存在同名文件"}

        try:
            os.makedirs(target_dir, exist_ok=True)
            _safe_move(file_path, target_path)
            self._move_sidecar_files(file_path, target_dir)
            self.db.update_file_path(file_path, target_path, target_folder)
            logger.info("[Noctyra-MM] 移动模型: %s -> %s", file_path, target_path)
            return {"success": True, "new_path": target_path}
        except OSError as e:
            logger.error("[Noctyra-MM] 移动模型失败: %s", e)
            return {"success": False, "error": str(e)}

    def _move_sidecar_files(self, src: str, target_dir: str):
        """移动模型的附属文件（预览图、metadata.json 等同名文件）。

        跳过扩展名属于 scan_extensions 的文件：同 stem 不同扩展名的"另一个真实模型"
        （如 model.safetensors 旁边的 model.ckpt）不应被当 sidecar 搬走——那样会把它
        移动了却不更新其 DB 记录，导致 DB 路径失效。
        """
        src_dir = os.path.dirname(src)
        src_base = os.path.basename(src)
        stem = os.path.splitext(src_base)[0]
        try:
            model_exts = {e.lower() for e in (self.config.scan_extensions or [])}
        except Exception:
            model_exts = {".safetensors", ".ckpt", ".pt", ".bin", ".gguf"}
        for f in os.listdir(src_dir):
            if f == src_base:
                continue
            f_stem, f_ext = os.path.splitext(f)
            if f_ext.lower() in model_exts:
                continue  # 同名的另一个模型文件，不是 sidecar
            if f_stem == stem or f.startswith(stem + "."):
                sidecar_src = os.path.join(src_dir, f)
                sidecar_dst = os.path.join(target_dir, f)
                if not os.path.exists(sidecar_dst):
                    try:
                        _safe_move(sidecar_src, sidecar_dst)
                    except OSError:
                        pass

    @staticmethod
    def _safe_folder_name(name: str) -> str:
        """将 base_model 名转为安全的文件夹名"""
        unsafe = '<>:"/\\|?*'
        result = name.strip()
        for c in unsafe:
            result = result.replace(c, '_')
        return result
