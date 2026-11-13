"""Paralleler Ordner-Scan mit Worker-Prozessen.

Laeuft in einem Hintergrund-Thread der GUI; Rueckmeldung ueber Callbacks.
Bereits analysierte, unveraenderte Dateien werden uebersprungen.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Callable

from .analyzer import ANALYZER_VERSION, analyze_file
from .db import Database
from .decode import AUDIO_EXTENSIONS, probe

MOVE_DURATION_TOL = 0.5  # Sekunden Abweichung der Spieldauer, ab der es nicht dieselbe Aufnahme ist


def collect_files(root: str, stop: threading.Event | None = None) -> list[tuple[str, int, float]]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if stop is not None and stop.is_set():
            break
        dirnames.sort(key=str.lower)
        for name in sorted(filenames, key=str.lower):
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                p = os.path.normpath(os.path.join(dirpath, name))
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append((p, st.st_size, st.st_mtime))
    return out


def default_workers() -> int:
    return max(1, (os.cpu_count() or 4) - 2)


class Scanner:
    def __init__(self, db: Database, root: str | list[str], workers: int,
                 on_status: Callable[[str], None],
                 on_progress: Callable[[int, int], None],
                 on_result: Callable[[int, dict], None]):
        self.db = db
        # Ein Ordner oder eine Liste aus Ordnern und Einzeldateien (Drag & Drop)
        self.targets = [os.path.normpath(p) for p in ([root] if isinstance(root, str) else root)]
        self.workers = max(1, workers)
        self.on_status = on_status
        self.on_progress = on_progress
        self.on_result = on_result
        self.stop = threading.Event()

    def _handle_moves(self, files) -> tuple[int, int, int]:
        """Verschobene Dateien wiedererkennen: Ein Eintrag, dessen Datei fehlt, zieht zu einer
        neu gefundenen Datei mit gleichem Namen, gleicher Dauer und Abtastrate um -- Groesse und
        Datum duerfen sich unterscheiden (z. B. nach dem Bearbeiten der Tags). Was danach noch
        fehlt, wird als 'missing' markiert (ausgeblendet, kann spaeter noch umziehen)."""
        index = self.db.path_index()
        gone = self.db.find_missing()
        if not gone:
            return 0, 0, 0
        by_name: dict[str, list] = {}
        for fid, path, dur, sr, _defect in gone:
            by_name.setdefault(os.path.basename(path).lower(), []).append((fid, dur or 0.0, sr or 0))
        moved = replaced = 0
        for path, size, mtime in files:
            if self.stop.is_set():
                break
            if path in index:
                continue
            cands = by_name.get(os.path.basename(path).lower())
            if not cands:
                continue
            try:
                info = probe(path)
            except Exception:
                continue
            dur = info.frames / info.sr if info.sr and info.frames else 0.0
            hits = [c for c in cands if c[2] == info.sr and abs(c[1] - dur) <= MOVE_DURATION_TOL]
            if len(hits) == 1:  # nur eindeutige Treffer umziehen
                if self.db.relocate(hits[0][0], path, size, mtime):
                    replaced += 1   # ersetzter Defekt: wird unten frisch analysiert
                else:
                    moved += 1
                cands.remove(hits[0])
        still = [fid for lst in by_name.values() for fid, _, _ in lst]
        self.db.mark_missing(still)
        return moved, len(still), replaced

    def run(self):
        self.on_status("Dateien werden gesammelt …")
        files, seen = [], set()
        for target in self.targets:
            if os.path.isdir(target):
                found = collect_files(target, self.stop)
            elif os.path.splitext(target)[1].lower() in AUDIO_EXTENSIONS and os.path.isfile(target):
                st = os.stat(target)
                found = [(target, st.st_size, st.st_mtime)]
            else:
                found = []
            for item in found:
                if item[0] not in seen:
                    seen.add(item[0])
                    files.append(item)
        moved, missing, replaced = self._handle_moves(files)
        todo = [f for f in files
                if self.db.needs_analysis(f[0], f[1], f[2], ANALYZER_VERSION)]
        total = len(files)
        done = total - len(todo)
        extra = ""
        if moved:
            extra += f" · {moved} verschobene Dateien wiedererkannt (Prüfstand übernommen)"
        if replaced:
            extra += f" · {replaced} ersetzte Defekt-Dateien werden neu geprüft"
        if missing:
            extra += f" · {missing} Einträge zeigen auf fehlende Dateien"
        self.on_status(f"{total} Audiodateien, davon {len(todo)} zu analysieren{extra}")
        self.on_progress(done, total)
        if not todo or self.stop.is_set():
            return
        with ProcessPoolExecutor(max_workers=self.workers) as ex:
            pending = {}
            it = iter(todo)

            def submit_next():
                try:
                    p, size, mtime = next(it)
                except StopIteration:
                    return False
                fut = ex.submit(analyze_file, p)
                pending[fut] = (p, size, mtime)
                return True

            for _ in range(self.workers * 2):
                if not submit_next():
                    break
            while pending:
                finished, _ = wait(list(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in finished:
                    p, size, mtime = pending.pop(fut)
                    try:
                        res = fut.result()
                    except Exception as exc:  # Worker abgestuerzt (z. B. Speicher)
                        res = {"path": p, "ok": False, "error": f"Analyse abgebrochen: {exc}",
                               "markers": [{"start": 0.0, "end": 5.0, "center": 0.0, "kinds": ["decode"],
                                            "score": 10.0, "detail": str(exc)}]}
                    file_id = self.db.store_result(res, size, mtime, ANALYZER_VERSION)
                    done += 1
                    self.on_progress(done, total)
                    if res.get("markers"):
                        self.on_result(file_id, res)
                    if not self.stop.is_set():
                        submit_next()
                if self.stop.is_set():
                    for fut in pending:
                        fut.cancel()
                    ex.shutdown(wait=False, cancel_futures=True)
                    self.on_status("Scan abgebrochen – beim nächsten Start wird fortgesetzt")
                    return
        self.on_status(f"Scan fertig: {total} Dateien geprüft")
