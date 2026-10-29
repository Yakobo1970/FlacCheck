"""Klickverhalten der Haekchen-Klappliste mit simulierten Mausereignissen."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QWidget

    from gui.check_combo import CheckCombo
    from gui.theme import apply_dark

    app = QApplication(sys.argv)
    apply_dark(app)
    host = QWidget()
    host.resize(400, 80)
    cb = CheckCombo("Alle Fehlerarten", "Fehlerarten", host)
    cb.set_items([("Taktsprung", "jump"), ("Knackser", "click"), ("Rauschstoß", "noise")])
    host.show()
    QTest.qWaitForWindowExposed(host)
    changes = []
    cb.selection_changed.connect(lambda: changes.append(sorted(cb.checked_values())))
    vp = cb.view().viewport()

    def row_points(row):
        r = cb.view().visualRect(cb._model.index(row, 0))
        opt = QStyleOptionViewItem()
        opt.initFrom(cb.view())
        opt.rect = r
        opt.features = QStyleOptionViewItem.HasCheckIndicator | QStyleOptionViewItem.HasDisplay
        box = cb.view().style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt, cb.view())
        return box.center(), QPoint(r.right() - 20, r.center().y()), box

    def check(name, cond):
        print(("OK   " if cond else "FEHLER ") + name)
        return cond

    ok = True
    # 1) Feld anklicken: Druecken oeffnet, Loslassen ueber Eintrag darf nichts umschalten
    QTest.mousePress(cb, Qt.LeftButton, pos=cb.rect().center())
    app.processEvents()
    ok &= check("Liste geoeffnet", cb.view().isVisible())
    box1, text1, rect1 = row_points(1)
    print("     Kaestchen Zeile 1:", rect1.getRect(), "Text-Klickpunkt:", text1.toTuple())
    QTest.mouseRelease(vp, Qt.LeftButton, pos=box1)
    app.processEvents()
    ok &= check("Loslassen nach dem Oeffnen schaltet nicht um", changes == [])

    # 2) Klick auf den Namen: nichts aendern, Liste bleibt offen
    QTest.mouseClick(vp, Qt.LeftButton, pos=text1)
    app.processEvents()
    ok &= check("Klick auf Namen aendert nichts", changes == [])
    ok &= check("Liste bleibt offen", cb.view().isVisible())

    # 3) Klick ins Kaestchen: umschalten, Liste bleibt offen
    QTest.mouseClick(vp, Qt.LeftButton, pos=box1)
    app.processEvents()
    ok &= check("Klick ins Kaestchen schaltet Taktsprung ab", changes[-1:] == [["click", "noise"]])
    ok &= check("Liste bleibt nach Haekchen offen", cb.view().isVisible())

    # 4) Druecken im Kaestchen, Loslassen woanders: nichts
    n = len(changes)
    QTest.mousePress(vp, Qt.LeftButton, pos=box1)
    QTest.mouseRelease(vp, Qt.LeftButton, pos=row_points(2)[0])
    app.processEvents()
    ok &= check("Ziehen von Kaestchen zu Kaestchen schaltet nichts", len(changes) == n)

    # 5) "Alle" ankreuzen
    QTest.mouseClick(vp, Qt.LeftButton, pos=row_points(0)[0])
    app.processEvents()
    ok &= check("Alle-Kaestchen setzt alle Haekchen", sorted(cb.checked_values()) == ["click", "jump", "noise"])
    print("Zusammenfassung:", cb._summary)
    cb.hidePopup()
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")
