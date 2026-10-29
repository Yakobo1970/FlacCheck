from __future__ import annotations

import datetime as _dt
import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from engine import editing, fileops
from engine.analyzer import KIND_LABELS, SENSITIVITIES
from engine.db import Database, FileRow
from engine.decode import AUDIO_EXTENSIONS
from engine.scanner import Scanner, default_workers
from version import APP_NAME, VERSION

from .about_dialog import AboutDialog
from .check_combo import CheckCombo
from .fileops_dialog import FileOpsDialog
from .player import LoopPlayer
from .track_view import FileRole, TrackView, fmt_time

APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
ERROR_LOG = APP_DATA_DIR / "fehler.log"


def log_error(where: str, exc: BaseException):
    """Fehler mit Stacktrace in fehler.log schreiben (FlacCheck laeuft ohne Konsole)."""
    import traceback
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {_dt.datetime.now():%Y-%m-%d %H:%M:%S} · {where} · {APP_NAME} {VERSION}\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass
DB_PATH = APP_DATA_DIR / "flaccheck.db"
DEFAULT_DB_PATH = DB_PATH
CONFIG_PATH = APP_DATA_DIR / "config.json"   # merkt sich die zuletzt geoeffnete Datenbank


def _read_config() -> dict:
    import json
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_config(cfg: dict):
    import json
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log_error("config.json schreiben", exc)
DEFAULT_LOG_DIR = Path.home() / "Documents" / APP_NAME

FILTERS = [("open", "Offen (mit Markern)"), ("defect", "Als defekt bestätigt"),
           ("clean", "Fehlerfrei"), ("all", "Alle Befunde inkl. geprüft"),
           ("parked", "Sonderliste"), ("everything", "Alle Listen")]
SORTS = [("score", "Verdacht (stärkste zuerst)"), ("kind", "Fehlerart"), ("path", "Pfad"),
         ("markers", "Anzahl Marker"), ("recent", "Zuletzt bearbeitet")]
# Reihenfolge der Fehlerarten beim Sortieren: zuverlaessigste zuerst
KIND_ORDER = ["decode", "glitch", "dropout", "click", "spike", "silence", "noise", "jump"]


