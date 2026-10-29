"""Erzeugt assets/editor_icon.ico (Bearbeitungsfenster) im Stil des App-Icons: dieselbe CD,
die Wellenform an zwei roten Schnittlinien getrennt (Mittelteil fehlt) und eine Schere im
Abzeichen statt des Haekchens.

Aufruf: python scripts/make_editor_icon.py
"""
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import BG, DISC, RING, SIZES, WAVE, to_pil  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CUT = QColor(255, 80, 80)          # wie die Schnittpunkte im Editor


def _wave(p: QPainter, pts, s: float, width: float):
    path = QPainterPath(QPointF(pts[0][0] * s, pts[0][1] * s))
    for x, y in pts[1:]:
        path.lineTo(x * s, y * s)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(WAVE, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 220.0          # Entwurf ist auf 220 x 220 angelegt
    small = size < 48

    p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawRoundedRect(QRectF(0, 0, size, size), 44 * s, 44 * s)
    c = QPointF(110 * s, 110 * s)
    p.setBrush(DISC)
    p.drawEllipse(c, 92 * s, 92 * s)
    if not small:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(RING, max(1.0, 2 * s)))
        p.drawEllipse(c, 78 * s, 78 * s)
        p.drawEllipse(c, 63 * s, 63 * s)
        p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawEllipse(c, 20 * s, 20 * s)
    p.setBrush(DISC)
    p.drawEllipse(c, 9 * s, 9 * s)

    # herausgeschnittener Mittelteil: dunkle Luecke zwischen zwei roten Schnittlinien
    x0, x1 = (80, 140) if small else (84, 136)
    p.setBrush(QColor(BG.red(), BG.green(), BG.blue(), 150))
    p.drawRect(QRectF(x0 * s, 50 * s, (x1 - x0) * s, 120 * s))
    pen = QPen(CUT, max(1.0, (7 if small else 4.5) * s))
    if not small:
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([2.2, 1.6])
    p.setPen(pen)
    for x in (x0, x1):
        p.drawLine(QPointF(x * s, 46 * s), QPointF(x * s, 174 * s))

    # Wellenform links und rechts der Luecke
    w = (11 if small else 8) * s
    if small:
        _wave(p, [(20, 110), (36, 110), (48, 86), (62, 134), (74, 110), (80, 110)], s, w)
        _wave(p, [(140, 110), (150, 92), (162, 128), (172, 110), (200, 110)], s, w)
    else:
        _wave(p, [(20, 110), (30, 110), (38, 86), (46, 134), (54, 94), (62, 126), (70, 104),
                  (78, 116), (84, 110)], s, w)
        _wave(p, [(136, 110), (142, 92), (150, 130), (158, 98), (166, 122), (174, 110), (200, 110)], s, w)

    # Abzeichen mit Schere
    r = (50 if size <= 24 else 38 if small else 30) * s
    bc = QPointF(178 * s, 178 * s) if not small else QPointF(166 * s, 166 * s)
    p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawEllipse(bc, r + 5 * s, r + 5 * s)
    p.setBrush(WAVE)
    p.drawEllipse(bc, r, r)

    def at(dx, dy):
        return QPointF(bc.x() + dx * r, bc.y() + dy * r)

    blade = QPen(BG, (0.20 if small else 0.15) * r, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(blade)
    p.drawLine(at(-0.30, -0.16), at(0.62, 0.30))   # Klinge oben-links nach unten-rechts
    p.drawLine(at(-0.30, 0.16), at(0.62, -0.30))   # Klinge unten-links nach oben-rechts
    ring = QPen(BG, (0.17 if small else 0.13) * r)
    p.setPen(ring)
    p.setBrush(Qt.NoBrush)
    hr = (0.20 if small else 0.18) * r
    p.drawEllipse(at(-0.50, -0.28), hr, hr)
    p.drawEllipse(at(-0.50, 0.28), hr, hr)
    p.setPen(Qt.NoPen)
    p.setBrush(WAVE)
    p.drawEllipse(at(0.10, 0.0), 0.05 * r, 0.05 * r)  # Drehpunkt
    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 -- noetig fuer QPainter
    assets = ROOT / "assets"
    images = [to_pil(render(sz)) for sz in SIZES]
    images[-1].save(assets / "editor_icon.ico", format="ICO", sizes=[(sz, sz) for sz in SIZES],
                    append_images=images[:-1])
    to_pil(render(512)).save(assets / "editor_logo.png")
    sheet = Image.new("RGBA", (sum(SIZES) + 10 * len(SIZES) + 10, 266), (240, 240, 240, 255))
    x = 10
    for sz, im in zip(SIZES, images):
        sheet.paste(im, (x, 256 - sz + 5), im)
        x += sz + 10
    sheet.save(assets / "editor_icon_preview.png")
    print("geschrieben:", assets / "editor_icon.ico")


if __name__ == "__main__":
    main()
