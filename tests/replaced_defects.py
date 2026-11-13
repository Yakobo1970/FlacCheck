"""Ersetzte Defekt-Dateien: Ein als defekt bestaetigter Track, der unter gleichem Namen in einem
anderen Ordner neu eingelesen wurde (andere Dateigroesse), darf den Defekt nicht erben, sondern wird
frisch analysiert. Ein nur verschobener Defekt (gleiche Groesse) behaelt seinen Pruefstand.

Aufruf: python tests/replaced_defects.py <Arbeitsordner> <FLAC-Datei> <MP3-Datei>
Arbeitet nur mit Kopien und einer eigenen Test-Datenbank im Arbeitsordner.
"""
import os
import shutil
import sys
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from engine.db import Database
    from engine.scanner import Scanner

    work, flac, mp3 = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if work.exists():
        shutil.rmtree(work)
    a, b = work / "alt", work / "neu"
    a.mkdir(parents=True)
    b.mkdir()
    fa, ma = a / os.path.basename(flac), a / os.path.basename(mp3)
    shutil.copy2(flac, fa)
    shutil.copy2(mp3, ma)
    db = Database(work / "test.db")
    ok = True

    def check(name, cond):
        global ok
        ok &= bool(cond)
        print(("OK   " if cond else "FEHLER ") + name)

    def scan(targets):
        msgs = []
        Scanner(db, [str(t) for t in targets], 4, on_status=msgs.append, on_progress=lambda d, t: None,
                on_result=lambda fid, res: None).run()
        return msgs

    scan([a])
    ids = {os.path.basename(p): i for p, (i, *_ ) in db.path_index().items()}
    for fid in (ids[fa.name], ids[ma.name]):
        db.set_defect(fid, True)

    # MP3 nur verschieben, FLAC "neu einlesen": gleicher Ton, andere Kompression -> andere Groesse
    shutil.move(str(ma), str(b / ma.name))
    data, sr = sf.read(str(fa), dtype="int16")
    sf.write(str(b / fa.name), data, sr, format="FLAC", subtype="PCM_16", compression_level=0.0)
    os.remove(fa)
    check(f"Neu-Rip hat andere Größe ({os.path.getsize(b / fa.name)} statt {os.stat(flac).st_size})",
          os.path.getsize(b / fa.name) != os.stat(flac).st_size)

    msgs = scan([a, b])
    print("   Status:", msgs[-1] if msgs else "-")
    f_row, m_row = db.load_file(ids[fa.name]), db.load_file(ids[ma.name])
    check(f"Ersetztes FLAC zog um: {f_row.path}", f_row.path == str(b / fa.name))
    check(f"Ersetztes FLAC hat den Defekt nicht geerbt (defect={f_row.defect})", not f_row.defect)
    check(f"Ersetztes FLAC frisch analysiert (Status {f_row.status})", f_row.status in ("clean", "suspect"))
    check(f"Verschobenes MP3 zog um: {m_row.path}", m_row.path == str(b / ma.name))
    check(f"Verschobenes MP3 bleibt defekt (defect={m_row.defect})", m_row.defect)
    check("Statuszeile meldet die Neuprüfung", any("ersetzte Defekt-Dateien" in m for m in msgs))
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")
