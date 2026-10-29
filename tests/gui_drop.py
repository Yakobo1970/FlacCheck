"""Defekte ohne Befund sichtbar + Drag & Drop von Ordnern/Dateien, an einer DB-Kopie.

Aufruf: python tests/gui_drop.py <DB-Kopie> <Ordner zum Ziehen> <Datei zum Ziehen> <Screenshot>
ACHTUNG: veraendert die uebergebene Datenbank -- nur mit Kopien aufrufen.
"""
import os
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QApplication, QMessageBox

    import gui.main_window as mw
    from gui.theme import apply_dark

    db_file, folder, single, shot = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    assert "copy" in db_file.name or "test" in db_file.name, "nur mit DB-Kopien testen!"
    mw.DB_PATH = db_file
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow()
    w.resize(1500, 950)
    w.show()
    ok = True

    def check(name, cond):
        global ok
        ok &= bool(cond)
        print(("OK   " if cond else "FEHLER ") + name)

    # 1) Defekte ohne aktuellen Befund
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("defect"))
    app.processEvents()
    names = [os.path.basename(r.path) for r in w.view.model_.rows]
    check(f"Defekt-Liste zeigt {len(names)} Titel inkl. Aguilera",
          any("The Way You Talk to Me" in n for n in names))
    agu = next(r for r in w.view.model_.rows if "The Way You Talk to Me" in r.path)
    w.view.scrollTo(w.view.model_.index(w.view.model_.row_of(agu.id)))
    app.processEvents()
    w.grab().save(shot)
    print("    Zaehler:", w.lbl_count.text())

    # 2) Drop: ein Ordner + eine Einzeldatei
    def drop(paths):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        ev = QDropEvent(QPointF(200, 300), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
        w.dropEvent(ev)
        app.processEvents()

    before = w.db.conn.execute("select count(*) from files").fetchone()[0]
    drop([folder, single, os.path.join(folder, "keine_audiodatei.txt")])
    check("Scan nach Drop gestartet", w.scanner is not None)

    # 3) zweiter Drop waehrend des Scans -> Hinweis, kein zweiter Scan
    with mock.patch.object(QMessageBox, "information") as info:
        drop([single])
        check("Drop waehrend Scan wird abgewiesen", info.called)
    t0 = time.time()
    while w.scanner is not None and time.time() - t0 < 300:
        app.processEvents()
        time.sleep(0.05)
    after = w.db.conn.execute("select count(*) from files").fetchone()[0]
    n_folder = sum(1 for dp, dn, fn in os.walk(folder) for f in fn
                   if os.path.splitext(f)[1].lower() in mw.AUDIO_EXTENSIONS)
    print(f"    Dateien in DB: {before} -> {after} (Ordner hat {n_folder} Audiodateien, + 1 Einzeldatei)")
    check("Ordner und Einzeldatei analysiert",
          w.db.conn.execute("select count(*) from files where path=?", (os.path.normpath(single),)).fetchone()[0] == 1)
    roots = w.db.scan_roots()
    check("Ordner und Ordner der Einzeldatei als Quellen gemerkt",
          os.path.normpath(folder) in roots and os.path.dirname(os.path.normpath(single)) in roots)
    print("    Status:", w.lbl_status.text())
    w.close()
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")
