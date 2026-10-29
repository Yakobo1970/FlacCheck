"""Bearbeitungsfenster: Teilstuecke per Kontrollkaestchen weglassen (z. B. Stille vor Bonustrack).

Aufruf: python tests/editor_parts.py <Arbeitsordner> <Audiodatei> <Screenshot-Praefix>
Arbeitet nur auf einer Kopie im Arbeitsordner.
"""
import os
import shutil
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from engine.db import FileRow  # noqa: E402
from engine.decode import probe  # noqa: E402
from gui.theme import apply_dark  # noqa: E402


def main():
    work, src, shot = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    path = work / os.path.basename(src)
    shutil.copy2(src, path)
    app = QApplication([])
    apply_dark(app)
    from gui.editor import EditorWindow
    ok = True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("OK   " if cond else "FEHLER ") + name)

    def pump(n=10):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    info = probe(str(path))
    row = FileRow(id=1, path=str(path), duration=info.frames / info.sr, sr=info.sr, channels=info.channels,
                  codec=info.codec, tempo=0.0, status="clean", defect=False, reviewed=False, peaks=None, error="")
    ed = EditorWindow(row, [], None)
    ed.resize(1500, 900)
    ed.show()
    t0 = time.time()
    while ed.wave.peaks.shape[0] == 0 and time.time() - t0 < 60:
        pump(1)
    D = ed.wave.duration
    s1, s2 = round(D * 0.4, 1), round(D * 0.7, 1)
    for s in (s1, s2):
        ed.wave.cursor = s
        ed._split()
    pump()
    sc = ed.save_col
    states = [ed.table.item(r, sc).checkState() == Qt.Checked for r in range(ed.table.rowCount())]
    check(f"3 Teile, alle zum Speichern vorgemerkt: {states}", states == [True, True, True])

    ed.table.item(1, sc).setCheckState(Qt.Unchecked)   # wie ein Klick ins Kaestchen
    pump()
    check(f"Mittelteil als entfernt markiert: {ed.wave.removed}", ed.wave.removed == [(s1, s2)])
    check(f"Zeile 2 'komplett entfernt', Haken aus",
          ed.table.item(1, 1).text() == "komplett entfernt" and ed.table.item(1, sc).checkState() == Qt.Unchecked)
    check(f"Button '{ed.b_save.text()}'", ed.b_save.text() == "2 Teilstücke speichern")
    ed.grab().save(f"{shot}_weglassen.png")

    ed.table.item(1, sc).setCheckState(Qt.Checked)
    pump()
    check(f"Haken wieder an: nichts mehr entfernt {ed.wave.removed}", ed.wave.removed == [])
    ed._undo_step()
    pump()
    check(f"Rückgängig holt das Weglassen zurück: {ed.wave.removed}", ed.wave.removed == [(s1, s2)])

    # Mittelteil mit vorher markiertem Stueck darin: Haken aus fasst zusammen, Haken an gibt nur ihn frei
    ed._reset()
    pump()
    for s in (s1, s2):
        ed.wave.cursor = s
        ed._split()
    ed.wave.sel = (2.0, 5.0)
    ed._cut()
    pump()
    ed.table.item(1, sc).setCheckState(Qt.Unchecked)
    pump()
    ed.table.item(1, sc).setCheckState(Qt.Checked)
    pump()
    check(f"Fremde Entfernung bleibt erhalten: {ed.wave.removed}", ed.wave.removed == [(2.0, 5.0)])
    ed.table.item(1, sc).setCheckState(Qt.Unchecked)
    pump()

    asks = []
    with mock.patch.object(QMessageBox, "question", side_effect=lambda *a: (asks.append(a[2]), QMessageBox.Yes)[1]), \
            mock.patch("send2trash.send2trash", side_effect=lambda p: os.remove(p)):
        ed._save()
    pump()
    out = sorted(p for p in os.listdir(work) if not p.startswith("(defekt)"))
    check(f"Zwei Dateien geschrieben: {out}", len(out) == 2)
    durs = sorted(probe(str(work / n)).frames / probe(str(work / n)).sr for n in out)
    exp = sorted([s1 - 3.0, D - s2])
    check(f"Dauern {[round(d, 1) for d in durs]} ≈ {[round(e, 1) for e in exp]}",
          all(abs(d - e) < 0.1 for d, e in zip(durs, exp)))
    check(f"Namen durchnummeriert: {out}", any("(Teil 1)" in n for n in out) and any("(Teil 2)" in n for n in out))
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")


if __name__ == "__main__":
    main()
