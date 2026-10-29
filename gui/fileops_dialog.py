"""Dialog: Logdatei laden, Eintraege auswaehlen, loeschen/kopieren/verschieben."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from engine import fileops


class FileOpsDialog(QDialog):
    def __init__(self, db, log_path: str | None, parent=None):
        super().__init__(parent)
        self.db = db
        self.log_path = log_path
        self.setWindowTitle("Defekte Dateien – Aktionen")
        self.resize(980, 560)
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.lbl_log = QLabel()
        self.lbl_log.setTextInteractionFlags(Qt.TextSelectableByMouse)
        btn_open = QPushButton("Andere Logdatei …")
        btn_open.clicked.connect(self._choose_log)
        top.addWidget(self.lbl_log, 1)
        top.addWidget(btn_open)
        lay.addLayout(top)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        lay.addWidget(self.list, 1)

        sel = QHBoxLayout()
        b_all = QPushButton("Alle auswählen")
        b_none = QPushButton("Keine")
        b_all.clicked.connect(lambda: self._check_all(True))
        b_none.clicked.connect(lambda: self._check_all(False))
        self.chk_struct = QCheckBox("Ordnerstruktur beibehalten")
        self.chk_struct.setChecked(True)
        self.chk_struct.setToolTip("Unterordner relativ zum gemeinsamen Elternordner im Ziel nachbilden")
        self.chk_perm = QCheckBox("Endgültig löschen (statt Papierkorb)")
        sel.addWidget(b_all)
        sel.addWidget(b_none)
        sel.addStretch(1)
        sel.addWidget(self.chk_struct)
        sel.addWidget(self.chk_perm)
        lay.addLayout(sel)

        act = QHBoxLayout()
        self.btn_copy = QPushButton("Kopieren nach …")
        self.btn_move = QPushButton("Verschieben nach …")
        self.btn_del = QPushButton("Löschen")
        self.btn_del.setStyleSheet("QPushButton { color: #ff8a80; }")
        b_close = QPushButton("Schließen")
        self.btn_copy.clicked.connect(lambda: self._transfer(move=False))
        self.btn_move.clicked.connect(lambda: self._transfer(move=True))
        self.btn_del.clicked.connect(self._delete)
        b_close.clicked.connect(self.accept)
        act.addWidget(self.btn_copy)
        act.addWidget(self.btn_move)
        act.addWidget(self.btn_del)
        act.addStretch(1)
        act.addWidget(b_close)
        lay.addLayout(act)
        self._load()

    # ------------------------------------------------------------
    def _choose_log(self):
        p, _ = QFileDialog.getOpenFileName(self, "Logdatei öffnen", os.path.dirname(self.log_path or ""),
                                           "Logdateien (*.log *.txt);;Alle Dateien (*.*)")
        if p:
            self.log_path = p
            self._load()

    def _load(self):
        self.list.clear()
        if not self.log_path or not os.path.exists(self.log_path):
            self.lbl_log.setText("Keine Logdatei geladen.")
            return
        self.lbl_log.setText(f"Logdatei: {self.log_path}")
        for p in fileops.read_log(self.log_path):
            it = QListWidgetItem(p)
            exists = os.path.exists(p)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if exists else Qt.Unchecked)
            if not exists:
                it.setText(p + "   (nicht mehr vorhanden)")
                it.setForeground(Qt.gray)
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
            it.setData(Qt.UserRole, p)
            self.list.addItem(it)

    def _check_all(self, on: bool):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.flags() & Qt.ItemIsEnabled:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _selected(self) -> list[str]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked and (it.flags() & Qt.ItemIsEnabled):
                out.append(it.data(Qt.UserRole))
        return out

    def _rewrite_log(self, replace: dict[str, str | None]):
        """Log nach Aktion aktualisieren: verschobene Pfade ersetzen, geloeschte entfernen."""
        paths = []
        for p in fileops.read_log(self.log_path):
            if p in replace:
                if replace[p]:
                    paths.append(replace[p])
            else:
                paths.append(p)
        fileops.write_log(self.log_path, paths)

    def _report(self, title, results):
        errors = [(p, e) for p, _, e in results if e]
        ok = len(results) - len(errors)
        msg = f"{ok} von {len(results)} Dateien erfolgreich."
        if errors:
            msg += "\n\nFehler:\n" + "\n".join(f"{p}: {e}" for p, e in errors[:15])
        QMessageBox.information(self, title, msg)

    def _transfer(self, move: bool):
        paths = self._selected()
        if not paths:
            return
        dest = QFileDialog.getExistingDirectory(self, "Zielordner wählen")
        if not dest:
            return
        verb = "verschieben" if move else "kopieren"
        if QMessageBox.question(self, "Bestätigen",
                                f"{len(paths)} Dateien nach\n{dest}\n{verb}?") != QMessageBox.Yes:
            return
        fn = fileops.move_files if move else fileops.copy_files
        results = fn(paths, dest, self.chk_struct.isChecked())
        if move:
            for src, tgt, err in results:
                if not err:
                    self.db.rename_path(src, os.path.normpath(tgt))
            self._rewrite_log({s: os.path.normpath(t) for s, t, e in results if not e})
        self._report("Verschieben" if move else "Kopieren", results)
        self._load()

    def _delete(self):
        paths = self._selected()
        if not paths:
            return
        perm = self.chk_perm.isChecked()
        how = "ENDGÜLTIG löschen (nicht wiederherstellbar)" if perm else "in den Papierkorb verschieben"
        if QMessageBox.warning(self, "Löschen bestätigen", f"{len(paths)} Dateien {how}?",
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        results = fileops.delete_files(paths, permanent=perm)
        for p, _, err in results:
            if not err:
                self.db.remove_path(p)
        self._rewrite_log({p: None for p, _, e in results if not e})
        self._report("Löschen", results)
        self._load()
