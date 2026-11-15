"""Defekte bleiben an ihrem Ursprungsort: Verschieben/Loeschen aus dem Log, Ersatz am gleichen Pfad,
Ersatz mit anderem Namen (gleiche Tracknummer), Pruefung durch den Nutzer, Anzeige in der Liste.

Aufruf: python tests/defect_origin.py <Arbeitsordner> <FLAC-Datei> <MP3-Datei> <Screenshot.png>
Arbeitet nur mit Kopien und einer eigenen Test-Datenbank im Arbeitsordner.
"""
import os
import shutil
import sys
import time
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from engine import fileops
    from engine.db import Database
    from engine.scanner import Scanner

    work, flac, mp3, shot = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    if work.exists():
        shutil.rmtree(work)
    root = work / "Alben (FLAC)"
    album = root / "Interpret" / "Interpret [1990] Album"
    album.mkdir(parents=True)
    a = album / "01 - Interpret - Erster Titel.flac"
    b = album / "02 - Interpret - Zweiter Titel.mp3"
    c = album / "03 - Interpret - Dritter Titel.flac"
    shutil.copy2(flac, a)
    shutil.copy2(mp3, b)
    shutil.copy2(flac, c)
    db = Database(work / "test.db")
    ok = True

    def check(name, cond):
        global ok
        ok &= bool(cond)
        print(("OK   " if cond else "FEHLER ") + name)

    def scan():
        msgs = []
        Scanner(db, [str(root)], 4, on_status=msgs.append, on_progress=lambda d, t: None,
                on_result=lambda fid, res: None).run()
        return msgs

    def row_of(fid):
        return db.load_file(fid)

    scan()
    ids = {os.path.basename(p): i for p, (i, *_ ) in db.path_index().items()}
    ia, ib, ic = ids[a.name], ids[b.name], ids[c.name]
    for fid in (ia, ib, ic):
        db.set_defect(fid, True)
    check("Log enthält alle drei Defekte", len(db.defect_paths()) == 3)

    # 1) aus dem Log verschieben (wie FileOpsDialog._transfer) und c in den Papierkorb (simuliert)
    trash = work / "DEFEKT"
    for src, tgt, err in fileops.move_files([str(a), str(b)], str(trash), keep_structure=True):
        assert not err, err
        db.set_moved(src, os.path.normpath(tgt))
    os.remove(c)
    db.set_moved(str(c), "(Papierkorb)")
    ra, rb = row_of(ia), row_of(ib)
    check(f"Eintrag bleibt am Ursprungsort: {os.path.basename(os.path.dirname(ra.path))}", ra.path == str(a))
    check("Eintrag merkt sich den neuen Ort der defekten Datei", ra.moved_to.startswith(str(trash)))
    check("Status 'fehlt' (blinkt in der Liste)", ra.missing and rb.missing and row_of(ic).missing)
    check("Fehlende Defekte kommen nicht ins Log", db.defect_paths() == [])
    check("Fehlende Defekte bleiben in der Befundliste", {ia, ib, ic} <= {r.id for r in db.load_findings()})

    # 2) Scan ohne Ersatz: nichts zieht in den DEFEKT-Ordner um, Eintraege bleiben stehen
    msgs = scan()
    check("Scan meldet wartende Defekte", any("Defekte, die auf Ersatz warten" in m for m in msgs))
    check("Nach dem Scan weiterhin am Ursprungsort", row_of(ia).path == str(a) and row_of(ia).missing)
    check("'Fehlende Dateien prüfen' räumt Defekte nicht weg", db.delete_missing() == 0 and row_of(ia))

    # 3) Ersatz unter gleichem Namen (neu eingelesen: gleicher Ton, andere Groesse)
    data, sr = sf.read(row_of(ia).moved_to, dtype="int16")
    sf.write(str(a), data, sr, format="FLAC", subtype="PCM_16", compression_level=0.0)
    # 4) Ersatz fuer b mit anderem Titel, gleiche Tracknummer
    b_new = album / "02 - Interpret - Zweiter Titel (Neu).mp3"
    shutil.copy2(mp3, b_new)
    scan()
    ra = row_of(ia)
    check(f"Ersatz am gleichen Pfad analysiert (Status {ra.status})", ra.status in ("clean", "suspect"))
    check("Ersatz bleibt als defekt markiert, bis geprüft", ra.defect and ra.replaced)
    check("Ungeprüfter Ersatz kommt nicht ins Log", str(a) not in db.defect_paths())
    check("Wellenform des Ersatzes gespeichert (zum Anhören)", db.load_peaks(ia))
    found = db.find_replacement(ib)
    check(f"Ersatz mit anderem Namen gefunden: {os.path.basename(found or '-')}", found == str(b_new))
    n_before = len(db.path_index())
    db.adopt_replacement(ib, found)
    check("Eigener Eintrag des Ersatzes wird aufgelöst", len(db.path_index()) == n_before - 1)
    scan()   # Status pending -> wird analysiert (in der App: sofortige Neuanalyse)
    rb = row_of(ib)
    check(f"Übernommener Ersatz analysiert, defekt+Ersatz ({rb.status})",
          rb.path == str(b_new) and rb.defect and rb.replaced and rb.status in ("clean", "suspect"))
    check("Kein Ersatz für c (gelöscht, nichts im Ordner)", db.find_replacement(ic) is None)

    # 5) Nutzer hoert den Ersatz an und entfernt den Haken
    db.set_defect(ia, False)
    ra = row_of(ia)
    check("Haken entfernt: nicht mehr defekt, kein Ersatz-Hinweis", not ra.defect and not ra.replaced
          and not ra.moved_to)

    # 6) Anzeige: Liste "Als defekt bestätigt" mit Ersatz oben und fehlender Datei
    from PySide6.QtWidgets import QApplication
    import gui.main_window as mw
    from gui.theme import apply_dark
    db.close()
    mw.DB_PATH = work / "test.db"
    mw.CONFIG_PATH = work / "config.json"
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow()
    w.resize(1500, 700)
    w.show()
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("defect"))
    for _ in range(20):
        app.processEvents()
        time.sleep(0.03)
    rows = w.view.model_.rows
    check(f"Defekt-Liste: {[('ERSATZ' if r.replaced else 'FEHLT' if r.missing else '-') for r in rows]}",
          [r.id for r in rows] == [ib, ic])
    w.view.delegate.blink_on = True
    w.view.viewport().update()
    app.processEvents()
    w.grab().save(shot)
    w.close()
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")
