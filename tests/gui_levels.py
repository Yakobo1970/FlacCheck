"""Stufenwechsel und Sortierung nach Fehlerart ohne neuen Scan; Screenshots je Stufe.

Aufruf: python tests/gui_levels.py <Datenbank> <Musikordner> <Screenshot-Praefix>
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    import gui.main_window as mw
    from gui.theme import apply_dark

    mw.DB_PATH = Path(sys.argv[1])
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow()
    w.resize(1500, 950)
    w.show()
    w.ed_root.setText(sys.argv[2])
    w.cb_sort.setCurrentIndex([k for k, _ in mw.SORTS].index("kind"))
    for name in ("niedrig", "mittel", "hoch"):
        t0 = time.perf_counter()
        w.cb_sens.setCurrentIndex(mw.SENSITIVITIES.index(name))
        app.processEvents()
        dt = time.perf_counter() - t0
        rows = w.view.model_.rows
        lvl = mw.SENSITIVITIES.index(name)
        firsts = []
        for r in rows[:6]:
            ks = [k for m in r.visible_markers(lvl) for k in m.visible_kinds(lvl)]
            firsts.append(ks[0] if ks else "-")
        print(f"[{name}] {len(rows)} Zeilen in {dt:.2f}s, erste Arten: {firsts} | {w.lbl_count.text()}")
        w.grab().save(f"{sys.argv[3]}_{name}.png")
    w.cb_kinds.set_checked({"jump"})
    app.processEvents()
    print("Filter Taktsprung (hoch):", len(w.view.model_.rows), "Zeilen")
    w.close()
