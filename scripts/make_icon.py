"""Erzeugt assets/icon.ico und assets/logo.png (Motiv: gepruefte CD mit Wellenform,
rot markierter Fehlerstelle und Haekchen). Jede Groesse wird einzeln gezeichnet; unter
48 px entfallen Rillen und Details, damit das Icon in der Taskleiste klar bleibt.

Aufruf: python scripts/make_icon.py
"""
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
BG = QColor("#1e2024")
DISC = QColor("#3a3f47")
RING = QColor("#4a505a")
WAVE = QColor("#6ebea0")
ERR = QColor(229, 57, 53, 110)
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


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

    # Fehlerbereich (wie die Marker in der App)
    p.setBrush(ERR)
    p.drawRoundedRect(QRectF(78 * s, 52 * s, 36 * s, 116 * s), 4 * s, 4 * s)

    # Wellenform mit Ausreisser im Fehlerbereich
    pts = [(20, 110), (34, 110), (42, 86), (50, 134), (58, 94), (66, 126), (74, 110), (84, 110),
           (92, 58), (100, 162), (108, 110), (126, 110), (134, 90), (142, 130), (150, 98),
           (158, 122), (166, 110), (200, 110)]
    if small:  # weniger Zacken, damit es nicht verschwimmt
        pts = [(20, 110), (44, 110), (56, 88), (70, 132), (84, 110), (92, 56), (102, 164),
               (112, 110), (136, 110), (148, 92), (160, 128), (172, 110), (200, 110)]
    path = QPainterPath(QPointF(pts[0][0] * s, pts[0][1] * s))
    for x, y in pts[1:]:
        path.lineTo(x * s, y * s)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(WAVE, (11 if small else 8) * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)

    # Haekchen-Abzeichen
    r = (50 if size <= 24 else 38 if small else 30) * s
    bc = QPointF(178 * s, 178 * s) if not small else QPointF(166 * s, 166 * s)
    p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawEllipse(bc, r + 5 * s, r + 5 * s)
    p.setBrush(WAVE)
    p.drawEllipse(bc, r, r)
    tick = QPainterPath(QPointF(bc.x() - 0.48 * r, bc.y()))
    tick.lineTo(bc.x() - 0.12 * r, bc.y() + 0.36 * r)
    tick.lineTo(bc.x() + 0.50 * r, bc.y() - 0.34 * r)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(BG, (0.30 if small else 0.27) * r, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(tick)
    p.end()
    return img


def to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 -- noetig fuer QPainter-Schriften/Rendering
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    images = [to_pil(render(sz)) for sz in SIZES]
    big = images[-1]
    big.save(assets / "icon.ico", format="ICO", sizes=[(sz, sz) for sz in SIZES],
             append_images=images[:-1])
    to_pil(render(512)).save(assets / "logo.png")
    # Kontrollbild: alle Groessen nebeneinander
    sheet = Image.new("RGBA", (sum(SIZES) + 10 * len(SIZES) + 10, 266), (240, 240, 240, 255))
    x = 10
    for sz, im in zip(SIZES, images):
        sheet.paste(im, (x, 256 - sz + 5), im)
        x += sz + 10
    sheet.save(assets / "icon_preview.png")
    print("geschrieben:", assets / "icon.ico", Image.open(assets / "icon.ico").info.get("sizes"))


if __name__ == "__main__":
    main()
