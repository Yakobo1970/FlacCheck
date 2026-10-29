"""Ausgegraute Marker, Abspielen bis zum Ende, Legende mit Anzahl, Datenbank sichern/neu/wechseln.

Aufruf: python tests/gui_v010.py <DB-Kopie> <Arbeitsordner> <Screenshot>
Arbeitet nur mit Kopien; die echte config.json wird nicht angefasst.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    import gui.main_window as mw
    import gui.player as player_mod
    from gui.theme import apply_dark

    db_file, work, shot = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    assert "copy" in db_file.name or "test" in db_file.name, "nur mit DB-Kopien testen!"
    work.mkdir(parents=True, exist_ok=True)
    mw.DB_PATH = db_file
    mw.CONFIG_PATH = work / "config.json"
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

    # 1) Offen-Ansicht: ausgegraute Marker werden mitgezeichnet
    w.cb_sens.setCurrentIndex(mw.SENSITIVITIES.index("hoch"))
    w.cb_kinds.set_checked(set(w.cb_kinds.values()))
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("open"))
    app.processEvents()
    m = w.view.model_
    mixed = [r for r in m.rows if any(x.removed for x in m.drawn(r)) and m.visible(r)]
    check(f"Offen: {len(mixed)} Tracks zeigen ausgegraute neben offenen Markern", mixed)
    if mixed:
        w.view.scrollTo(m.index(m.row_of(mixed[0].id)))
        app.processEvents()
    w.grab().save(shot)
    print("    Legende:", w.lbl_legend.text().replace("<span style='color:", "[").replace("'>■</span>", "]")[:240])
    check("Legende enthält Anzahlen", "(" in w.lbl_legend.text())

    # 2) Abspielen bis zum Ende, nachladend (kleine Bloecke, damit mehrere Grenzen passiert werden)
    player_mod.CHUNK_S = 1.0
    r = m.rows[0]
    w._play_at(r, 30.0)
    t0 = time.time()
    while time.time() - t0 < 3.5:
        app.processEvents()
        time.sleep(0.02)
    pos = m.play_pos
    check(f"Wiedergabe ohne Schleife läuft weiter über Blockgrenzen (Position {pos:.1f} s)",
          not w.player.loop and pos > 32.5)
    w.player.stop()
    app.processEvents()

    # 3) Datenbank sichern, neu anlegen, zurueckwechseln
    n_before = w.db.conn.execute("select count(*) from files").fetchone()[0]
    backup = work / "sicherung_test.db"
    if backup.exists():
        backup.unlink()
    w.db.backup_to(backup)
    check("Sicherung angelegt", backup.exists() and backup.stat().st_size > 0)
    new = work / "neu_test.db"
    for x in ("", "-wal", "-shm"):
        if Path(str(new) + x).exists():
            os.remove(str(new) + x)
    w._switch_db(new)
    check("Neue DB ist leer", w.db.conn.execute("select count(*) from files").fetchone()[0] == 0
          and w.view.model_.rowCount() == 0)
    check("Fenstertitel zeigt DB-Namen", "neu_test.db" in w.windowTitle())
    w._switch_db(backup)
    check("Sicherung geöffnet, alle Einträge da",
          w.db.conn.execute("select count(*) from files").fetchone()[0] == n_before)
    import json
    check("config.json merkt sich die geöffnete DB",
          json.loads(mw.CONFIG_PATH.read_text(encoding="utf-8")).get("db_path") == str(backup))
    w._switch_db(db_file)
    w.close()
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")
