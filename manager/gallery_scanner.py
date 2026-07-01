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
图库扫描器（Billfish 文件夹模型）—— 原地索引注册的真实文件夹，文件不拷贝。

对每个已启用的注册文件夹递归遍历，把图片/视频以「真实 file_path」入库
（图片顺带扒 ComfyUI workflow / A1111 参数 / 尺寸）。增量：已在库的路径跳过，
扫描后清理注册根内已从磁盘消失的记录。供「扫描」按钮手动触发（不做 watcher）。
"""

import logging
import os

from .image_meta import extract_image_meta

logger = logging.getLogger("noctyra.gallery")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}

# 单次扫描的安全上限，防超大目录把内存/时间打爆
_MAX_FILES = 200000


def _image_dims(path: str):
    """读图片宽高（瀑布流需要原始比例）。失败返回 (None, None)。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None, None


def scan_gallery(db, config, progress=None) -> dict:
    """扫描所有启用的注册文件夹，原地索引新媒体并清理失效记录。

    progress(added, skipped) 可选回调，用于推送进度。
    返回 {added, skipped, pruned, errors, roots}。
    """
    folders = [f for f in config.gallery_folders if f.get("enabled", True)]
    existing = db.gallery_existing_paths()

    added = skipped = errors = 0
    seen_files = 0
    scanned_roots = []
    capped = False

    for folder in folders:
        root = (folder.get("path") or "").strip()
        if not root or not os.path.isdir(root):
            continue
        completed = True
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                is_img = ext in IMAGE_EXTS
                is_vid = ext in VIDEO_EXTS
                if not (is_img or is_vid):
                    continue
                seen_files += 1
                if seen_files > _MAX_FILES:
                    logger.warning("[Noctyra-Gallery] 文件数超过上限 %d，停止扫描", _MAX_FILES)
                    completed = False
                    capped = True
                    break
                fp = os.path.join(dirpath, fn)
                if fp in existing:
                    skipped += 1
                    continue
                try:
                    embed = {}
                    width = height = None
                    if is_img:
                        try:
                            embed = extract_image_meta(fp) or {}
                        except Exception:
                            embed = {}
                        width, height = _image_dims(fp)
                    db.save_workflow_image({
                        "file_path": fp,
                        "file_name": fn,
                        "source": "local",
                        "source_url": "",
                        "civitai_image_id": None,
                        "width": width,
                        "height": height,
                        "nsfw_level": 0,
                        "meta": embed.get("parsed", {}),
                        "resources": [],
                        "has_workflow": bool(embed.get("workflow")),
                        "workflow_json": embed.get("workflow"),
                        "api_prompt_json": embed.get("api_prompt"),
                        "parameters_text": embed.get("parameters", ""),
                        "parsed_params": embed.get("parsed", {}),
                        "embed_source": embed.get("source_type", "none"),
                        "media_type": "video" if is_vid else "image",
                        "source_root": root,
                    })
                    existing.add(fp)
                    added += 1
                    if progress and added % 50 == 0:
                        try:
                            progress(added, skipped)
                        except Exception:
                            pass
                except Exception as e:
                    errors += 1
                    logger.warning("[Noctyra-Gallery] 索引失败 %s: %s", fp, e)
            if not completed:
                break
        # 只有完整走完的根才纳入 prune；触顶时当前根是「部分扫描」，连同后续未扫的根都不纳入，
        # 否则 prune_missing_under 会把"还没扫到"的合法图库记录当失效删掉（数据丢失）
        if completed:
            scanned_roots.append(root)
        if capped:
            break

    pruned = 0
    try:
        pruned = db.prune_missing_under(scanned_roots)
    except Exception as e:
        logger.warning("[Noctyra-Gallery] 清理失效记录失败: %s", e)

    logger.info(
        "[Noctyra-Gallery] 扫描完成: 新增 %d / 跳过 %d / 清理 %d / 错误 %d / 文件夹 %d",
        added, skipped, pruned, errors, len(scanned_roots),
    )
    return {
        "added": added,
        "skipped": skipped,
        "pruned": pruned,
        "errors": errors,
        "roots": len(scanned_roots),
    }


def build_folder_tree(config, dir_counts: dict) -> list:
    """把注册文件夹 + 各目录图片数聚合成嵌套文件夹树。

    dir_counts: {目录绝对路径: 该目录(不含子目录)图片数}（来自 db.gallery_dir_counts()）。
    返回每个注册根一棵树：
      {path, name, builtin, enabled, count(递归), children:[...同结构]}
    count 为递归汇总（含子目录）。只展示「磁盘上存在 或 有图片记录」的子目录。
    """
    def _norm(p):
        try:
            return os.path.normcase(os.path.normpath(os.path.abspath(p)))
        except Exception:
            return p

    # 把 dir_counts 规范化键，便于匹配
    norm_counts = {}
    for d, c in (dir_counts or {}).items():
        norm_counts[_norm(d)] = norm_counts.get(_norm(d), 0) + c

    def _node(abs_dir, name, builtin=False, enabled=True):
        ndir = _norm(abs_dir)
        # 直接位于本目录的图片数
        own = norm_counts.get(ndir, 0)
        children = []
        # 找出 abs_dir 下的直接子目录：既包括磁盘真实子目录，也包括有记录的子目录
        sub_names = set()
        try:
            if os.path.isdir(abs_dir):
                for entry in os.scandir(abs_dir):
                    if entry.is_dir():
                        sub_names.add(entry.name)
        except Exception:
            pass
        # 来自记录但磁盘已无的子目录也补上（避免计数丢失）
        for nd in norm_counts:
            parent = os.path.dirname(nd)
            if parent == ndir and nd != ndir:
                sub_names.add(os.path.basename(nd))
        total = own
        for sn in sorted(sub_names, key=lambda s: s.lower()):
            child = _node(os.path.join(abs_dir, sn), sn)
            if child["count"] > 0 or os.path.isdir(os.path.join(abs_dir, sn)):
                children.append(child)
                total += child["count"]
        return {
            "path": abs_dir,
            "name": name,
            "builtin": builtin,
            "enabled": enabled,
            "count": total,
            "children": children,
        }

    tree = []
    for folder in config.gallery_folders:
        node = _node(
            folder["path"],
            folder.get("name") or os.path.basename(folder["path"].rstrip("\\/")) or folder["path"],
            builtin=folder.get("builtin", False),
            enabled=folder.get("enabled", True),
        )
        tree.append(node)
    return tree
