"""SQLite-Persistenz: Analyseergebnisse, Marker und Pruefstatus.

Bereits analysierte, unveraenderte Dateien werden beim naechsten Scan
uebersprungen; ein abgebrochener Scan laesst sich so einfach fortsetzen.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    size        INTEGER,
    mtime       REAL,
    duration    REAL,
    sr          INTEGER,
    channels    INTEGER,
    codec       TEXT,
    tempo       REAL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- clean | suspect | error
    defect      INTEGER NOT NULL DEFAULT 0,       -- vom Nutzer als defekt bestaetigt
    reviewed    INTEGER NOT NULL DEFAULT 0,       -- vom Nutzer geprueft
    peaks       BLOB,
    analyzer    INTEGER,
    sensitivity TEXT,
    error       TEXT,
    seconds     REAL
);
CREATE TABLE IF NOT EXISTS markers (
    id       INTEGER PRIMARY KEY,
    file_id  INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    start    REAL NOT NULL,
    end      REAL NOT NULL,
    center   REAL NOT NULL,
    kinds    TEXT NOT NULL,
    score    REAL NOT NULL,
    detail   TEXT,
    removed  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_markers_file ON markers(file_id);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS scan_roots (path TEXT PRIMARY KEY, last_scan TEXT);
"""


@dataclass
class MarkerRow:
    id: int
    start: float
    end: float
    center: float
    kinds: list[str]
    score: float
    detail: str
    removed: bool
    level: int = 1                       # ab welcher Empfindlichkeit sichtbar (0..2)
    kind_levels: dict = field(default_factory=dict)

    def visible_kinds(self, level: int, kinds: set | None = None) -> list[str]:
        """Fehlerarten dieses Markers, die bei der gewaehlten Stufe sichtbar und
        ausgewaehlt sind (kinds=None: alle Arten)."""
        out = [k for k in self.kinds if self.kind_levels.get(k, self.level) <= level]
        return [k for k in out if k in kinds] if kinds is not None else out


@dataclass
class FileRow:
    id: int
    path: str
    duration: float
    sr: int
    channels: int
    codec: str
    tempo: float
    status: str
    defect: bool
    reviewed: bool
    peaks: bytes | None            # wird nicht mitgeladen (siehe Database.load_peaks)
    error: str
    markers: list[MarkerRow] = field(default_factory=list)
    has_peaks: bool = False
    reviewed_at: str = ""          # Zeitpunkt der letzten Pruefaktion (ISO), "" = unbekannt
    parked: bool = False           # in der Sonderliste geparkt (z. B. zum Bearbeiten)

    @property
    def removed_markers(self) -> list[MarkerRow]:
        return [m for m in self.markers if m.removed]

    def is_clean(self, level: int, kinds: set | None = None) -> bool:
        """Fehlerfrei: nicht defekt und keine offenen Marker -- entweder von Hand
        freigegeben (Marker entfernt) oder von der Analyse ohne Befund."""
        if self.defect or self.status == "error":
            return False
        if self.status == "clean":
            return True
        return bool(self.removed_markers) and not self.visible_markers(level, kinds)

    @property
    def active_markers(self) -> list[MarkerRow]:
        return [m for m in self.markers if not m.removed]

    def visible_markers(self, level: int, kinds: set | None = None) -> list[MarkerRow]:
        return [m for m in self.markers if not m.removed and m.visible_kinds(level, kinds)]

    def strength(self, level: int, kinds: set | None = None) -> tuple[int, float]:
        """Sortierschluessel 'Verdacht': erst die strengste Stufe, dann der Score."""
        vis = self.visible_markers(level, kinds)
        if not vis:
            return (9, 0.0)
        return (min(m.level for m in vis), max(m.score for m in vis))


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def infer_scan_roots(paths: list[str], last_root: str | None = None) -> list[str]:
    """Scanquellen aus Dateipfaden schaetzen: Liegen die Dateien in mehreren
    Unterordnern eines gemeinsamen Elternordners (z. B. _A_, _B_, _C_), ist jeder
    dieser Unterordner eine Quelle; sonst der gemeinsame Ordner selbst."""
    roots: set[str] = set()
    by_drive: dict[str, list[str]] = {}
    for p in paths:
        by_drive.setdefault(os.path.splitdrive(p)[0].lower(), []).append(p)
    for group in by_drive.values():
        common = os.path.commonpath([os.path.dirname(p) for p in group])
        children = set()
        direct = False
        for p in group:
            rel = os.path.relpath(os.path.dirname(p), common)
            if rel == ".":
                direct = True
            else:
                children.add(rel.split(os.sep)[0])
        if len(children) > 1 and not direct:
            roots |= {os.path.join(common, c) for c in children}
        else:
            roots.add(common)
    if last_root:
        last_root = os.path.normpath(last_root)
        if not any(r == last_root or r.startswith(last_root.rstrip("\\") + "\\") for r in roots):
            if any(p.startswith(last_root.rstrip("\\") + "\\") for p in paths):
                roots.add(last_root)
    return sorted(roots, key=str.lower)


