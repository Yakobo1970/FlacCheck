"""Liste der auffaelligen Tracks: Wellenform je Zeile mit Markerbereichen.

Interaktion je Zeile:
  * Checkbox "Defekt"            -> Datei als wirklich fehlerhaft bestaetigen
  * ▶ am Marker                    -> Markerbereich in Schleife abspielen (nochmal = Stopp)
  * ✕ am Marker                   -> Marker als Fehlalarm ausgrauen, ↺ holt ihn zurueck
  * Klick in die Wellenform       -> ab dort bis zum Trackende abspielen
"""
from __future__ import annotations

import os
import re

import numpy as np
from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QToolTip

from engine.analyzer import KIND_LABELS, SENSITIVITIES
from engine.db import FileRow, MarkerRow

from .theme import KIND_COLORS, LEVEL_WAVE_COLORS, WAVE_COLOR, WAVE_COLOR_DEFECT, WAVE_DIM

ROW_H = 132
PAD = 8
HEAD_H = 24
BTN = 16

FileRole = Qt.UserRole + 1


def fmt_time(t: float) -> str:
    m, s = divmod(round(max(0.0, t), 1), 60.0)  # erst runden, sonst "1:60.0"
    return f"{int(m)}:{s:04.1f}"


class TrackModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[FileRow] = []
        self.play_file: int | None = None
        self.play_marker: int | None = None
        self.play_pos = -1.0
        self.level = 1              # Anzeige-Empfindlichkeit (0 niedrig, 1 mittel, 2 hoch)
        self.kinds: set | None = None  # ausgewaehlte Fehlerarten (None = alle)
        self.show_removed = True       # entfernte Marker grau gestrichelt zeigen (mit Wiederherstellen)

    def visible(self, row: FileRow) -> list[MarkerRow]:
        return row.visible_markers(self.level, self.kinds)

    def drawn(self, row: FileRow) -> list[MarkerRow]:
        """Gezeichnete Marker: sichtbare, in der Ansicht 'Fehlerfrei' auch die entfernten."""
        if not self.show_removed:
            return self.visible(row)
        removed = [m for m in row.removed_markers if m.visible_kinds(self.level, self.kinds)]
        return sorted(self.visible(row) + removed, key=lambda m: m.start)

    def kinds_of(self, m: MarkerRow) -> list[str]:
        return m.visible_kinds(self.level, self.kinds) or m.kinds

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == FileRole:
            return row
        if role == Qt.DisplayRole:
            return row.path
        return None

    def set_rows(self, rows: list[FileRow]):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def append_row(self, row: FileRow):
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append(row)
        self.endInsertRows()

    def row_of(self, file_id: int) -> int:
        for i, r in enumerate(self.rows):
            if r.id == file_id:
                return i
        return -1

    def replace(self, row: FileRow):
        i = self.row_of(row.id)
        if i >= 0:
            self.rows[i] = row
            idx = self.index(i)
            self.dataChanged.emit(idx, idx)

    def remove_id(self, file_id: int):
        i = self.row_of(file_id)
        if i >= 0:
            self.beginRemoveRows(QModelIndex(), i, i)
            self.rows.pop(i)
            self.endRemoveRows()

    def refresh_id(self, file_id: int | None):
        if file_id is None:
            return
        i = self.row_of(file_id)
        if i >= 0:
            idx = self.index(i)
            self.dataChanged.emit(idx, idx)


