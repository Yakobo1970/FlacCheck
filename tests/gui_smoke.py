"""GUI-Rauchtest: Scan eines Ordners mit isolierter DB, danach Screenshot.

Aufruf: python tests/gui_smoke.py <Musikordner> <screenshot.png> [Testdatenbank]
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    from PySide6.QtWidgets import QApplication

    import gui.main_window as mw
    from gui.theme import apply_dark

    root, shot = sys.argv[1], sys.argv[2]
    db_file = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp()) / "test.db"
    mw.DB_PATH = db_file
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow()
    w.resize(1500, 950)
    w.show()
    w.ed_root.setText(root)
    w._toggle_scan()
    t0 = time.time()
    while w.scanner is not None and time.time() - t0 < 600:
        app.processEvents()
        time.sleep(0.05)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    print("Status:", w.lbl_status.text(), "|", w.lbl_count.text())
    m = w.view.model_
    for r in m.rows:
        print(f"  {os.path.basename(r.path)}: {len(r.active_markers)} Marker, "
              f"{[(round(x.start, 1), x.kinds) for x in r.active_markers]}")
    w.grab().save(shot)
    w.close()
