"""Logdatei mit bestaetigt defekten Dateien sowie Loeschen/Kopieren/Verschieben."""
from __future__ import annotations

import datetime as _dt
import os
import shutil
from pathlib import Path

from send2trash import send2trash


def write_log(log_path: str | Path, paths: list[str], append: bool = False) -> int:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing = set(read_log(log_path)) if append and log_path.exists() else set()
    new = [p for p in paths if p not in existing]
    mode = "a" if append and log_path.exists() else "w"
    with open(log_path, mode, encoding="utf-8", newline="\r\n") as f:
        if mode == "w":
            f.write("# FlacCheck – als defekt bestätigte Dateien\n")
        f.write(f"# {_dt.datetime.now():%Y-%m-%d %H:%M}  ({len(new)} Einträge)\n")
        for p in new:
            f.write(p + "\n")
    return len(new)


def read_log(log_path: str | Path) -> list[str]:
    out = []
    with open(log_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def common_root(paths: list[str]) -> str:
    try:
        return os.path.commonpath([os.path.dirname(p) for p in paths]) if paths else ""
    except ValueError:  # verschiedene Laufwerke
        return ""


def _target(path: str, dest_dir: str, keep_structure: bool, root: str) -> str:
    if keep_structure and root:
        rel = os.path.relpath(path, root)
        if rel.startswith(".."):
            rel = os.path.basename(path)
        drive_free = rel
    else:
        drive_free = os.path.basename(path)
    target = os.path.join(dest_dir, drive_free)
    base, ext = os.path.splitext(target)
    n = 2
    while os.path.exists(target):
        target = f"{base} ({n}){ext}"
        n += 1
    return target


def copy_files(paths, dest_dir, keep_structure=True):
    root = common_root(paths)
    results = []
    for p in paths:
        try:
            t = _target(p, dest_dir, keep_structure, root)
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.copy2(p, t)
            results.append((p, t, None))
        except Exception as exc:
            results.append((p, None, str(exc)))
    return results


def move_files(paths, dest_dir, keep_structure=True):
    root = common_root(paths)
    results = []
    for p in paths:
        try:
            t = _target(p, dest_dir, keep_structure, root)
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.move(p, t)
            results.append((p, t, None))
        except Exception as exc:
            results.append((p, None, str(exc)))
    return results


def delete_files(paths, permanent=False):
    results = []
    for p in paths:
        try:
            if permanent:
                os.remove(p)
            else:
                send2trash(p)
            results.append((p, None, None))
        except Exception as exc:
            results.append((p, None, str(exc)))
    return results