class _Geom:
    """Geometrie einer Zeile (fuer Zeichnen und Trefferpruefung identisch)."""

    def __init__(self, rect, row: FileRow, markers: list[MarkerRow]):
        self.rect = rect
        self.check = QRectF(rect.left() + PAD, rect.top() + 5, 14, 14)
        self.head = QRectF(rect.left() + PAD, rect.top(), rect.width() - 2 * PAD, HEAD_H)
        self.wave = QRectF(rect.left() + PAD, rect.top() + HEAD_H + 2,
                           rect.width() - 2 * PAD, rect.height() - HEAD_H - PAD - 2)
        self.duration = max(row.duration, 1e-6)
        self.markers = []
        for m in markers:
            x0 = self.x_of(m.start)
            x1 = max(self.x_of(m.end), x0 + 2 * BTN + 6)
            r = QRectF(x0, self.wave.top(), x1 - x0, self.wave.height())
            play = QRectF(x0 + 2, self.wave.top() + 2, BTN, BTN)
            close = QRectF(x1 - BTN - 2, self.wave.top() + 2, BTN, BTN)
            self.markers.append((m, r, play, close))

    def x_of(self, t: float) -> float:
        return self.wave.left() + self.wave.width() * min(max(t / self.duration, 0.0), 1.0)

    def t_of(self, x: float) -> float:
        return (x - self.wave.left()) / max(self.wave.width(), 1.0) * self.duration


