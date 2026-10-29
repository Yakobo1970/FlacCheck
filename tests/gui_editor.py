"""Sonderliste + Bearbeitungsfenster von Anfang bis Ende, mit Kopien echter Dateien.

Aufruf: python tests/gui_editor.py <Arbeitsordner> <mp3> <flac> <Screenshot-Praefix>
Kopiert die Dateien in den Arbeitsordner und arbeitet nur dort (eigene Test-DB).
"""
import os
import shutil
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from PySide6.QtWidgets import QApplication, QMessageBox

    import gui.main_window as mw
    from gui.theme import apply_dark
    from engine import editing
    from engine.decode import probe

    work, mp3, flac, shot = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    if work.exists():
        shutil.rmtree(work)
    (work / "musik").mkdir(parents=True)
    src_mp3 = work / "musik" / os.path.basename(mp3)
    src_flac = work / "musik" / os.path.basename(flac)
    shutil.copy2(mp3, src_mp3)
    shutil.copy2(flac, src_flac)
    mw.DB_PATH = work / "test.db"
    mw.CONFIG_PATH = work / "config.json"
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow()
    w.resize(1500, 900)
    w.show()
    ok = True

    def check(name, cond):
        global ok
        ok &= bool(cond)
        print(("OK   " if cond else "FEHLER ") + name)

    def wait(cond, timeout=180):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            app.processEvents()
            time.sleep(0.05)

    # 1) Scan, alle Arten/Stufe hoch, dann MP3 parken
    w.cb_sens.setCurrentIndex(mw.SENSITIVITIES.index("hoch"))
    w.cb_kinds.set_checked(set(w.cb_kinds.values()))
    w._start_scan([str(work / "musik")])
    wait(lambda: w.scanner is None)
    ids = {os.path.basename(p): i for p, (i, *_ ) in w.db.path_index().items()}
    mp3_id = ids[src_mp3.name]
    w.db.set_parked([mp3_id], True)
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("parked"))
    app.processEvents()
    check("Sonderliste zeigt das geparkte MP3", [r.id for r in w.view.model_.rows] == [mp3_id])
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("everything"))
    app.processEvents()
    check(f"'Alle Listen' zeigt beide Dateien ({w.view.model_.rowCount()})", w.view.model_.rowCount() == 2)
    for f in ("open", "all", "clean", "defect"):
        w.cb_filter.setCurrentIndex(w.cb_filter.findData(f))
        app.processEvents()
        if any(r.id == mp3_id for r in w.view.model_.rows):
            check(f"Geparktes erscheint nicht in '{f}'", False)

    # 2) Bearbeiten: teilen + herausschneiden, Namen/Titel aendern, speichern
    row = w.db.load_file(mp3_id)
    from gui.editor import EditorWindow
    ed = EditorWindow(row, [], w)
    ed.saved.connect(w._on_edited)
    ed.show()
    wait(lambda: ed.wave.peaks.shape[0] > 0, 60)
    D = ed.wave.duration
    ed.wave.cursor = D * 0.5
    ed._split()
    ed.wave.sel = (D * 0.2, D * 0.2 + 10.0)
    ed._cut()
    check(f"Teiltabelle: {ed.table.rowCount()} Teile", ed.table.rowCount() == 2)
    ed.table.item(0, 3).setText("Intro-Teil.mp3")
    ed.table.item(0, 4).setText("Mein neuer Titel")
    app.processEvents()
    ed.chk_keep.setChecked(True)   # MP3: Original behalten (umbenannt im Ordner)
    ed.grab().save(f"{shot}_editor.png")
    infos, asks = [], []
    with mock.patch.object(QMessageBox, "question", side_effect=lambda *a: (asks.append(a[2]), QMessageBox.Yes)[1]), \
            mock.patch.object(QMessageBox, "information", side_effect=lambda *a: infos.append(a[2])):
        ed._save()
    app.processEvents()
    p1 = src_mp3.parent / "Intro-Teil.mp3"
    p2 = src_mp3.parent / f"{src_mp3.stem} (Teil 2).mp3"
    check("Beide Teile geschrieben", p1.exists() and p2.exists())
    kept = src_mp3.parent / f"(defekt) {src_mp3.name}"
    check("Original behalten als '(defekt) …'", kept.exists() and not src_mp3.exists())
    check("Abfrage nennt Originalordner", asks and "bleibt im Originalordner" in asks[-1])
    check(f"Kein zweites Info-Fenster ({len(infos)})", not infos)
    d1 = probe(str(p1)).frames / probe(str(p1)).sr
    check(f"Teil 1 Dauer {d1:.1f} s ≈ {D * 0.5 - 10:.1f} s", abs(d1 - (D * 0.5 - 10)) < 0.1)
    t1 = editing.read_tags(str(p1))
    check(f"Teil 1 Titel '{t1.get('title')}', Interpret '{t1.get('artist')}'",
          t1.get("title") == "Mein neuer Titel" and t1.get("artist"))
    check(f"Speichern-Button hieß '{ed.b_save.text()}'", ed.b_save.text() == "2 Teilstücke speichern")
    check("Checkbox 'Original nicht löschen/überschreiben', grundsätzlich aus",
          EditorWindow(row, [], w).chk_keep.isChecked() is False)
    # 3) Teile werden ohne Zutun neu bewertet und bleiben geparkt (mit Wellenform zum Anhoeren)
    ids2 = {os.path.basename(p): i for p, (i, *_ ) in w.db.path_index().items()}
    part_ids = [ids2[p1.name], ids2[p2.name]]
    wait(lambda: all(w.db.load_file(i).status != "pending" for i in part_ids), 180)
    for _ in range(20):
        app.processEvents(); time.sleep(0.05)
    st = [w.db.load_file(i) for i in part_ids]
    check(f"Automatisch neu bewertet: Status {[x.status for x in st]}",
          all(x.status in ("clean", "suspect") for x in st))
    check("Teile bleiben in der Sonderliste", all(x.parked for x in st))
    pk = w.db.load_peaks(part_ids[0]) if hasattr(w.db, "load_peaks") else None
    check("Wellenform gespeichert", pk is not None and len(pk) > 0)
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("parked"))
    app.processEvents()
    names = sorted(os.path.basename(r.path) for r in w.view.model_.rows)
    check(f"Teile stehen in der Sonderliste: {names}", "Intro-Teil.mp3" in names and p2.name in names)
    w.grab().save(f"{shot}_sonderliste.png")

    # 4) Nur kuerzen (FLAC, nicht geparkt): Button-Text
    frow = w.db.load_file(ids[src_flac.name])
    ed2 = EditorWindow(frow, [], w)
    ed2.show()
    wait(lambda: ed2.wave.peaks.shape[0] > 0, 60)
    ed2.wave.sel = (5.0, 15.0)
    ed2._cut()
    check(f"Nur gekürzt: Button '{ed2.b_save.text()}'", ed2.b_save.text() == "1 gekürzte Datei speichern")
    ed2.grab().save(f"{shot}_kuerzen.png")
    # Zielordner-Dialog zeigt auch Dateien
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QListView
    seen = {}

    def look():
        dlg = ed2._folder_dialog
        app.processEvents(); time.sleep(0.5); app.processEvents()
        lv = dlg.findChild(QListView, "listView")
        m = lv.model()
        root = lv.rootIndex()
        seen["names"] = [m.data(m.index(r, 0, root)) for r in range(m.rowCount(root))]
        dlg.grab().save(f"{shot}_ordnerdialog.png")
        dlg.reject()
    QTimer.singleShot(800, look)
    ed2._choose_folder()
    check(f"Ordnerdialog zeigt Dateien: {seen.get('names')}",
          any(str(n).lower().endswith((".flac", ".mp3")) for n in seen.get("names", [])))
    # Standard: Original umbenannt in den Papierkorb (send2trash simuliert)
    trashed = []
    infos.clear(); asks.clear()
    with mock.patch.object(QMessageBox, "question", side_effect=lambda *a: (asks.append(a[2]), QMessageBox.Yes)[1]),             mock.patch.object(QMessageBox, "information", side_effect=lambda *a: infos.append(a[2])),             mock.patch("send2trash.send2trash", side_effect=lambda p: (trashed.append(p), os.remove(p))):
        ed2._save()
    app.processEvents()
    check(f"Gekürztes FLAC unter Originalnamen: {src_flac.exists()}", src_flac.exists())
    check(f"Papierkorb bekam '(defekt) …': {[os.path.basename(t) for t in trashed]}",
          [os.path.basename(t) for t in trashed] == [f"(defekt) {src_flac.name}"])
    check("Abfrage nennt Papierkorb", asks and "Papierkorb" in asks[-1])
    check(f"Kein zweites Info-Fenster ({len(infos)})", not infos)
    d2 = probe(str(src_flac)).frames / probe(str(src_flac)).sr
    check(f"FLAC um 10 s kürzer ({d2:.1f} s)", abs(d2 - (ed2.wave.duration - 10)) < 0.1)
    ed2.close()
    w.close()
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")
