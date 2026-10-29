"""Wiedergabe mit uebersprungenen Bereichen (Editor-Vorschau) ohne Soundkarte pruefen.

Aufruf: python tests/player_ranges.py <Audiodatei>
Die Ausgabe wird ueber einen Ersatz-Stream eingesammelt und mit den erwarteten Samples verglichen.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sounddevice as sd  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

import gui.player as pl  # noqa: E402
from engine.decode import read_segment  # noqa: E402


class FakeStream:
    """Ruft den Callback wie eine Soundkarte in 512er-Bloecken auf und sammelt alles ein."""
    def __init__(self, samplerate, channels, dtype, callback, **_kw):
        self.cb, self.ch, self.out, self.active = callback, channels, [], False

    def start(self):
        self.active = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.active:
            buf = np.zeros((512, self.ch), np.float32)
            try:
                self.cb(buf, 512, None, None)
            except sd.CallbackStop:
                self.out.append(buf)
                self.active = False
                break
            self.out.append(buf)
            time.sleep(0.0005)

    def abort(self):
        self.active = False

    def close(self):
        pass


def main():
    path = sys.argv[1]
    app = QCoreApplication([])
    pl.sd.OutputStream = FakeStream
    pl.CHUNK_S = 1.5          # kleine Bloecke, damit das Nachladen mitgetestet wird
    ok = True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("OK   " if cond else "FEHLER ") + name)

    ranges = [(10.0, 12.2), (20.0, 23.5), (40.0, 41.0)]
    p = pl.LoopPlayer()
    positions = []
    p.position_changed.connect(lambda _f, t: positions.append(t))
    p.error.connect(lambda m: print("Player-Fehler:", m))
    p.play(-1, path, 10.0, 999.0, loop=False, ranges=ranges)
    stream = p._stream
    t0 = time.time()
    while stream.active and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.01)
    p.stop()
    got = np.concatenate(stream.out)
    # Erwartung aus denselben Nachladestuecken wie der Player (MP3-Spruenge sind nur ~1 Sample genau)
    exp = np.concatenate([pl._fade(read_segment(path, a, b)[0][:, :2], 44100, fi, fo)
                          for a, b, fi, fo in pl._pieces(ranges)])
    nz = np.flatnonzero(np.abs(got).sum(axis=1) > 0)
    got = got[: nz[-1] + 1] if nz.size else got[:0]
    check(f"Länge {got.shape[0]} ≈ erwartet {exp.shape[0]} Frames", abs(got.shape[0] - exp.shape[0]) < 64)
    n = min(got.shape[0], exp.shape[0])
    fade = int(pl.SEAM_FADE_S * 44100) + 2
    mask = np.ones(n, bool)
    edge = 0
    for a, b in ranges[:-1]:
        edge += read_segment(path, a, b)[0].shape[0]
        mask[max(0, edge - fade): edge + fade] = False   # Blenden an den Spruengen ausnehmen
    diff = np.abs(got[:n] - exp[:n]).max() if n else 1.0
    check(f"Inhalt = behaltene Bereiche (max. Abweichung {diff:.2e})", diff < 1e-5)
    jumps = [t for t in positions if 12.3 < t < 19.9 or 23.6 < t < 39.9]
    check(f"Abspielposition springt über entfernte Bereiche ({len(jumps)} Werte dazwischen)", not jumps)
    seam = got[edge - fade: edge + fade]
    check(f"Kein Knack an der letzten Naht (max. Sprung {np.abs(np.diff(seam, axis=0)).max():.3f})",
          np.abs(np.diff(seam, axis=0)).max() < 0.5)
    print("ALLES OK" if ok else "FEHLER GEFUNDEN")


if __name__ == "__main__":
    main()