class TrackDelegate(QStyledItemDelegate):
    play_marker = Signal(object, object)        # FileRow, MarkerRow
    play_at = Signal(object, float)             # FileRow, Sekunde
    remove_marker = Signal(object, object)      # FileRow, MarkerRow
    restore_marker = Signal(object, object)     # FileRow, MarkerRow
    toggle_defect = Signal(object)              # FileRow

    def __init__(self, model: TrackModel, parent=None):
        super().__init__(parent)
        self.model = model
        self._wave_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        self.peak_loader = lambda file_id: None  # wird vom Hauptfenster gesetzt (DB-Zugriff)

    def sizeHint(self, option, index):
        return QSize(400, ROW_H)

    def _has_selection(self) -> bool:
        view = self.parent()
        sm = view.selectionModel() if view is not None else None
        return bool(sm and sm.hasSelection())

    def clear_cache(self, file_id: int | None = None):
        if file_id is None:
            self._wave_cache.clear()
        else:
            for k in [k for k in self._wave_cache if k[0] == file_id]:
                del self._wave_cache[k]

    # ------------------------------------------------------------ Zeichnen
    def _envelope(self, row: FileRow, width: int):
        key = (row.id, width)
        if key in self._wave_cache:
            return self._wave_cache[key]
        peaks = self.peak_loader(row.id) if row.has_peaks else None  # erst beim Zeichnen laden
        if not peaks:
            env = (np.zeros(width), np.zeros(width))
        else:
            pk = np.frombuffer(peaks, dtype=np.float16).reshape(-1, 2).astype(np.float32)
            edges = np.linspace(0, pk.shape[0], width + 1).astype(int)
            edges[-1] = pk.shape[0]
            lo = np.minimum.reduceat(pk[:, 0], np.minimum(edges[:-1], pk.shape[0] - 1))
            hi = np.maximum.reduceat(pk[:, 1], np.minimum(edges[:-1], pk.shape[0] - 1))
            env = (lo, hi)
        if len(self._wave_cache) > 4000:
            self._wave_cache.clear()
        self._wave_cache[key] = env
        return env

    def paint(self, painter: QPainter, option, index):
        row: FileRow = index.data(FileRole)
        if row is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        pal = option.palette
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        bg = pal.alternateBase().color() if index.row() % 2 else pal.base().color()
        if selected:
            bg = bg.lighter(135)
        painter.fillRect(rect, bg)
        if row.defect:
            painter.fillRect(QRectF(rect.left(), rect.top(), 4, rect.height()), QColor(220, 70, 70))
        g = _Geom(rect, row, self.model.drawn(row))

        # Kopfzeile
        painter.setPen(QPen(pal.text().color(), 1.2))
        painter.setBrush(QColor(200, 60, 60) if row.defect else Qt.NoBrush)
        painter.drawRoundedRect(g.check, 3, 3)
        if row.defect:
            painter.setPen(QPen(Qt.white, 2))
            c = g.check
            painter.drawPolyline(QPolygonF([QPointF(c.left() + 3, c.center().y()),
                                            QPointF(c.left() + 6, c.bottom() - 3),
                                            QPointF(c.right() - 3, c.top() + 3)]))
        x = g.check.right() + 6
        f = QFont(option.font)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(pal.text().color())
        name = os.path.basename(row.path)
        info = self._info(row, len(self.model.visible(row)), len(row.removed_markers))
        fm_info = painter.fontMetrics()
        painter.setFont(option.font)
        info_w = painter.fontMetrics().horizontalAdvance(info) + 8
        painter.setFont(f)
        avail = g.head.right() - x - info_w
        name_el = painter.fontMetrics().elidedText(name, Qt.ElideMiddle, int(avail * 0.55))
        painter.drawText(QRectF(x, g.head.top(), avail, HEAD_H), Qt.AlignVCenter | Qt.AlignLeft, name_el)
        nx = x + painter.fontMetrics().horizontalAdvance(name_el) + 10
        painter.setFont(option.font)
        painter.setPen(QColor(150, 150, 150))
        folder = painter.fontMetrics().elidedText(os.path.dirname(row.path), Qt.ElideMiddle,
                                                  int(max(0, g.head.right() - info_w - nx)))
        painter.drawText(QRectF(nx, g.head.top(), g.head.right() - info_w - nx, HEAD_H),
                         Qt.AlignVCenter | Qt.AlignLeft, folder)
        painter.setPen(QColor(190, 190, 190))
        painter.drawText(QRectF(g.head.right() - info_w, g.head.top(), info_w, HEAD_H),
                         Qt.AlignVCenter | Qt.AlignRight, info)
        del fm_info

        # Wellenform
        w = g.wave
        painter.fillRect(w, QColor(20, 22, 25))
        lo, hi = self._envelope(row, max(2, int(w.width())))
        mid = w.center().y()
        amp = w.height() / 2 - 1
        xs = w.left() + np.arange(lo.size) + 0.5
        top = [QPointF(float(xx), float(mid - h * amp)) for xx, h in zip(xs, np.clip(hi, -1, 1))]
        bot = [QPointF(float(xx), float(mid - l_ * amp)) for xx, l_ in zip(xs[::-1], np.clip(lo[::-1], -1, 1))]
        path = QPainterPath()
        path.addPolygon(QPolygonF(top + bot))
        painter.setPen(Qt.NoPen)
        if row.defect:
            wave_col = WAVE_COLOR_DEFECT
        else:
            vis = self.model.visible(row)
            wave_col = LEVEL_WAVE_COLORS[min(m.level for m in vis)] if vis else WAVE_COLOR
        if not selected and self._has_selection():
            wave_col = wave_col.darker(WAVE_DIM)  # nur markierte Tracks leuchten hell
        painter.setBrush(wave_col)
        painter.drawPath(path)
        if row.error and not row.has_peaks:
            painter.setPen(QColor(240, 120, 120))
            painter.drawText(w.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, row.error)
        elif row.status == "pending":
            painter.setPen(QColor(150, 190, 240))
            painter.drawText(w.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                             "Noch nicht analysiert – Rechtsklick → »Zur Neubewertung zurückschicken«. "
                             "Klick: ab hier abspielen")
        elif row.defect and not g.markers:
            hint = ("Als defekt bestätigt – die aktuelle Analyse findet hier keine Stelle mehr. "
                    "Klick: ab hier abspielen · Häkchen entfernen, falls doch in Ordnung")
            tw = painter.fontMetrics().horizontalAdvance(hint) + 16
            box = QRectF(w.left() + 4, w.center().y() - 12, min(tw, w.width() - 8), 24)
            painter.fillRect(box, QColor(20, 22, 25, 225))  # lesbar auch ueber der Wellenform
            painter.setPen(QColor(240, 170, 120))
            painter.drawText(box.adjusted(8, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, hint)
        elif row.status == "clean" and row.has_peaks:
            hint = "Unauffällig – bei der Analyse nichts gefunden. Klick: ab hier abspielen"
            tw = painter.fontMetrics().horizontalAdvance(hint) + 16
            box = QRectF(w.left() + 4, w.top() + 4, min(tw, w.width() - 8), 22)
            painter.fillRect(box, QColor(20, 22, 25, 225))
            painter.setPen(QColor(170, 170, 170))
            painter.drawText(box.adjusted(8, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, hint)
        elif row.status == "clean":
            painter.setPen(QColor(130, 130, 130))
            painter.drawText(w.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                             "Unauffällig – bei der Analyse nichts gefunden (keine Vorschau gespeichert). "
                             "Klick: ab hier abspielen")

        # Marker
        model = self.model
        for i, (m, r, play, close) in enumerate(g.markers):
            kinds = model.kinds_of(m) or m.kinds
            col = QColor(KIND_COLORS.get(kinds[0], QColor(255, 200, 0)))
            playing = model.play_marker == m.id
            if m.removed:  # entfernt: grau gestrichelt, mit Wiederherstellen-Knopf
                col = QColor(150, 150, 150)
                fill = QColor(150, 150, 150, 45 if playing else 18)
                painter.setPen(QPen(col, 1, Qt.DashLine))
            else:
                fill = QColor(col)
                fill.setAlpha(95 if playing else 55)
                painter.setPen(QPen(col, 1))
            painter.setBrush(fill)
            painter.drawRect(r)
            cx = g.x_of(m.center)
            painter.setPen(QPen(col.lighter(130), 1.5, Qt.DashLine))
            painter.drawLine(QPointF(cx, r.top()), QPointF(cx, r.bottom()))
            # Buttons
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(play, 3, 3)
            painter.drawRoundedRect(close, 3, 3)
            painter.setBrush(QColor(255, 255, 255))
            if playing:
                painter.drawRect(play.adjusted(4, 4, -4, -4))
            else:
                painter.drawPolygon(QPolygonF([QPointF(play.left() + 5, play.top() + 3),
                                               QPointF(play.right() - 3, play.center().y()),
                                               QPointF(play.left() + 5, play.bottom() - 3)]))
            painter.setPen(QPen(Qt.white, 1.6))
            if m.removed:  # Pfeil im Kreis: wiederherstellen
                painter.setBrush(Qt.NoBrush)
                arc = close.adjusted(3.5, 3.5, -3.5, -3.5)
                painter.drawArc(arc, 60 * 16, 270 * 16)
                tip = QPointF(arc.center().x() + arc.width() / 2 * 0.5, arc.top() + 0.6)
                painter.drawLine(tip, tip + QPointF(-3.5, -1.5))
                painter.drawLine(tip, tip + QPointF(-1.0, 3.5))
            else:
                painter.drawLine(close.topLeft() + QPointF(4, 4), close.bottomRight() - QPointF(4, 4))
                painter.drawLine(QPointF(close.left() + 4, close.bottom() - 4),
                                 QPointF(close.right() - 4, close.top() + 4))
            if r.width() > 2 * BTN + 30:
                painter.setPen(QColor(255, 255, 255, 220))
                lab = f"{i + 1}  " + ("entfernt: " if m.removed else "") + " + ".join(KIND_LABELS[k] for k in kinds)
                painter.drawText(QRectF(r.left() + 3, r.bottom() - 16, r.width() - 6, 14),
                                 Qt.AlignLeft | Qt.AlignVCenter,
                                 painter.fontMetrics().elidedText(lab, Qt.ElideRight, int(r.width() - 6)))

        # Abspielposition
        if model.play_file == row.id and model.play_pos >= 0:
            px = g.x_of(model.play_pos)
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.drawLine(QPointF(px, w.top()), QPointF(px, w.bottom()))
        painter.restore()

    @staticmethod
    def _info(row: FileRow, n: int, removed: int = 0) -> str:
        parts = [fmt_time(row.duration).split(".")[0]]
        if row.codec:
            parts.append(row.codec.split()[0].upper())
        if row.sr:
            parts.append(f"{row.sr / 1000:.1f} kHz".replace(".", ","))
        if row.tempo:
            parts.append(f"{row.tempo:.0f} BPM")
        parts.append(f"{n} Marker" if n != 1 else "1 Marker")
        if removed:
            parts.append(f"{removed} entfernt")
        if row.parked:
            parts.append("Sonderliste")
        if row.reviewed_at:
            parts.append("bearbeitet " + row.reviewed_at.replace("T", " ")[:16])
        return "  ·  ".join(parts)

    # ------------------------------------------------------------ Interaktion
    def editorEvent(self, event, model, option, index):
        row: FileRow = index.data(FileRole)
        if row is None or event.type() != QEvent.MouseButtonRelease or event.button() != Qt.LeftButton:
            return False
        pos = event.position()
        g = _Geom(QRectF(option.rect), row, self.model.drawn(row))
        if g.check.adjusted(-4, -4, 40, 4).contains(pos) and pos.x() < g.check.right() + 4:
            self.toggle_defect.emit(row)
            return True
        # rueckwaerts, damit oben liegende (spaetere) Marker Vorrang haben
        for m, r, play, close in reversed(g.markers):
            if close.adjusted(-2, -2, 2, 2).contains(pos):
                (self.restore_marker if m.removed else self.remove_marker).emit(row, m)
                return True
            if play.adjusted(-2, -2, 2, 2).contains(pos):
                self.play_marker.emit(row, m)   # nur der Play-Knopf spielt in Schleife
                return True
        if g.wave.contains(pos):
            self.play_at.emit(row, g.t_of(pos.x()))
            return True
        return False

    def helpEvent(self, event, view, option, index):
        row: FileRow = index.data(FileRole)
        if row is None:
            return False
        g = _Geom(QRectF(option.rect), row, self.model.drawn(row))
        pos = QPointF(event.pos())
        for i, (m, r, play, close) in enumerate(reversed(g.markers)):
            if close.contains(pos):
                QToolTip.showText(event.globalPos(),
                                  "Marker wiederherstellen (zurück in die Prüfliste)" if m.removed
                                  else "Als Fehlalarm ausgrauen (Fehler nicht hörbar) – ↺ holt ihn zurück", view)
                return True
            if r.contains(pos):
                n = len(g.markers) - i
                text = (f"<b>Marker {n}</b>: {fmt_time(m.start)} – {fmt_time(m.end)}<br>"
                        + "<br>".join(_html(line) for line in _visible_lines(m.detail, self.model))
                        + "<br><i>▶ = in Schleife · Klick = ab hier bis zum Ende</i>")
                QToolTip.showText(event.globalPos(), text, view)
                return True
        if g.check.adjusted(-4, -4, 4, 4).contains(pos):
            QToolTip.showText(event.globalPos(), "Als wirklich defekt bestätigen", view)
            return True
        if g.wave.contains(pos):
            QToolTip.showText(event.globalPos(),
                              f"{fmt_time(g.t_of(pos.x()))} – Klick: ab hier bis zum Ende abspielen", view)
            return True
        QToolTip.showText(event.globalPos(), row.path + (f"\n{row.error}" if row.error else ""), view)
        return True


# Fruehere Bezeichnungen in bereits gespeicherten Detailtexten
OLD_LABELS = {"Knackser/Schnitt": "Schnitt"}


def _visible_lines(detail: str, model: TrackModel) -> list[str]:
    """Detailzeilen ohne Fundstellen, die erst bei hoeherer Empfindlichkeit erscheinen."""
    out = []
    for line in detail.splitlines():
        m = re.search(r"\[(niedrig|mittel|hoch)\] (.+?):", line)
        if m:
            if SENSITIVITIES.index(m.group(1)) > model.level:
                continue
            label = OLD_LABELS.get(m.group(2), m.group(2))
            if model.kinds is not None and label not in {KIND_LABELS[k] for k in model.kinds}:
                continue
        out.append(line)
    return out


def _html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TrackView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = TrackModel(self)
        self.setModel(self.model_)
        self.delegate = TrackDelegate(self.model_, self)
        self.setItemDelegate(self.delegate)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setMouseTracking(True)
        self.setSelectionMode(QListView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        # Abdunkeln haengt an der ganzen Auswahl: bei jeder Aenderung alle Zeilen neu zeichnen
        self.selectionModel().selectionChanged.connect(lambda *_: self.viewport().update())

    def resizeEvent(self, e):
        self.delegate.clear_cache()
        super().resizeEvent(e)