def _was_removed(new: dict, old: list) -> bool:
    """Neuer Marker gilt als entfernt, wenn er nur von bereits entfernten alten
    Markern ueberlappt wird (Mitte des einen liegt im Bereich des anderen)."""
    hits = [o for o in old
            if o[0] <= new["center"] <= o[1] or new["start"] <= o[2] <= new["end"]]
    return bool(hits) and all(o[3] for o in hits)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        """Neue Spalten fuer bestehende DBs nachruesten. Indizes auf neuen Spalten
        gehoeren hierher (nach dem ALTER TABLE), nie ins statische SCHEMA."""
        # Vorschau-Wellenformen unauffaelliger Dateien werden nie angezeigt -> einmalig verwerfen
        flag = self.conn.execute("SELECT value FROM settings WHERE key='pruned_clean_peaks'").fetchone()
        if flag is None:
            self.conn.execute("UPDATE files SET peaks=NULL WHERE status='clean'")
            self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('pruned_clean_peaks', 'true')")
        # Scanquellen bestehender DBs aus den Dateipfaden ableiten (gab es vor 0.4 nicht)
        if (self.conn.execute("SELECT COUNT(*) FROM scan_roots").fetchone()[0] == 0
                and self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] > 0):
            paths = [r[0] for r in self.conn.execute("SELECT path FROM files")]
            last = self.conn.execute("SELECT value FROM settings WHERE key='root'").fetchone()
            for root in infer_scan_roots(paths, json.loads(last[0]) if last else None):
                self.conn.execute("INSERT OR IGNORE INTO scan_roots(path, last_scan) VALUES(?, NULL)",
                                  (root,))
        fcols = {r[1] for r in self.conn.execute("PRAGMA table_info(files)")}
        if "reviewed_at" not in fcols:
            self.conn.execute("ALTER TABLE files ADD COLUMN reviewed_at TEXT")
        if "parked" not in fcols:
            self.conn.execute("ALTER TABLE files ADD COLUMN parked INTEGER NOT NULL DEFAULT 0")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(markers)")}
        if "level" not in cols:
            self.conn.execute("ALTER TABLE markers ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
        if "kind_levels" not in cols:
            self.conn.execute("ALTER TABLE markers ADD COLUMN kind_levels TEXT")

    def close(self):
        with self.lock:
            self.conn.close()

    def backup_to(self, target: str | Path):
        """Konsistente Kopie der Datenbank schreiben (auch waehrend sie geoeffnet ist)."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.conn.commit()
            dst = sqlite3.connect(str(target))
            try:
                self.conn.backup(dst)
            finally:
                dst.close()

    # ------------------------------------------------------------ Einstellungen
    def get_setting(self, key: str, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                              (key, json.dumps(value)))
            self.conn.commit()

    # ------------------------------------------------------------ Scan
    def needs_analysis(self, path: str, size: int, mtime: float, analyzer: int) -> bool:
        """Neu analysieren bei geaenderter Datei oder neuer Analyse-Version. Der
        Pruefstand des Nutzers wird dabei von store_result uebernommen."""
        with self.lock:
            row = self.conn.execute(
                "SELECT size, mtime, analyzer, status FROM files WHERE path=?", (path,)).fetchone()
        if row is None:
            return True
        r_size, r_mtime, r_an, status = row
        if r_size != size or abs((r_mtime or 0) - mtime) > 1e-3 or status in ("pending", "missing"):
            return True
        return r_an != analyzer

    def store_result(self, res: dict, size: int, mtime: float, analyzer: int, keep_review: bool = True):
        """keep_review=False (Neuanalyse auf Wunsch): alle Marker frisch, entfernte kommen zurueck."""
        markers = res.get("markers") or []
        if not res.get("ok", True):
            status = "error"
        else:
            status = "suspect" if markers else "clean"
        removed_flags = [False] * len(markers)
        with self.lock:
            cur = self.conn.execute("SELECT id, size, mtime, defect, parked FROM files WHERE path=?",
                                    (res["path"],)).fetchone()
            # Geparkte (z. B. frisch geschnittene) Dateien behalten die Wellenform zum Anhoeren
            keep_peaks = bool(markers) or bool(cur and (cur[3] or cur[4]))
            values = (size, mtime, res.get("duration", 0.0), res.get("sr", 0), res.get("channels", 0),
                      res.get("codec", ""), res.get("tempo", 0.0), status,
                      (res.get("peaks") or None) if keep_peaks else None,  # Vorschau nur bei Befund/Defekt/geparkt
                      analyzer, "tief", res.get("error", ""), res.get("seconds", 0.0))
            if cur:
                file_id = cur[0]
                same_file = cur[1] == size and abs((cur[2] or 0) - mtime) <= 1e-3
                keep = ""
                if not keep_review:
                    keep = ", reviewed=0, reviewed_at='" + _now() + "'"
                elif same_file:
                    # Nur die Analyse ist neu, die Datei nicht: Pruefstand uebernehmen.
                    old = self.conn.execute("SELECT start, end, center, removed FROM markers "
                                            "WHERE file_id=?", (file_id,)).fetchall()
                    removed_flags = [_was_removed(m, old) for m in markers]
                else:
                    keep = ", defect=0, reviewed=0"
                self.conn.execute(
                    f"""UPDATE files SET size=?, mtime=?, duration=?, sr=?, channels=?, codec=?, tempo=?,
                       status=?, peaks=?, analyzer=?, sensitivity=?, error=?, seconds=?{keep}
                       WHERE id=?""", values + (file_id,))
                self.conn.execute("DELETE FROM markers WHERE file_id=?", (file_id,))
            else:
                file_id = self.conn.execute(
                    """INSERT INTO files(size, mtime, duration, sr, channels, codec, tempo, status, peaks,
                       analyzer, sensitivity, error, seconds, path)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values + (res["path"],)).lastrowid
            self.conn.executemany(
                "INSERT INTO markers(file_id, start, end, center, kinds, score, detail, removed, level, "
                "kind_levels) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(file_id, m["start"], m["end"], m["center"], ",".join(m["kinds"]), m["score"], m["detail"],
                  int(rm), m.get("level", 1), json.dumps(m.get("kind_levels") or {}))
                 for m, rm in zip(markers, removed_flags)])
            self.conn.commit()
        return file_id

    # ------------------------------------------------------------ Lesen
    def _rows_to_files(self, rows) -> list[FileRow]:
        files = [FileRow(id=r[0], path=r[1], duration=r[2] or 0.0, sr=r[3] or 0, channels=r[4] or 0,
                         codec=r[5] or "", tempo=r[6] or 0.0, status=r[7], defect=bool(r[8]),
                         reviewed=bool(r[9]), peaks=None, error=r[11] or "", has_peaks=bool(r[10]),
                         reviewed_at=r[12] or "", parked=bool(r[13]))
                 for r in rows]
        if not files:
            return files
        by_id = {f.id: f for f in files}
        ids = list(by_id)
        with self.lock:
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                q = ",".join("?" * len(chunk))
                for m in self.conn.execute(
                        f"SELECT id, file_id, start, end, center, kinds, score, detail, removed, level, "
                        f"kind_levels FROM markers WHERE file_id IN ({q}) ORDER BY start", chunk):
                    by_id[m[1]].markers.append(MarkerRow(m[0], m[2], m[3], m[4], m[5].split(","),
                                                         m[6], m[7] or "", bool(m[8]),
                                                         m[9] if m[9] is not None else 1,
                                                         json.loads(m[10]) if m[10] else {}))
        return files

    # Wellenformen werden nicht mitgeladen (bei grossen Archiven sonst Hunderte MB im Speicher)
    _COLS = ("id, path, duration, sr, channels, codec, tempo, status, defect, reviewed, "
             "peaks IS NOT NULL, error, reviewed_at, parked")

    @staticmethod
    def _root_clause(roots: list[str] | None) -> tuple[str, list]:
        """SQL-Bedingung 'liegt unterhalb einer der Quellen'. None = keine Einschraenkung,
        leere Liste = nichts."""
        if roots is None:
            return "", []
        if not roots:
            return " AND 0", []
        parts, args = [], []
        for root in roots:
            prefix = root.rstrip("\\/") + "\\"
            like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parts.append("path LIKE ? ESCAPE '\\' OR path = ?")
            args += [like, root]
        return " AND (" + " OR ".join(parts) + ")", args

    # ------------------------------------------------------------ Scanquellen
    def add_scan_root(self, root: str):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO scan_roots(path, last_scan) VALUES(?, ?)",
                              (root, _dt.datetime.now().isoformat(timespec="seconds")))
            self.conn.commit()

    def scan_roots(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute("SELECT path FROM scan_roots ORDER BY path COLLATE NOCASE")]

    def load_parked(self, roots: list[str] | None = None) -> list[FileRow]:
        """Sonderliste: geparkte Dateien, unabhaengig vom Analysestand."""
        clause, args = self._root_clause(roots)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT {self._COLS} FROM files WHERE parked=1 AND status <> 'missing'{clause}", args).fetchall()
        return self._rows_to_files(rows)

    def set_parked(self, file_ids: list[int], parked: bool):
        with self.lock:
            self.conn.executemany("UPDATE files SET parked=?, reviewed_at=? WHERE id=?",
                                  [(int(parked), _now(), i) for i in file_ids])
            self.conn.commit()

    def add_pending(self, path: str, size: int, mtime: float, duration: float, sr: int,
                    channels: int, codec: str, parked: bool) -> int:
        """Neue (z. B. geschnittene) Datei eintragen, noch nicht analysiert. Ein vorhandener
        Eintrag gleichen Pfads (Original ersetzt) wird zurueckgesetzt."""
        with self.lock:
            cur = self.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
            if cur:
                self.conn.execute("DELETE FROM markers WHERE file_id=?", (cur[0],))
                self.conn.execute(
                    "UPDATE files SET size=?, mtime=?, duration=?, sr=?, channels=?, codec=?, status='pending', "
                    "defect=0, reviewed=0, peaks=NULL, error='', parked=?, reviewed_at=? WHERE id=?",
                    (size, mtime, duration, sr, channels, codec, int(parked), _now(), cur[0]))
                fid = cur[0]
            else:
                fid = self.conn.execute(
                    "INSERT INTO files(path, size, mtime, duration, sr, channels, codec, status, parked, "
                    "reviewed_at) VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                    (path, size, mtime, duration, sr, channels, codec, int(parked), _now())).lastrowid
            self.conn.commit()
        return fid

    def load_clean(self, roots: list[str] | None = None) -> list[FileRow]:
        """Von der Analyse als unauffaellig eingestufte Dateien (ohne Marker)."""
        clause, args = self._root_clause(roots)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT {self._COLS} FROM files WHERE status='clean'{clause}", args).fetchall()
        return self._rows_to_files(rows)

    def delete_files(self, file_ids: list[int]):
        """Eintraege aus der DB entfernen (die Audiodateien bleiben unberuehrt)."""
        with self.lock:
            for i in range(0, len(file_ids), 900):
                chunk = file_ids[i:i + 900]
                self.conn.execute(f"DELETE FROM files WHERE id IN ({','.join('?' * len(chunk))})", chunk)
            self.conn.commit()

    # ------------------------------------------------------------ Verschobene / fehlende Dateien
    def path_index(self) -> dict[str, tuple]:
        """Alle Eintraege: Pfad -> (id, status, Dauer, Abtastrate)."""
        with self.lock:
            return {r[0]: r[1:] for r in self.conn.execute(
                "SELECT path, id, status, duration, sr FROM files")}

    def find_missing(self) -> list[tuple]:
        """Eintraege, deren Datei nicht (mehr) existiert: (id, Pfad, Dauer, Abtastrate, defekt)."""
        with self.lock:
            rows = self.conn.execute("SELECT id, path, duration, sr, defect FROM files").fetchall()
        return [r for r in rows if not os.path.exists(r[1])]

    def mark_missing(self, ids: list[int]):
        with self.lock:
            self.conn.executemany("UPDATE files SET status='missing' WHERE id=?", [(i,) for i in ids])
            self.conn.commit()

    def relocate(self, file_id: int, new_path: str, size: int, mtime: float) -> bool:
        """Verschobene Datei: Eintrag samt Pruefstand an den neuen Ort umziehen. Der Status wird
        aus den Markern wiederhergestellt, eine Neuanalyse ist nicht noetig (Ton unveraendert).
        Ausnahme: Eine als defekt bestaetigte Datei, deren Groesse sich geaendert hat, ist meist ein
        neu eingelesener Ersatz -- sie erbt den Defekt nicht, sondern wird frisch analysiert.
        Rueckgabe True = Eintrag zur Neuanalyse zurueckgesetzt."""
        with self.lock:
            old_size, defect = self.conn.execute("SELECT size, defect FROM files WHERE id=?",
                                                 (file_id,)).fetchone()
            self.conn.execute("DELETE FROM files WHERE path=? AND id<>?", (new_path, file_id))
            if defect and old_size != size:
                self.conn.execute("DELETE FROM markers WHERE file_id=?", (file_id,))
                self.conn.execute(
                    "UPDATE files SET path=?, size=?, mtime=?, status='pending', defect=0, reviewed=0, "
                    "peaks=NULL, error='', reviewed_at=? WHERE id=?", (new_path, size, mtime, _now(), file_id))
                self.conn.commit()
                return True
            has_markers = self.conn.execute("SELECT COUNT(*) FROM markers WHERE file_id=?",
                                            (file_id,)).fetchone()[0]
            err = self.conn.execute("SELECT error FROM files WHERE id=?", (file_id,)).fetchone()[0]
            status = "error" if err and not has_markers else ("suspect" if has_markers else "clean")
            self.conn.execute("UPDATE files SET path=?, size=?, mtime=?, status=? WHERE id=?",
                              (new_path, size, mtime, status, file_id))
            self.conn.commit()
        return False

    def delete_missing(self) -> int:
        with self.lock:
            n = self.conn.execute("DELETE FROM files WHERE status='missing'").rowcount
            self.conn.commit()
        return n

    def file_meta(self, file_id: int) -> tuple[str, int, float] | None:
        with self.lock:
            return self.conn.execute("SELECT path, size, mtime FROM files WHERE id=?", (file_id,)).fetchone()

    def load_findings(self, roots: list[str] | None = None) -> list[FileRow]:
        """Alle Dateien mit Befund (auch gepruefte/als OK quittierte) sowie alle als defekt
        bestaetigten -- auch wenn eine neuere Analyse dort nichts mehr findet."""
        clause, args = self._root_clause(roots)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT {self._COLS} FROM files WHERE (status IN ('suspect','error') OR defect=1) "
                f"AND status <> 'missing'{clause}",
                args).fetchall()
        return self._rows_to_files(rows)

    def load_peaks(self, file_id: int) -> bytes | None:
        with self.lock:
            row = self.conn.execute("SELECT peaks FROM files WHERE id=?", (file_id,)).fetchone()
        return row[0] if row else None

    def load_file(self, file_id: int) -> FileRow | None:
        with self.lock:
            rows = self.conn.execute(f"SELECT {self._COLS} FROM files WHERE id=?", (file_id,)).fetchall()
        files = self._rows_to_files(rows)
        return files[0] if files else None

    def defect_paths(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute(
                "SELECT path FROM files WHERE defect=1 ORDER BY path")]

    def counts(self, roots: list[str] | None = None) -> dict:
        clause, args = self._root_clause(roots)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT status, COUNT(*) FROM files WHERE 1=1{clause} GROUP BY status", args).fetchall()
            defect = self.conn.execute(
                f"SELECT COUNT(*) FROM files WHERE defect=1{clause}", args).fetchone()[0]
        d = dict(rows)
        d["defect"] = defect
        return d

    def reset(self) -> int:
        """Alle Scan-Ergebnisse und den Pruefstand loeschen (Einstellungen bleiben, nur die
        Quellen-Abwahl wird mit geleert). Gibt die Zahl der entfernten Dateien zurueck;
        wirft bei Fehlern, damit die Oberflaeche sie anzeigen kann."""
        with self.lock:
            n = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            try:
                self.conn.execute("DELETE FROM markers")
                self.conn.execute("DELETE FROM files")
                self.conn.execute("DELETE FROM scan_roots")
                self.conn.execute("DELETE FROM settings WHERE key='sources_off'")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            left = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            if left:
                raise RuntimeError(f"Nach dem Zurücksetzen sind noch {left} Einträge vorhanden.")
            try:
                self.conn.execute("VACUUM")  # nur Platz freigeben -- ein Fehler hier ist unkritisch
            except Exception:
                pass
        return n

    # ------------------------------------------------------------ Nutzeraktionen
    def set_marker_removed(self, marker_id: int, removed: bool):
        with self.lock:
            self.conn.execute("UPDATE markers SET removed=? WHERE id=?", (int(removed), marker_id))
            self.conn.execute(
                "UPDATE files SET reviewed=1, reviewed_at=? WHERE id=(SELECT file_id FROM markers WHERE id=?)",
                (_now(), marker_id))
            self.conn.commit()

    def set_all_markers_removed(self, file_id: int, removed: bool):
        with self.lock:
            self.conn.execute("UPDATE markers SET removed=? WHERE file_id=?", (int(removed), file_id))
            self.conn.execute("UPDATE files SET reviewed=1, reviewed_at=? WHERE id=?", (_now(), file_id))
            if removed:
                self.conn.execute("UPDATE files SET defect=0 WHERE id=?", (file_id,))
            self.conn.commit()

    def set_defect(self, file_id: int, defect: bool):
        with self.lock:
            self.conn.execute("UPDATE files SET defect=?, reviewed=1, reviewed_at=? WHERE id=?",
                              (int(defect), _now(), file_id))
            self.conn.commit()

    def rename_path(self, old: str, new: str):
        with self.lock:
            self.conn.execute("DELETE FROM files WHERE path=? AND path<>?", (new, old))
            self.conn.execute("UPDATE files SET path=? WHERE path=?", (new, old))
            self.conn.commit()

    def remove_path(self, path: str):
        with self.lock:
            self.conn.execute("DELETE FROM files WHERE path=?", (path,))
            self.conn.commit()

    def forget_file(self, file_id: int):
        """Erzwingt Neuanalyse beim naechsten Scan."""
        with self.lock:
            self.conn.execute("UPDATE files SET status='pending', reviewed=0, defect=0 WHERE id=?",
                              (file_id,))
            self.conn.commit()
