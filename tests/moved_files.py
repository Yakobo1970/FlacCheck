"""Verschobene Dateien: Scan des neuen Orts zieht Eintraege samt Pruefstand um.

Aufruf: python tests/moved_files.py <DB-Kopie> <Ordner1> [<Ordner2> ...]
ACHTUNG: veraendert die uebergebene Datenbank -- nur mit Kopien aufrufen.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from engine.db import Database
    from engine.scanner import Scanner

    db_file, targets = Path(sys.argv[1]), sys.argv[2:]
    assert "copy" in db_file.name or "test" in db_file.name, "nur mit DB-Kopien testen!"
    db = Database(db_file)

    def snapshot():
        rows = {}
        for fid, path, rev, df, st in db.conn.execute("SELECT id, path, reviewed, defect, status FROM files"):
            rm = db.conn.execute("SELECT COUNT(*), SUM(removed) FROM markers WHERE file_id=?", (fid,)).fetchone()
            rows[fid] = (path, rev, df, st, rm[0], rm[1] or 0)
        return rows

    before = snapshot()
    gone = {g[0] for g in db.find_missing()}
    print(f"vorher: {len(before)} Eintraege, {len(gone)} zeigen auf fehlende Dateien")
    msgs, results = [], []
    sc = Scanner(db, targets, 8, on_status=msgs.append, on_progress=lambda d, t: None,
                 on_result=lambda fid, res: results.append(fid))
    t0 = time.time()
    sc.run()
    print(f"Scan: {time.time() - t0:.1f} s")
    for m in msgs:
        print("   Status:", m)
    after = snapshot()
    moved = [fid for fid in gone if fid in after and after[fid][0] != before[fid][0]]
    kept = all(before[f][1:3] == after[f][1:3] and before[f][4:] == after[f][4:] for f in moved)
    print(f"umgezogen: {len(moved)} von {len(gone)} | Pruefstand (geprueft, defekt, Marker, entfernte) "
          f"erhalten: {'ja' if kept else 'NEIN'}")
    for f in moved[:4]:
        print(f"   {os.path.basename(before[f][0])[:55]:55} -> {os.path.dirname(after[f][0])}")
        print(f"      Status {before[f][3]}->{after[f][3]}, geprueft {after[f][1]}, Marker {after[f][4]}, entfernt {after[f][5]}")
    reanalyzed = [f for f in moved if f in results]
    print("davon trotzdem neu analysiert:", len(reanalyzed))
    still = db.conn.execute("SELECT COUNT(*) FROM files WHERE status='missing'").fetchone()[0]
    print("weiterhin fehlend:", still)
    db.close()
