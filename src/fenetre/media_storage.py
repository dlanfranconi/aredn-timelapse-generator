"""Optional relocation of photo/video storage to a separate media_dir.

Fenetre normally keeps everything under global.work_dir: config-adjacent state
(cameras.json, queues, launch state) as well as the actual photos/timelapses
(work_dir/photos) and launch recordings (work_dir/launches). Some deployments
want the (large, fast-growing) media on a NAS or other external mount while
keeping work_dir itself (and config.yaml, logs) on local/fast storage.

To support that without touching every place that already builds paths as
os.path.join(work_dir, "photos", ...), we keep work_dir/photos and
work_dir/launches as the canonical paths everywhere, but let them be symlinks
into global.media_dir. relocate_media_storage() does the one-time move of any
existing files into media_dir and (re)creates the symlinks; everything else in
the codebase keeps working unmodified because it never has to know whether
work_dir/photos is a real directory or a symlink.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

RELOCATABLE_SUBDIRS = ("photos", "launches")

# Guards relocation against overlapping with an in-progress capture write.
# fenetre.py's snap loop acquires this around the small block where it writes
# a picture under work_dir/photos/<camera>/... .
RELOCATE_LOCK = threading.RLock()


def _validate_new_media_dir(work_dir_abs: str, media_dir_abs: str) -> None:
    if media_dir_abs == work_dir_abs:
        raise ValueError("media_dir must be different from work_dir.")
    for subdir in RELOCATABLE_SUBDIRS:
        source = os.path.join(work_dir_abs, subdir)
        if media_dir_abs == source or media_dir_abs.startswith(source + os.sep):
            raise ValueError(f"media_dir cannot be located inside {source}.")


def _merge_move_tree(src: str, dst: str) -> None:
    """Move every entry from src into dst, recursing into shared subdirectories."""
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        src_path = os.path.join(src, name)
        dst_path = os.path.join(dst, name)
        if os.path.isdir(src_path) and not os.path.islink(src_path):
            if os.path.exists(dst_path):
                _merge_move_tree(src_path, dst_path)
                try:
                    os.rmdir(src_path)
                except OSError:
                    pass
            else:
                shutil.move(src_path, dst_path)
        else:
            if os.path.exists(dst_path):
                logger.warning(
                    "Skipping %s during media relocation; %s already exists.",
                    src_path,
                    dst_path,
                )
                continue
            shutil.move(src_path, dst_path)


def _relocate_subdir(
    work_dir_abs: str, media_dir_abs: str, subdir: str, dry_run: bool
) -> Dict[str, Any]:
    source = os.path.join(work_dir_abs, subdir)
    target = os.path.join(media_dir_abs, subdir)
    action: Dict[str, Any] = {"subdir": subdir, "source": source, "target": target}

    if os.path.islink(source):
        current_target = os.path.realpath(source)
        if current_target == os.path.abspath(target) and os.path.isdir(source):
            action["status"] = "already_linked"
            return action
        action["status"] = "relinked"
        if dry_run:
            action["dry_run"] = True
            return action
        os.makedirs(media_dir_abs, exist_ok=True)
        if os.path.isdir(source):
            if os.path.isdir(target):
                _merge_move_tree(source, target)
                # source is a symlink; its resolved dir is now empty (or partially
                # merged), remove the leftover real directory it pointed to.
                if current_target != os.path.abspath(target) and os.path.isdir(
                    current_target
                ):
                    shutil.rmtree(current_target, ignore_errors=True)
            else:
                shutil.move(current_target, target)
        else:
            os.makedirs(target, exist_ok=True)
        os.remove(source)
        os.symlink(target, source, target_is_directory=True)
        return action

    if os.path.isdir(source):
        action["status"] = "moved"
        if dry_run:
            action["dry_run"] = True
            return action
        os.makedirs(media_dir_abs, exist_ok=True)
        if os.path.isdir(target):
            _merge_move_tree(source, target)
            shutil.rmtree(source, ignore_errors=True)
        else:
            shutil.move(source, target)
        os.symlink(target, source, target_is_directory=True)
        return action

    if os.path.lexists(source):
        raise RuntimeError(
            f"{source} exists and is neither a directory nor a symlink; "
            "refusing to relocate it."
        )

    action["status"] = "created"
    if dry_run:
        action["dry_run"] = True
        return action
    os.makedirs(target, exist_ok=True)
    os.symlink(target, source, target_is_directory=True)
    return action


def relocate_media_storage(
    global_config: Dict[str, Any], new_media_dir: str, dry_run: bool = False
) -> Dict[str, Any]:
    """Move work_dir/photos and work_dir/launches under new_media_dir and symlink
    them back so every existing code path that builds paths under work_dir keeps
    working unmodified."""
    work_dir = (global_config or {}).get("work_dir")
    if not work_dir:
        raise ValueError("work_dir is not configured.")
    new_media_dir = str(new_media_dir or "").strip()
    if not new_media_dir:
        raise ValueError("media_dir is required.")

    work_dir_abs = os.path.abspath(work_dir)
    media_dir_abs = os.path.abspath(new_media_dir)
    _validate_new_media_dir(work_dir_abs, media_dir_abs)

    if os.path.exists(media_dir_abs) and not os.path.isdir(media_dir_abs):
        raise ValueError(f"{media_dir_abs} exists and is not a directory.")
    if not dry_run:
        os.makedirs(media_dir_abs, exist_ok=True)
        # work_dir normally already exists, but a symlink cannot be created
        # under a parent directory that doesn't exist yet.
        os.makedirs(work_dir_abs, exist_ok=True)

    report: Dict[str, Any] = {
        "work_dir": work_dir_abs,
        "media_dir": media_dir_abs,
        "dry_run": bool(dry_run),
        "actions": [],
    }
    with RELOCATE_LOCK:
        for subdir in RELOCATABLE_SUBDIRS:
            try:
                action = _relocate_subdir(work_dir_abs, media_dir_abs, subdir, dry_run)
            except Exception as exc:
                action = {"subdir": subdir, "status": "error", "error": str(exc)}
                logger.error(
                    "Failed to relocate %s to %s: %s",
                    subdir,
                    media_dir_abs,
                    exc,
                    exc_info=True,
                )
            report["actions"].append(action)
    report["ok"] = all(a.get("status") != "error" for a in report["actions"])
    return report


def describe_media_location(global_config: Dict[str, Any]) -> Dict[str, Any]:
    work_dir = (global_config or {}).get("work_dir")
    media_dir = (global_config or {}).get("media_dir")
    result: Dict[str, Any] = {"work_dir": work_dir, "media_dir": media_dir, "dirs": {}}
    if not work_dir:
        return result

    work_dir_abs = os.path.abspath(work_dir)
    for subdir in RELOCATABLE_SUBDIRS:
        source = os.path.join(work_dir_abs, subdir)
        entry: Dict[str, Any] = {"path": source}
        if os.path.islink(source):
            entry["is_symlink"] = True
            entry["target"] = os.path.realpath(source)
            entry["exists"] = os.path.isdir(source)
        elif os.path.isdir(source):
            entry["is_symlink"] = False
            entry["exists"] = True
        else:
            entry["is_symlink"] = False
            entry["exists"] = False
        result["dirs"][subdir] = entry
    return result


def ensure_media_storage_layout(global_config: Dict[str, Any]) -> None:
    """Non-destructive startup check: only links a subdir into media_dir when
    nothing exists at the default work_dir location yet. Never moves data on
    its own; existing data is only migrated via relocate_media_storage(), which
    is triggered explicitly from the admin Storage panel."""
    work_dir = (global_config or {}).get("work_dir")
    media_dir = (global_config or {}).get("media_dir")
    if not work_dir or not media_dir:
        return

    work_dir_abs = os.path.abspath(work_dir)
    media_dir_abs = os.path.abspath(media_dir)
    try:
        _validate_new_media_dir(work_dir_abs, media_dir_abs)
    except ValueError as exc:
        logger.error("Ignoring invalid global.media_dir configuration: %s", exc)
        return

    os.makedirs(work_dir_abs, exist_ok=True)
    for subdir in RELOCATABLE_SUBDIRS:
        source = os.path.join(work_dir_abs, subdir)
        target = os.path.join(media_dir_abs, subdir)
        if os.path.islink(source):
            if os.path.realpath(source) != os.path.abspath(target):
                logger.warning(
                    "%s is linked to %s, but configured media_dir expects %s. "
                    "Use the admin Storage panel to relocate media storage.",
                    source,
                    os.path.realpath(source),
                    target,
                )
            continue
        if os.path.isdir(source):
            logger.warning(
                "%s already has data but global.media_dir is set to %s. "
                "Use the admin Storage panel 'Relocate media storage' action to "
                "migrate it there.",
                source,
                media_dir_abs,
            )
            continue
        try:
            os.makedirs(target, exist_ok=True)
            os.symlink(target, source, target_is_directory=True)
            logger.info("Linked %s to configured media_dir at %s.", source, target)
        except OSError as exc:
            logger.error("Could not link %s to %s: %s", source, target, exc)
