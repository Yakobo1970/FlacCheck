from PySide6.QtGui import QColor, QPalette

# Markerfarben je Fehlerart
KIND_COLORS = {
    "jump": QColor(255, 152, 0),
    "click": QColor(229, 57, 53),
    "spike": QColor(46, 125, 50),
    "dropout": QColor(41, 182, 246),
    "silence": QColor(92, 107, 192),
    "noise": QColor(171, 71, 188),
    "glitch": QColor(255, 235, 59),
    "decode": QColor(245, 245, 245),
}

WAVE_COLOR = QColor(110, 190, 160)
WAVE_COLOR_DEFECT = QColor(205, 70, 70)
# Wellenfarbe nach der strengsten sichtbaren Stufe eines Tracks:
# niedrig (wahrscheinlichster Fehler) blass rot, mittel gelblich, hoch hellgruen
LEVEL_WAVE_COLORS = (QColor(222, 150, 150), QColor(218, 200, 125), WAVE_COLOR)
# Sobald Tracks markiert sind, werden die uebrigen Wellen um diesen Faktor abgedunkelt (QColor.darker)
WAVE_DIM = 210


def apply_dark(app):
    app.setStyle("Fusion")
    p = QPalette()
    base = QColor(30, 32, 36)
    p.setColor(QPalette.Window, QColor(38, 40, 45))
    p.setColor(QPalette.WindowText, QColor(225, 225, 225))
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor(44, 46, 52))
    p.setColor(QPalette.ToolTipBase, QColor(50, 52, 58))
    p.setColor(QPalette.ToolTipText, QColor(235, 235, 235))
    p.setColor(QPalette.Text, QColor(225, 225, 225))
    p.setColor(QPalette.Button, QColor(52, 55, 61))
    p.setColor(QPalette.ButtonText, QColor(225, 225, 225))
    p.setColor(QPalette.Highlight, QColor(60, 110, 160))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.PlaceholderText, QColor(140, 140, 140))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
    app.setPalette(p)
    app.setStyleSheet("QToolTip { border: 1px solid #555; padding: 4px; }")
