"""Ansicht 'Fehlerfrei': Sortierung zuletzt bearbeitet, Wiederherstellen, Neuanalyse, Liste leeren.

Aufruf: python tests/gui_clean.py <DB-Kopie> <Screenshot-Praefix>
ACHTUNG: veraendert die uebergebene Datenbank -- nur mit Kopien aufrufen.
"""
import os
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QMessageBox

    import gui.main_window as mw
    from gui.theme import apply_dark

    db_file = Path(sys.argv[1])
    shot = sys.argv[2]
    assert "copy" in db_file.name or "test" in db_file.name, "nur mit DB-Kopien testen!"
    mw.DB_PATH = db_file
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow()
    w.resize(1500, 950)
    w.show()
    w.cb_sens.setCurrentIndex(mw.SENSITIVITIES.index("mittel"))
    w.cb_filter.setCurrentIndex(w.cb_filter.findData("open"))
    app.processEvents()
    ok = True

    def check(name, cond):
        global ok
        ok &= bool(cond)
        print(("OK   " if cond else "FEHLER ") + name)

    # Zwei offene Tracks nacheinander freigeben (alle Marker per X entfernen)
    freed = []
    for r in list(w.view.model_.rows[:2]):
        for m in w.view.model_.visible(r):
            w._remove_marker(r, m)
            time.sleep(1.1)  # unterschiedliche Zeitstempel
        freed.append(r.id)
    app.processEvents()

    w.cb_filter.setCurrentIndex(w.cb_filter.findData("clean"))
    app.processEvents()
    rows = w.view.model_.rows
    n_hand = sum(1 for r in rows if r.status != "clean")
    print(f"Fehlerfrei: {len(rows)} Zeilen ({n_hand} von Hand freigegeben, {len(rows) - n_hand} ohne Befund) | "
          f"Sortierung: {w.cb_sort.currentText()} | Knopf sichtbar: {w.btn_clear_clean.isVisible()}")
    check("Sortierung springt auf 'Zuletzt bearbeitet'", w.cb_sort.currentData() == "recent")
    check("Zuletzt freigegebener Track steht oben", [r.id for r in rows[:2]] == freed[::-1])
    w.grab().save(f"{shot}_fehlerfrei.png")

    # Einzelnen Marker per Pfeil zurueckholen -> Track verlaesst 'Fehlerfrei'
    top = rows[0]
    removed = [m for m in w.view.model_.drawn(top) if m.removed]
    check("Entfernte Marker werden gezeichnet", len(removed) > 0)
    w._restore_marker(top, removed[0])
    app.processEvents()
    check("Wiederhergestellter Track verlaesst die Liste", top.id not in [r.id for r in w.view.model_.rows])

    # Jetzt neu analysieren: der zweite freigegebene Track bekommt frische Marker
    second = next(r for r in w.view.model_.rows if r.id == freed[0])
    w._reanalyze_now([second])
    t0 = time.time()
    while second.id in [r.id for r in w.view.model_.rows] and time.time() - t0 < 120:
        app.processEvents()
        time.sleep(0.1)
    row = w.db.load_file(second.id)
    check(f"Neu analysiert in {time.time() - t0:.1f}s, Marker wieder offen ({len(row.active_markers)})",
          row.active_markers and not row.removed_markers)

    # Liste leeren -- nur einen kleinen Ausschnitt (Suche) loeschen
    w.ed_search.setText("Bruno Bertone")
    w._search_timer.timeout.emit()
    app.processEvents()
    shown = list(w.view.model_.rows)
    before = w.db.conn.execute("select count(*) from files").fetchone()[0]
    with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
        w._clear_clean()
    check("Abbrechen loescht nichts", w.db.conn.execute("select count(*) from files").fetchone()[0] == before)
    with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
        w._clear_clean()
    after = w.db.conn.execute("select count(*) from files").fetchone()[0]
    check(f"Leeren entfernt genau die {len(shown)} angezeigten Tracks", before - after == len(shown))
    check("Bestaetigte Defekte bleiben", w.db.counts().get("defect") == 9)

    w.cb_filter.setCurrentIndex(w.cb_filter.findData("open"))
    app.processEvents()
    check("Zurueck zu 'Offen' stellt alte Sortierung her", w.cb_sort.currentData() != "recent")
    w.close()
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")
