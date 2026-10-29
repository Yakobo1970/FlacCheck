"""Scanquellen- und Fehlerarten-Mehrfachauswahl, verzoegertes Laden und DB-Reset an einer DB-Kopie.

Aufruf: python tests/gui_scope.py <DB-Kopie> <Screenshot-Praefix>
ACHTUNG: setzt die uebergebene Datenbank am Ende zurueck -- nur mit Kopien aufrufen.
"""
import os
import sys
import time
import tracemalloc
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    import gui.main_window as mw
    from gui.theme import apply_dark

    db_file = Path(sys.argv[1])
    shot = sys.argv[2]
    assert "copy" in db_file.name or "test" in db_file.name, "nur mit DB-Kopien testen!"
    mw.DB_PATH = db_file
    app = QApplication(sys.argv)
    apply_dark(app)
    tracemalloc.start()
    t0 = time.perf_counter()
    w = mw.MainWindow()
    w.resize(1500, 950)
    w.show()
    app.processEvents()
    print(f"Start + Laden: {time.perf_counter() - t0:.2f}s, Python-Speicher {tracemalloc.get_traced_memory()[1] / 1e6:.0f} MB")
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("defect"))
    app.processEvents()

    src, kinds = w.cb_sources, w.cb_kinds
    print("Quellen:", src.values())
    print("Alle Quellen:", len(w.view.model_.rows), "Defekte |", src.currentText() or src.placeholderText())

    # Einzelne Quelle per Haekchen abwaehlen (wie ein Klick in der Liste)
    src._model.item(1).setCheckState(Qt.Unchecked)
    app.processEvents()
    print("Ohne _A_:", len(w.view.model_.rows), "Defekte |", src.placeholderText(), "|", w.lbl_count.text())
    src._model.item(0).setCheckState(Qt.Unchecked)        # "Alle" aus -> nichts
    app.processEvents()
    print("Keine Quelle:", len(w.view.model_.rows), "|", src.placeholderText())
    src._model.item(0).setCheckState(Qt.Checked)          # "Alle" an -> alles
    app.processEvents()
    print("Alle wieder:", len(w.view.model_.rows), "|", src.placeholderText())

    # Fehlerarten: nur Fehlrauschen/Stottern + Aussetzer
    kinds.set_checked({"glitch", "dropout"})
    app.processEvents()
    print("Nur Stottern+Aussetzer:", len(w.view.model_.rows), "Defekte |", kinds.placeholderText())
    print("Gespeichert abgewaehlt:", w.db.get_setting("kinds_off"), w.db.get_setting("sources_off"))
    w.grab().save(f"{shot}_fenster.png")
    kinds.showPopup()
    app.processEvents()
    kinds.view().window().grab().save(f"{shot}_liste.png")
    kinds.hidePopup()
    kinds.set_checked(set(kinds.values()))

    # Reset: erst abbrechen, dann bestaetigen
    with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.No):
        w._reset_db()
    print("Nach Abbruch noch Dateien:", w.db.conn.execute("select count(*) from files").fetchone()[0])
    with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Yes):
        w._reset_db()
    print("Nach Reset Dateien:", w.db.conn.execute("select count(*) from files").fetchone()[0],
          "| Quellen:", src.values(), "|", w.lbl_count.text())
    w.close()
    print("DB-Datei nach Reset:", os.path.getsize(db_file) // 1024, "KB")
