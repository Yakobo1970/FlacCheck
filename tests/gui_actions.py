"""Testet die Pruefaktionen auf einer bereits gescannten Test-DB (siehe gui_smoke.py):
Loop-Wiedergabe, Marker entfernen, Defekt bestaetigen, Log, Kopieren/Verschieben/Loeschen.

Aufruf: python tests/gui_actions.py <Testdatenbank> <Arbeitsordner>
Arbeitet nur auf Kopien im Arbeitsordner.
"""
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    import gui.main_window as mw
    from engine import fileops

    db_file, work = Path(sys.argv[1]), Path(sys.argv[2])
    mw.DB_PATH = db_file
    app = QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    rows = w.view.model_.rows
    assert rows, "keine Befunde in der Test-DB"

    # 1) Loop-Wiedergabe eines Markers
    r = next(x for x in rows if x.active_markers)
    m = r.active_markers[0]
    w._play_marker(r, m)
    t0 = time.time()
    positions = []
    while time.time() - t0 < 1.5:
        app.processEvents()
        positions.append(w.view.model_.play_pos)
        time.sleep(0.03)
    print("Wiedergabe aktiv:", w.player.playing, "Position", round(max(positions), 2),
          "im Bereich", round(m.start, 1), "-", round(m.end, 1))
    assert w.player.playing and m.start <= max(positions) <= m.end
    w._play_marker(r, m)  # nochmal = Stopp
    app.processEvents()
    assert not w.player.playing
    print("Stopp per zweitem Klick: ok")

    # 2) Marker entfernen -> Zeile verschwindet aus "Offen", wenn kein Marker uebrig
    n_before = len(w.view.model_.rows)
    w._remove_marker(r, m)
    app.processEvents()
    still = [x for x in w.view.model_.rows if x.id == r.id]
    print("Marker entfernt; Zeile noch sichtbar:", bool(still), "Zeilen", n_before, "->",
          len(w.view.model_.rows))
    w.db.set_all_markers_removed(r.id, False)  # wiederherstellen
    w.reload()

    # 3) Zwei Dateien als defekt bestaetigen (Kopien im Arbeitsordner, damit Original unberuehrt)
    if work.exists():
        shutil.rmtree(work)
    (work / "quelle" / "sub").mkdir(parents=True)
    targets = []
    for i, x in enumerate(w.view.model_.rows[:2]):
        dst = work / "quelle" / ("sub" if i else "") / os.path.basename(x.path)
        shutil.copy2(x.path, dst)
        res = {"path": os.path.normpath(str(dst)), "ok": True, "duration": x.duration, "sr": x.sr,
               "markers": [{"start": 1, "end": 11, "center": 6, "kinds": ["click"], "score": 5,
                            "detail": "test"}]}
        st = dst.stat()
        fid = w.db.store_result(res, st.st_size, st.st_mtime, 99)
        w.db.set_defect(fid, True)
        targets.append(os.path.normpath(str(dst)))
    defect = w.db.defect_paths()
    assert set(targets) <= set(defect), defect
    log = work / "defekte.log"
    fileops.write_log(log, targets)
    print("Log geschrieben:", fileops.read_log(log) == targets)

    # 4) Kopieren mit Ordnerstruktur, Verschieben, Loeschen (Papierkorb)
    res = fileops.copy_files(targets, str(work / "kopie"), keep_structure=True)
    print("Kopiert:", [os.path.relpath(t, work) for _, t, e in res], [e for *_, e in res if e])
    res = fileops.move_files(targets, str(work / "verschoben"), keep_structure=True)
    for s, t, e in res:
        if not e:
            w.db.rename_path(s, os.path.normpath(t))
    moved = [os.path.normpath(t) for _, t, e in res if not e]
    print("Verschoben:", [os.path.relpath(t, work) for t in moved],
          "Quelle leer:", not any(os.path.exists(t) for t in targets),
          "DB aktualisiert:", set(moved) <= set(w.db.defect_paths()))
    res = fileops.delete_files(moved, permanent=False)
    for p, _, e in res:
        if not e:
            w.db.remove_path(p)
    print("Gelöscht (Papierkorb):", all(not os.path.exists(p) for p in moved),
          "Fehler:", [e for *_, e in res if e])
    w.close()
    print("ALLES OK")