class _Bridge(QObject):
    reanalyzed = Signal(int)
    """Leitet Callbacks aus dem Scan-Thread threadsicher in den GUI-Thread."""
    status = Signal(str)
    progress = Signal(int, int)
    result = Signal(int)
    finished = Signal()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)  # Dateien/Ordner ins Fenster ziehen = analysieren
        self.resize(1400, 900)
        self.db_path = Path(DB_PATH)
        if DB_PATH == DEFAULT_DB_PATH:  # zuletzt geoeffnete Datenbank (Tests setzen DB_PATH direkt)
            last = _read_config().get("db_path")
            if last and Path(last).exists():
                self.db_path = Path(last)
        self.db = Database(self.db_path)
        self._update_title()
        self.player = LoopPlayer(self)
        self.scanner: Scanner | None = None
        self.scan_thread: threading.Thread | None = None
        self.bridge = _Bridge()
        self.last_log: str | None = self.db.get_setting("last_log")
        self._closing = False
        self._build_ui()
        self._connect()
        root = self.db.get_setting("root", "")
        if root:
            self.ed_root.setText(root)
        self.reload()

    # ------------------------------------------------------------ Aufbau
    def _build_ui(self):
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(8, 8, 8, 4)

        row1 = QHBoxLayout()
        self.ed_root = QLineEdit()
        self.ed_root.setPlaceholderText("Ordner mit Musik wählen (Unterordner werden mitgeprüft) …")
        btn_root = QPushButton("Ordner …")
        btn_root.clicked.connect(self._choose_root)
        self.cb_sens = QComboBox()
        for s in SENSITIVITIES:
            self.cb_sens.addItem(f"Anzeige: {s}", s)
        self.cb_sens.setCurrentIndex(SENSITIVITIES.index(self.db.get_setting("view_level", "niedrig")))
        self.cb_sens.setToolTip("Geprüft wird immer in voller Tiefe – hier wird nur gefiltert,\n"
                                "welche Fundstellen angezeigt werden (ohne neuen Scan):\n"
                                "niedrig: nur sehr deutliche Fehler, kaum Fehlalarme\n"
                                "mittel: ausgewogen (Standard)\n"
                                "hoch: alles Verdächtige, viele Fehlalarme")
        self.sp_workers = QSpinBox()
        self.sp_workers.setRange(1, max(1, os.cpu_count() or 4))
        self.sp_workers.setValue(self.db.get_setting("workers", default_workers()))
        self.sp_workers.setPrefix("Kerne: ")
        self.btn_scan = QPushButton("Scan starten")
        self.btn_scan.setDefault(True)
        self.btn_reset = QPushButton("DB zurücksetzen …")
        self.btn_reset.setToolTip("Alle bisherigen Scan-Ergebnisse und den Prüfstand löschen.\n"
                                  "Neue Scans werden sonst immer zur Datenbank hinzugefügt.")
        row1.addWidget(self.ed_root, 1)
        row1.addWidget(btn_root)
        row1.addWidget(self.sp_workers)
        row1.addWidget(self.btn_scan)
        row1.addWidget(self.btn_reset)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("Scan: %v von %m Dateien des Ordners")
        self.lbl_status = QLabel("Bereit.")
        row2.addWidget(self.progress, 1)
        row2.addWidget(self.lbl_status, 2)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.cb_filter = QComboBox()
        for k, label in FILTERS:
            self.cb_filter.addItem(label, k)
        self.cb_sort = QComboBox()
        for k, label in SORTS:
            self.cb_sort.addItem(label, k)
        # Mehrfachauswahl; gespeichert wird, was abgewaehlt ist (Neues ist so automatisch an)
        self.cb_sources = CheckCombo("Alle gescannten Ordner", "Ordner")
        self.cb_sources.setMinimumWidth(230)
        self.cb_kinds = CheckCombo("Alle Fehlerarten", "Fehlerarten")
        self.cb_kinds.setMinimumWidth(190)
        kinds_off = set(self.db.get_setting("kinds_off", []))
        self.cb_kinds.set_items([(KIND_LABELS[k], k) for k in KIND_ORDER], set(KIND_ORDER) - kinds_off)
        self._load_sources()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Suche in Pfad …")
        self.ed_search.setClearButtonEnabled(True)
        self.lbl_count = QLabel()
        row3.addWidget(self.cb_sens)
        row3.addWidget(self.cb_sources)
        row3.addWidget(QLabel("Anzeigen:"))
        row3.addWidget(self.cb_filter)
        self.btn_clear_clean = QPushButton("Liste leeren …")
        self.btn_clear_clean.setToolTip("Die angezeigten fehlerfreien Tracks aus der Datenbank entfernen")
        self.btn_clear_clean.setVisible(False)
        row3.addWidget(self.btn_clear_clean)
        row3.addWidget(self.cb_kinds)
        row3.addWidget(QLabel("Sortierung:"))
        row3.addWidget(self.cb_sort)
        self.ed_search.setMinimumWidth(180)
        row3.addWidget(self.ed_search, 1)
        row2.addWidget(self.lbl_count)  # Zaehler in die Statuszeile, damit die Filterzeile Platz hat
        lay.addLayout(row3)

        self.view = TrackView()
        lay.addWidget(self.view, 1)

        row4 = QHBoxLayout()
        self.btn_stop = QPushButton("■ Wiedergabe stoppen  (Leertaste)")
        self.btn_log = QPushButton("Bestätigte Defekte ins Log schreiben …")
        self.btn_ops = QPushButton("Log öffnen: Löschen / Kopieren / Verschieben …")
        self.lbl_play = QLabel()
        row4.addWidget(self.btn_stop)
        row4.addWidget(self.lbl_play, 1)
        row4.addWidget(self.btn_log)
        row4.addWidget(self.btn_ops)
        lay.addLayout(row4)
        self.setCentralWidget(central)

        self.lbl_legend = QLabel()
        self.lbl_legend.setTextFormat(Qt.RichText)
        self.lbl_legend.setToolTip("Anzahl der angezeigten (nicht ausgegrauten) Fundstellen je Fehlerart")
        self.statusBar().addPermanentWidget(self.lbl_legend)
        self._update_legend()

        m = self.menuBar().addMenu("&Datei")
        a = QAction("Ordner wählen …", self)
        a.triggered.connect(self._choose_root)
        m.addAction(a)
        a = QAction("Defekte ins Log schreiben …", self)
        a.triggered.connect(self._write_log)
        m.addAction(a)
        a = QAction("Log öffnen / Dateiaktionen …", self)
        a.triggered.connect(self._open_ops)
        m.addAction(a)
        a = QAction("Datenbank öffnen …", self)
        a.triggered.connect(self._open_db)
        m.addAction(a)
        a = QAction("Neue Datenbank …", self)
        a.triggered.connect(self._new_db)
        m.addAction(a)
        a = QAction("Datenbank sichern unter …", self)
        a.triggered.connect(self._backup_db)
        m.addAction(a)
        a = QAction("Zur Standard-Datenbank wechseln", self)
        a.triggered.connect(lambda: self._switch_db(DEFAULT_DB_PATH))
        m.addAction(a)
        m.addSeparator()
        a = QAction("Fehlende Dateien prüfen …", self)
        a.triggered.connect(self._check_missing)
        m.addAction(a)
        a = QAction("Datenbank zurücksetzen …", self)
        a.triggered.connect(self._reset_db)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Beenden", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        h = self.menuBar().addMenu("&Hilfe")
        a = QAction("Bedienung", self)
        a.triggered.connect(self._help)
        h.addAction(a)
        h.addSeparator()
        a = QAction(f"Über {APP_NAME} …", self)
        a.triggered.connect(lambda: AboutDialog(self).exec())
        h.addAction(a)

    def _connect(self):
        self.btn_scan.clicked.connect(self._toggle_scan)
        self.cb_filter.currentIndexChanged.connect(self._filter_changed)
        self.btn_clear_clean.clicked.connect(self._clear_clean)
        self.bridge.reanalyzed.connect(self._on_reanalyzed)
        self.cb_kinds.selection_changed.connect(self._kinds_changed)
        self.cb_sources.selection_changed.connect(self._sources_changed)
        self.btn_reset.clicked.connect(self._reset_db)
        self.view.delegate.peak_loader = self.db.load_peaks
        self.cb_sens.currentIndexChanged.connect(self._level_changed)
        self.cb_sort.currentIndexChanged.connect(self.reload)
        self._search_timer = QTimer(self, singleShot=True, interval=250)
        self._search_timer.timeout.connect(self.reload)
        self.ed_search.textChanged.connect(lambda: self._search_timer.start())
        self.ed_root.editingFinished.connect(self.reload)
        self.btn_stop.clicked.connect(self.player.stop)
        self.btn_log.clicked.connect(self._write_log)
        self.btn_ops.clicked.connect(self._open_ops)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._space)

        d = self.view.delegate
        d.play_marker.connect(self._play_marker)
        d.play_at.connect(self._play_at)
        d.remove_marker.connect(self._remove_marker)
        d.toggle_defect.connect(self._toggle_defect)
        d.restore_marker.connect(self._restore_marker)
        self.view.customContextMenuRequested.connect(self._context_menu)

        self.player.position_changed.connect(self._on_position)
        self.player.error.connect(lambda msg: self.statusBar().showMessage(msg, 6000))

        self.bridge.status.connect(self.lbl_status.setText)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.result.connect(self._on_result)
        self.bridge.finished.connect(self._on_scan_finished)

    # ------------------------------------------------------------ Liste
    def _root(self) -> str:
        r = self.ed_root.text().strip()
        return os.path.normpath(r) if r else ""

    def _level(self) -> int:
        return SENSITIVITIES.index(self.cb_sens.currentData())

    # ------------------------------------------------------------ Datenbanken
    def _update_title(self):
        name = "" if self.db_path == DEFAULT_DB_PATH else f"  –  {self.db_path.name}"
        self.setWindowTitle(f"{APP_NAME} {VERSION}{name}")

    def _db_busy(self, what: str) -> bool:
        if self.scanner is not None:
            QMessageBox.information(self, what, "Während eines Scans nicht möglich. "
                                                "Bitte den Scan anhalten oder abwarten.")
            return True
        return False

    def _backup_db(self):
        default = str(DEFAULT_LOG_DIR / f"flaccheck_{_dt.datetime.now():%Y-%m-%d_%H%M}.db")
        p, _ = QFileDialog.getSaveFileName(self, "Datenbank sichern unter", default,
                                           "FlacCheck-Datenbank (*.db)")
        if not p:
            return
        if Path(p).resolve() == self.db_path.resolve():
            QMessageBox.warning(self, "Datenbank sichern", "Bitte einen anderen Dateinamen wählen.")
            return
        try:
            self.db.backup_to(p)
        except Exception as exc:
            log_error("DB sichern", exc)
            QMessageBox.critical(self, "Datenbank sichern", f"Sichern fehlgeschlagen:\n{exc}")
            return
        self.statusBar().showMessage(f"Datenbank gesichert: {p}", 8000)

    def _open_db(self):
        if self._db_busy("Datenbank öffnen"):
            return
        p, _ = QFileDialog.getOpenFileName(self, "Datenbank öffnen", str(self.db_path.parent),
                                           "FlacCheck-Datenbank (*.db);;Alle Dateien (*.*)")
        if p:
            self._switch_db(Path(p))

    def _new_db(self):
        if self._db_busy("Neue Datenbank"):
            return
        default = str(DEFAULT_LOG_DIR / f"flaccheck_{_dt.date.today():%Y-%m-%d}.db")
        p, _ = QFileDialog.getSaveFileName(self, "Neue Datenbank anlegen", default,
                                           "FlacCheck-Datenbank (*.db)")
        if not p:
            return
        if Path(p).exists():
            QMessageBox.warning(self, "Neue Datenbank", "Die Datei gibt es schon – zum Wechseln bitte "
                                                       "»Datenbank öffnen …« verwenden.")
            return
        self._switch_db(Path(p))

    def _switch_db(self, path: Path):
        """Zu einer anderen Datenbank wechseln (wird angelegt, falls neu)."""
        path = Path(path)
        if self._db_busy("Datenbank wechseln") or path.resolve() == self.db_path.resolve():
            return
        try:
            new_db = Database(path)
            new_db.counts()  # wirft, falls die Datei keine FlacCheck-Datenbank ist
        except Exception as exc:
            log_error("DB öffnen", exc)
            QMessageBox.critical(self, "Datenbank öffnen",
                                 f"Die Datei lässt sich nicht als FlacCheck-Datenbank öffnen:\n{exc}")
            return
        self.player.stop()
        self.db.close()
        self.db, self.db_path = new_db, path
        self.view.delegate.peak_loader = self.db.load_peaks
        self.view.delegate.clear_cache()
        cfg = _read_config()
        cfg["db_path"] = str(path) if path != DEFAULT_DB_PATH else None
        _write_config(cfg)
        # Einstellungen gehoeren zur jeweiligen Datenbank
        self.last_log = self.db.get_setting("last_log")
        self.ed_root.setText(self.db.get_setting("root", "") or "")
        self.cb_sens.blockSignals(True)
        self.cb_sens.setCurrentIndex(SENSITIVITIES.index(self.db.get_setting("view_level", "niedrig")))
        self.cb_sens.blockSignals(False)
        kinds_off = set(self.db.get_setting("kinds_off", []))
        self.cb_kinds.set_items([(KIND_LABELS[k], k) for k in KIND_ORDER], set(KIND_ORDER) - kinds_off)
        self._load_sources()
        self.progress.setValue(0)
        self._update_title()
        self.reload()
        self.lbl_status.setText(f"Datenbank: {path}")

    def _check_missing(self):
        """Eintraege suchen, deren Datei nicht mehr existiert; anbieten, sie zu entfernen."""
        if self._db_busy("Fehlende Dateien prüfen"):
            return
        gone = self.db.find_missing()
        self.db.mark_missing([g[0] for g in gone])
        self.reload()
        if not gone:
            QMessageBox.information(self, "Fehlende Dateien", "Alle Einträge zeigen auf vorhandene Dateien.")
            return
        n_def = sum(1 for g in gone if g[4])
        sample = "\n".join(g[1] for g in gone[:8]) + ("\n…" if len(gone) > 8 else "")
        r = QMessageBox.question(
            self, "Fehlende Dateien",
            f"{len(gone)} Einträge zeigen auf Dateien, die es nicht mehr gibt"
            f"{f' (davon {n_def} als defekt bestätigt)' if n_def else ''}:\n\n{sample}\n\n"
            "Wurden sie nur verschoben, einfach den neuen Ordner scannen – sie werden dort an Name und "
            "Spieldauer wiedererkannt und behalten ihren Prüfstand.\n\n"
            "Ja = jetzt aus der Datenbank entfernen\nNein = behalten (ausgeblendet)")
        if r == QMessageBox.Yes:
            n = self.db.delete_missing()
            self.reload()
            self.statusBar().showMessage(f"{n} fehlende Einträge entfernt", 6000)

    def _load_sources(self):
        """Scanquellen in die Klappliste uebernehmen (abgewaehlte bleiben abgewaehlt)."""
        roots = self.db.scan_roots()
        off = set(self.db.get_setting("sources_off", []))
        self.cb_sources.set_items([(r, r) for r in roots], set(roots) - off)

    def _scope_root(self) -> list[str] | None:
        """Gewaehlte Scanquellen; None = alle (dann auch Dateien ausserhalb bekannter Quellen)."""
        return None if self.cb_sources.all_checked() else self.cb_sources.checked_values()

    def _kinds(self) -> set | None:
        return None if self.cb_kinds.all_checked() else set(self.cb_kinds.checked_values())

    def _sources_changed(self):
        off = set(self.cb_sources.values()) - set(self.cb_sources.checked_values())
        self.db.set_setting("sources_off", sorted(off))
        self.reload()

    def _kinds_changed(self):
        off = set(self.cb_kinds.values()) - set(self.cb_kinds.checked_values())
        self.db.set_setting("kinds_off", sorted(off))
        self.reload()

    def _reset_db(self):
        if self.scanner is not None:
            QMessageBox.information(self, "Datenbank zurücksetzen",
                                    "Während eines Scans kann die Datenbank nicht zurückgesetzt werden. "
                                    "Bitte den Scan anhalten oder abwarten.")
            return
        c = self.db.counts()
        total = sum(v for k, v in c.items() if k != "defect")
        msg = (f"Alle Ergebnisse von {total} geprüften Dateien und der Prüfstand werden gelöscht"
               f" – auch {c.get('defect', 0)} als defekt bestätigte Dateien und alle entfernten Marker.\n\n"
               "Tipp: Vorher »Bestätigte Defekte ins Log schreiben«.\n\nDatenbank wirklich zurücksetzen?")
        if QMessageBox.warning(self, "Datenbank zurücksetzen", msg,
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            self.lbl_status.setText("Zurücksetzen abgebrochen – es wurde nichts gelöscht.")
            return
        self.player.stop()
        try:
            removed = self.db.reset()
        except Exception as exc:
            log_error("DB zurücksetzen", exc)
            QMessageBox.critical(self, "Datenbank zurücksetzen",
                                 f"Das Zurücksetzen ist fehlgeschlagen – es wurde nichts gelöscht.\n\n{exc}\n\n"
                                 f"Details: {ERROR_LOG}")
            return
        self._load_sources()
        self.progress.setValue(0)
        self.lbl_status.setText(f"Datenbank geleert: {removed} Dateien entfernt.")
        self.reload()
        QMessageBox.information(self, "Datenbank zurücksetzen",
                                f"Datenbank geleert: {removed} Dateien und der gesamte Prüfstand wurden "
                                "entfernt. Der nächste Scan beginnt bei 0.")

    def _level_changed(self):
        self.db.set_setting("view_level", self.cb_sens.currentData())
        self.reload()

    def _filter_changed(self):
        """In 'Fehlerfrei' standardmaessig die zuletzt bearbeiteten oben; sonst alte Sortierung."""
        clean = self.cb_filter.currentData() == "clean"
        self.btn_clear_clean.setVisible(clean)
        if clean and self.cb_sort.currentData() != "recent":
            self._sort_before_clean = self.cb_sort.currentIndex()
            self.cb_sort.blockSignals(True)
            self.cb_sort.setCurrentIndex(self.cb_sort.findData("recent"))
            self.cb_sort.blockSignals(False)
        elif not clean and getattr(self, "_sort_before_clean", None) is not None:
            self.cb_sort.blockSignals(True)
            self.cb_sort.setCurrentIndex(self._sort_before_clean)
            self.cb_sort.blockSignals(False)
            self._sort_before_clean = None
        self.reload()

    def _passes(self, row: FileRow) -> bool:
        flt = self.cb_filter.currentData()
        kinds = self._kinds()
        q = self.ed_search.text().strip().lower()
        if flt in ("parked", "everything"):
            # Sonderliste: alles Geparkte; "Alle Listen": jeder Track (Fehlerart-Filter wirkt nur,
            # wenn der Track ueberhaupt Marker hat)
            if flt == "parked" and not row.parked:
                return False
            if kinds is not None and row.markers and not row.visible_markers(self._level(), kinds) \
                    and not row.parked:
                return False
            return not q or q in row.path.lower()
        if row.parked:  # Geparktes erscheint nur in Sonderliste und "Alle Listen"
            return False
        if flt == "clean":
            if not row.is_clean(self._level(), kinds):
                return False
            if kinds is not None and row.status != "clean" and not any(
                    set(m.kinds) & kinds for m in row.removed_markers):
                return False
            q = self.ed_search.text().strip().lower()
            return not q or q in row.path.lower()
        visible = row.visible_markers(self._level(), kinds)
        if flt == "open" and (row.defect or not visible):
            return False
        if flt == "defect" and not row.defect:
            return False
        if flt == "all" and not visible and not row.defect and row.status != "error":
            return False
        if kinds is not None and not visible:
            return False
        q = self.ed_search.text().strip().lower()
        return not q or q in row.path.lower()

    def _sorted(self, rows):
        key = self.cb_sort.currentData()
        lvl, kind = self._level(), self._kinds()
        if key == "path":
            return sorted(rows, key=lambda r: r.path.lower())
        if key == "recent":  # zuletzt bearbeitete oben, ohne Zeitstempel danach alphabetisch
            stamped = sorted([r for r in rows if r.reviewed_at], key=lambda r: r.reviewed_at, reverse=True)
            rest = sorted([r for r in rows if not r.reviewed_at],
                          key=lambda r: (r.status == "clean", r.path.lower()))
            return stamped + rest
        if key == "markers":
            return sorted(rows, key=lambda r: (-len(r.visible_markers(lvl, kind)), r.path.lower()))
        if key == "kind":
            def kind_key(r):
                vis = r.visible_markers(lvl, kind)
                kinds = {k for m in vis for k in m.visible_kinds(lvl, kind)}
                first = min((KIND_ORDER.index(k) for k in kinds if k in KIND_ORDER), default=99)
                st = r.strength(lvl, kind)
                return (first, st[0], -st[1], r.path.lower())
            return sorted(rows, key=kind_key)

        def score_key(r):
            if r.status == "error":
                return (-1, 0.0, r.path.lower())
            st = r.strength(lvl, kind)
            return (st[0], -st[1], r.path.lower())
        return sorted(rows, key=score_key)

    def reload(self):
        if self._closing:
            return
        m = self.view.model_
        m.level, m.kinds = self._level(), self._kinds()
        flt = self.cb_filter.currentData()
        roots = self._scope_root()
        if flt == "parked":
            self._all_rows = self.db.load_parked(roots)
        else:
            self._all_rows = self.db.load_findings(roots)
            if flt in ("clean", "everything"):
                self._all_rows += self.db.load_clean(roots)
            if flt == "everything":
                have = {r.id for r in self._all_rows}
                self._all_rows += [r for r in self.db.load_parked(roots) if r.id not in have]
        shown = self._sorted([r for r in self._all_rows if self._passes(r)])
        self.view.delegate.clear_cache()
        m.set_rows(shown)
        self._update_count()

    def _update_count(self):
        lvl = self._level()
        rows = getattr(self, "_all_rows", [])
        with_findings = sum(1 for r in rows if r.visible_markers(lvl) or r.status == "error")
        c = self.db.counts(self._scope_root())
        total = sum(v for k, v in c.items() if k not in ("defect", "missing"))
        missing = c.get("missing", 0)
        shown = self.view.model_.rowCount()
        scope = "in allen Ordnern" if self._scope_root() is None else "in den gewählten Ordnern"
        self.lbl_count.setText(f"{shown} angezeigt  ·  {with_findings} mit Befund (Stufe {SENSITIVITIES[lvl]})  ·  "
                               f"{total} Dateien in der Datenbank {scope}  ·  "
                               f"{c.get('defect', 0)} als defekt bestätigt"
                               + (f"  ·  {missing} fehlen (verschoben/gelöscht?)" if missing else ""))
        if hasattr(self, "lbl_legend"):
            self._update_legend()
        self.lbl_count.setToolTip("Gezählt werden alle bisher analysierten Dateien der gewählten Ordner – "
                                  "nicht nur die des laufenden Scans. Über »Liste leeren« entfernte Dateien "
                                  "fehlen bis zum nächsten Scan ihres Ordners.")

    def _update_legend(self):
        """Fusszeile: Farbe, Name und Anzahl der angezeigten Fundstellen je Fehlerart."""
        from collections import Counter
        from .theme import KIND_COLORS
        m = self.view.model_
        cnt = Counter(k for r in m.rows for mk in m.visible(r) for k in m.kinds_of(mk))
        self.lbl_legend.setText("   ".join(
            f"<span style='color:{c.name()}'>■</span> {KIND_LABELS[k]} ({cnt.get(k, 0)})"
            for k, c in KIND_COLORS.items()))

    def _remember(self, file_id: int, row: FileRow | None):
        """Gesamtliste (fuer Zaehler und Stufenwechsel) aktuell halten."""
        rows = [r for r in getattr(self, "_all_rows", []) if r.id != file_id]
        self._all_rows = rows + ([row] if row is not None else [])

    def _refresh_row(self, file_id: int):
        row = self.db.load_file(file_id)
        self._remember(file_id, row)
        if row is None:
            self.view.model_.remove_id(file_id)
            self._update_count()
            return
        if self._passes(row):
            self.view.model_.replace(row)
        else:
            self.view.model_.remove_id(file_id)
        self._update_count()

    # ------------------------------------------------------------ Scan
    def _choose_root(self):
        d = QFileDialog.getExistingDirectory(self, "Musikordner wählen", self._root() or str(Path.home()))
        if d:
            self.ed_root.setText(os.path.normpath(d))
            self.db.set_setting("root", os.path.normpath(d))
            self.reload()

    def _toggle_scan(self):
        if self.scanner is not None:
            self.scanner.stop.set()
            self.btn_scan.setEnabled(False)
            self.lbl_status.setText("Wird angehalten …")
            return
        root = self._root()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, APP_NAME, "Bitte zuerst einen vorhandenen Ordner wählen.")
            return
        self.db.set_setting("root", root)
        self._start_scan([root])

    def _start_scan(self, targets: list[str]):
        """Scan fuer Ordner und/oder Einzeldateien starten. Ordner werden zu Scanquellen,
        bei Einzeldateien ihr Ordner (die Liste zeigt trotzdem nur analysierte Dateien)."""
        self.db.set_setting("workers", self.sp_workers.value())
        for t in targets:
            self.db.add_scan_root(t if os.path.isdir(t) else os.path.dirname(t))
        self._load_sources()
        b = self.bridge
        self.scanner = Scanner(self.db, targets, self.sp_workers.value(),
                               on_status=b.status.emit, on_progress=b.progress.emit,
                               on_result=lambda fid, res: b.result.emit(fid))

        def run():
            try:
                self.scanner.run()
            except Exception as exc:
                b.status.emit(f"Scan-Fehler: {exc}")
            finally:
                b.finished.emit()

        self.btn_scan.setText("Scan anhalten")
        self.btn_reset.setEnabled(False)
        self.sp_workers.setEnabled(False)
        self.reload()
        self.scan_thread = threading.Thread(target=run, daemon=True)
        self.scan_thread.start()

    # ------------------------------------------------------------ Drag & Drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [os.path.normpath(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        targets = [p for p in paths if os.path.isdir(p)
                   or os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS]
        if not targets:
            self.statusBar().showMessage("Keine Audiodateien oder Ordner erkannt.", 6000)
            return
        e.acceptProposedAction()
        if self.scanner is not None:
            QMessageBox.information(self, APP_NAME, "Es läuft bereits ein Scan. Bitte nach dessen Ende "
                                                    "erneut ins Fenster ziehen.")
            return
        if len(targets) == 1 and os.path.isdir(targets[0]):
            self.ed_root.setText(targets[0])
            self.db.set_setting("root", targets[0])
        n_dirs = sum(os.path.isdir(t) for t in targets)
        self.statusBar().showMessage(f"Analysiere {n_dirs} Ordner und {len(targets) - n_dirs} Dateien …", 6000)
        self._start_scan(targets)

    def _on_progress(self, done, total):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)

    def _on_result(self, file_id: int):
        row = self.db.load_file(file_id)
        self._remember(file_id, row)
        if row is not None and self._passes(row):
            if self.view.model_.row_of(file_id) >= 0:
                self.view.model_.replace(row)
            else:
                self.view.model_.append_row(row)
        self._update_count()

    def _on_scan_finished(self):
        self.scanner = None
        self.btn_scan.setText("Scan starten")
        self.btn_scan.setEnabled(True)
        self.btn_reset.setEnabled(True)
        self.sp_workers.setEnabled(True)
        self.reload()

    # ------------------------------------------------------------ Wiedergabe
    def _play_marker(self, row: FileRow, m):
        if self.player.playing and self.player.marker_id == m.id:
            self.player.stop()
            return
        self.player.play(row.id, row.path, m.start, m.end, marker_id=m.id, loop=True)
        self._set_play_state(row.id, m.id)
        self.lbl_play.setText(f"▶ Schleife {fmt_time(m.start)} – {fmt_time(m.end)}  ·  "
                              f"{os.path.basename(row.path)}")

    def _play_at(self, row: FileRow, t: float):
        """Klick in die Wellenform: ab dort bis zum Trackende abspielen, ohne Schleife."""
        t0 = max(0.0, t)
        self.player.play(row.id, row.path, t0, row.duration, loop=False)
        self._set_play_state(row.id, None)
        self.lbl_play.setText(f"▶ ab {fmt_time(t0)} bis zum Ende  ·  {os.path.basename(row.path)}")

    def _set_play_state(self, file_id, marker_id):
        m = self.view.model_
        old = m.play_file
        m.play_file, m.play_marker = (file_id, marker_id) if self.player.playing else (None, None)
        m.refresh_id(old)
        m.refresh_id(file_id)

    def _on_position(self, file_id: int, pos: float):
        m = self.view.model_
        if pos < 0:
            old = m.play_file
            m.play_file = m.play_marker = None
            m.play_pos = -1.0
            m.refresh_id(old)
            self.lbl_play.setText("")
            return
        m.play_pos = pos
        m.refresh_id(file_id)

    def _space(self):
        if self.player.playing:
            self.player.stop()

    # ------------------------------------------------------------ Pruefaktionen
    def _restore_marker(self, row: FileRow, m):
        self.db.set_marker_removed(m.id, False)
        self._refresh_row(row.id)
        self.statusBar().showMessage(
            f"Marker {fmt_time(m.start)} wiederhergestellt – der Track steht wieder unter »Offen«", 5000)

    # ------------------------------------------------------------ Bearbeiten
    def _open_editor(self, row: FileRow):
        from .editor import EditorWindow
        from .theme import KIND_COLORS
        if self.player.file_id == row.id:
            self.player.stop()
        markers = [(m.start, m.end, KIND_COLORS.get(m.kinds[0]), m.removed) for m in row.markers]
        ed = EditorWindow(row, markers, self)
        ed.saved.connect(self._on_edited)
        ed.setAttribute(Qt.WA_DeleteOnClose)
        ed.show()

    def _on_edited(self, paths: list, original: str, original_removed: bool, park: bool):
        """Geschnittene Teile in die DB eintragen und sofort neu analysieren."""
        from engine.decode import probe
        new_ids = []
        for p in paths:
            p = os.path.normpath(p)
            try:
                st = os.stat(p)
                info = probe(p)
                dur = info.frames / info.sr if info.sr else 0.0
                new_ids.append(self.db.add_pending(p, st.st_size, st.st_mtime, dur, info.sr, info.channels,
                                                   info.codec, park))
            except Exception as exc:
                log_error("Teilstück eintragen", exc)
        if original_removed:
            self.db.remove_path(os.path.normpath(original))
        self.reload()
        rows = [r for r in (self.db.load_file(i) for i in new_ids) if r is not None]
        if rows:
            self._reanalyze_now(rows)
        where = " – in der Sonderliste" if park else ""
        self.statusBar().showMessage(f"{len(paths)} bearbeitete Datei(en) gespeichert, werden neu bewertet{where}",
                                     8000)

    def _reanalyze_now(self, rows: list[FileRow]):
        """Ausgewaehlte Titel sofort neu analysieren (frische Marker, Entfernungen verworfen)."""
        from engine.analyzer import ANALYZER_VERSION, analyze_file
        ids = [r.id for r in rows]
        b = self.bridge
        self.lbl_status.setText(f"Analysiere {len(ids)} Titel neu …")

        def run():
            for fid in ids:
                meta = self.db.file_meta(fid)
                if meta is None:
                    continue
                path = meta[0]
                try:
                    st = os.stat(path)
                except OSError:
                    b.status.emit(f"Nicht gefunden: {path}")
                    continue
                res = analyze_file(path)
                self.db.store_result(res, st.st_size, st.st_mtime, ANALYZER_VERSION, keep_review=False)
                b.reanalyzed.emit(fid)
            b.status.emit(f"{len(ids)} Titel neu analysiert")

        threading.Thread(target=run, daemon=True).start()

    def _on_reanalyzed(self, file_id: int):
        self.view.delegate.clear_cache(file_id)
        self._refresh_row(file_id)

    def _clear_clean(self):
        rows = list(self.view.model_.rows)
        if not rows:
            return
        by_hand = sum(1 for r in rows if r.status != "clean")
        msg = (f"{len(rows)} fehlerfreie Tracks aus der Datenbank entfernen?\n"
               f"(davon {by_hand} von Hand freigegeben, {len(rows) - by_hand} ohne Befund)\n\n"
               "Die Audiodateien selbst bleiben unberührt. Beim nächsten Scan ihres Ordners werden "
               "diese Tracks aber neu analysiert und erscheinen dann wieder mit ihren Markern.")
        if QMessageBox.question(self, "Liste leeren", msg) != QMessageBox.Yes:
            return
        if self.player.file_id in {r.id for r in rows}:
            self.player.stop()
        self.db.delete_files([r.id for r in rows])
        self.reload()
        self.statusBar().showMessage(f"{len(rows)} Tracks aus der Datenbank entfernt", 5000)

    def _remove_marker(self, row: FileRow, m):
        if self.player.marker_id == m.id:
            self.player.stop()
        self.db.set_marker_removed(m.id, True)
        self._refresh_row(row.id)
        self.statusBar().showMessage(f"Marker {fmt_time(m.start)} als Fehlalarm ausgegraut – ↺ holt ihn zurück", 5000)

    def _toggle_defect(self, row: FileRow):
        self.db.set_defect(row.id, not row.defect)
        self._refresh_row(row.id)

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        sel = [i.data(FileRole) for i in self.view.selectedIndexes()] or [idx.data(FileRole)]
        if idx.data(FileRole).id not in {r.id for r in sel}:
            sel = [idx.data(FileRole)]
        menu = QMenu(self)
        first = idx.data(FileRole)
        a_edit = menu.addAction("Bearbeiten (teilen / schneiden) …")
        a_edit.setEnabled(editing.can_edit(first.path) and os.path.exists(first.path))
        if not editing.can_edit(first.path):
            a_edit.setText("Bearbeiten – nur MP3, FLAC, WAV, AIFF")
        any_parked = any(r.parked for r in sel)
        a_park = menu.addAction(f"In Sonderliste parken ({len(sel)})")
        a_unpark = menu.addAction(f"Aus der Sonderliste nehmen ({len(sel)})")
        a_rescore = menu.addAction(f"Zur Neubewertung zurückschicken – jetzt analysieren ({len(sel)})")
        a_unpark.setEnabled(any_parked)
        menu.addSeparator()
        a_def = menu.addAction(f"Als defekt bestätigen ({len(sel)})")
        a_ok = menu.addAction(f"Unauffällig – alle Marker entfernen ({len(sel)})")
        a_undef = menu.addAction("Defekt-Markierung aufheben")
        menu.addSeparator()
        a_restore = menu.addAction("Entfernte Marker wiederherstellen")
        a_now = menu.addAction(f"Jetzt neu analysieren ({len(sel)})")
        a_reana = menu.addAction("Beim nächsten Scan neu analysieren")
        menu.addSeparator()
        a_explorer = menu.addAction("Im Explorer zeigen")
        a_copy = menu.addAction("Pfad kopieren")
        act = menu.exec(self.view.viewport().mapToGlobal(pos))
        if act is None:
            return
        for r in sel:
            if act is a_def:
                self.db.set_defect(r.id, True)
            elif act is a_undef:
                self.db.set_defect(r.id, False)
            elif act is a_ok:
                self.db.set_all_markers_removed(r.id, True)
            elif act is a_restore:
                self.db.set_all_markers_removed(r.id, False)
            elif act is a_reana:
                self.db.forget_file(r.id)
        if act is a_edit:
            self._open_editor(first)
            return
        if act in (a_park, a_unpark):
            self.db.set_parked([r.id for r in sel], act is a_park)
            self.reload()
            self.statusBar().showMessage(
                f"{len(sel)} Track(s) {'in die Sonderliste geparkt' if act is a_park else 'aus der Sonderliste genommen'}", 5000)
            return
        if act is a_rescore:
            self.db.set_parked([r.id for r in sel], False)
            self._reanalyze_now(sel)
            self.reload()
            return
        if act is a_now:
            self._reanalyze_now(sel)
            return
        if act is a_explorer:
            subprocess.Popen(["explorer", "/select,", sel[0].path])
        elif act is a_copy:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText("\n".join(r.path for r in sel))
        else:
            for r in sel:
                self._refresh_row(r.id)

    # ------------------------------------------------------------ Log
    def _write_log(self):
        paths = self.db.defect_paths()
        if not paths:
            QMessageBox.information(self, APP_NAME, "Es ist noch keine Datei als defekt bestätigt.")
            return
        default = self.last_log or str(DEFAULT_LOG_DIR / f"defekte_{_dt.date.today():%Y-%m-%d}.log")
        p, _ = QFileDialog.getSaveFileName(self, "Logdatei speichern", default,
                                           "Logdateien (*.log);;Textdateien (*.txt)",
                                           options=QFileDialog.DontConfirmOverwrite)
        if not p:
            return
        append = False
        if os.path.exists(p):
            r = QMessageBox.question(self, "Logdatei existiert",
                                     "Die Logdatei existiert bereits.\n\nJa = neue Einträge anhängen\n"
                                     "Nein = überschreiben",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            append = r == QMessageBox.Yes
        n = fileops.write_log(p, paths, append=append)
        self.last_log = p
        self.db.set_setting("last_log", p)
        QMessageBox.information(self, APP_NAME, f"{n} Dateien ins Log geschrieben:\n{p}")

    def _open_ops(self):
        self.player.stop()
        dlg = FileOpsDialog(self.db, self.last_log, self)
        dlg.exec()
        if dlg.log_path:
            self.last_log = dlg.log_path
            self.db.set_setting("last_log", dlg.log_path)
        self.reload()

    def _help(self):
        QMessageBox.information(self, "Bedienung", (
            "1. Ordner wählen und »Scan starten«. Unterordner werden mitgeprüft; ein Abbruch ist "
            "jederzeit möglich – der nächste Scan setzt fort.\n\n"
            "2. Auffällige Tracks erscheinen untereinander als Wellenform. Farbige Bereiche sind "
            "Marker (Fundstelle −5 s … +5 s).\n\n"
            "3. Marker anklicken oder ▶: Bereich in Schleife anhören. Nochmal klicken oder Leertaste = Stopp. "
            "Klick in die Wellenform außerhalb: ab dort 10 s Schleife.\n\n"
            "4. Fehler nicht hörbar? ✕ am Marker entfernt ihn. Fehler bestätigt? Checkbox links "
            "(»Defekt«) anhaken.\n\n"
            "5. »Bestätigte Defekte ins Log schreiben« speichert die vollen Pfade. Über »Log öffnen« "
            "lassen sich diese Dateien löschen (Papierkorb), kopieren oder verschieben.\n\n"
            "Rechtsklick auf einen Track: weitere Aktionen (alle Marker entfernen, Marker "
            "wiederherstellen, im Explorer zeigen …)."))

    def closeEvent(self, e):
        self._closing = True
        self.player.stop()
        if self.scanner is not None:
            self.scanner.stop.set()
        self.db.close()
        super().closeEvent(e)
