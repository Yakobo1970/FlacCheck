"""DB-Reset mit dem ECHTEN Bestaetigungsdialog, an einer DB-Kopie.

Aufruf: python tests/gui_reset_real.py <DB-Kopie> [knopf|menu|nein]
  knopf -- Knopf "DB zuruecksetzen", mit "Ja" bestaetigen
  menu  -- Menue Datei > Datenbank zuruecksetzen, mit "Ja" bestaetigen
  nein  -- Knopf, Dialog mit "Nein" beantworten (es darf nichts geloescht werden)
ACHTUNG: setzt die uebergebene Datenbank zurueck -- nur mit Kopien aufrufen.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    import gui.main_window as mw

    db_file = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "knopf"
    assert "copy" in db_file.name or "test" in db_file.name, "nur mit DB-Kopien testen!"
    mw.DB_PATH = db_file
    app = QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    app.processEvents()
    before = w.db.conn.execute("select count(*) from files").fetchone()[0]
    print("vorher:", w.lbl_count.text())

    def answer_dialogs():
        """Jeden aufgehenden Dialog beantworten: Rueckfrage mit Ja/Nein, Meldungen mit OK."""
        box = app.activeModalWidget()
        if isinstance(box, QMessageBox):
            text = box.text()[:80].replace("\n", " ")
            if box.button(QMessageBox.Yes):
                btn = box.button(QMessageBox.No if mode == "nein" else QMessageBox.Yes)
                print("Rückfrage:", text, "...", "->", btn.text())
            else:
                btn = box.button(QMessageBox.Ok)
                print("Meldung:", text)
            btn.click()
        QTimer.singleShot(100, answer_dialogs)

    QTimer.singleShot(200, answer_dialogs)
    if mode == "menu":
        act = next(a for a in w.menuBar().actions()[0].menu().actions() if "zurücksetzen" in a.text())
        act.trigger()
    else:
        w.btn_reset.click()
    app.processEvents()
    after = w.db.conn.execute("select count(*) from files").fetchone()[0]
    print("Status:", w.lbl_status.text())
    print("nachher:", w.lbl_count.text())
    ok = (after == before) if mode == "nein" else (after == 0 and w.view.model_.rowCount() == 0
                                                    and not w.db.scan_roots())
    print("DB-Dateien:", before, "->", after, "| Quellen:", len(w.db.scan_roots()),
          "|", "OK" if ok else "FEHLER")
    w.close()
