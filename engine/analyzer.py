"""Fehlersuche in Audiodateien.

Die Datei wird blockweise gestreamt; dabei entstehen nur kompakte Merkmalsreihen
im 512-Sample-Raster (auch stundenlange Mixe brauchen so wenig Speicher). Auf
diesen Reihen laufen anschliessend die Detektoren:

* jump     -- bleibende Stufe in der Lage des Rhythmusrasters (typisch fuer Fehler,
              bei denen der Track springt, die Gesamtlaenge aber stimmt und die
              Pruefsumme nichts merkt).
* click    -- Diskontinuitaet in der Wellenform (Schnittkante/Knackser): einzelner
              Ausreisser im LPC-Vorhersagefehler, weit ueber dem lokalen Grundpegel.
* spike    -- lauter Knackser: kurze Spitze deutlich ueber dem Pegel der Umgebung.
* dropout  -- Aussetzer: Lauf exakter digitaler Nullen mitten im lauten Material.
* silence  -- ploetzliche Stille (Pegelabsturz um > 40 dB mit abruptem Wiedereinsatz).
* noise    -- Rauschstoss: kurzer breitbandiger, "flacher" Spektralbereich, deutlich
              rauschiger und lauter als die Umgebung.
* decode   -- Dekodier-/Containerfehler oder Datei kuerzer als im Header angegeben.

Ergebnis: Ereignisse (Zeitpunkt, Art, Staerke) und daraus Markerbereiche
(Zeitpunkt -5 s ... +5 s, ueberlappende zusammengefasst).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

import numpy as np
from scipy.linalg import solve_toeplitz
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import butter, lfilter, sosfilt

from .decode import DecodeReport, iter_blocks, probe

ANALYZER_VERSION = 10

HOP = 512
NFFT = 2048
PEAK_BINS = 3000
MARKER_PRE = 5.0
MARKER_POST = 5.0
MARKER_PRE_JUMP = 8.0  # Raster-Detektor erkennt Spruenge eher etwas spaet
LPC_ORDER = 20
LPC_BLOCK = 4 * HOP
PHASE_BINS = 48
FLAT_RUN_STORE = 64          # kuerzere Laeufe identischer Werte gar nicht erst merken
FROZEN_MIN_S = 0.05          # eingefrorener Wert (nicht 0): ab 50 ms -- Rechteckwellen halten kuerzer

KIND_LABELS = {
    "jump": "Taktsprung",
    "click": "Schnitt",
    "spike": "Lauter Knackser",
    "dropout": "Aussetzer",
    "silence": "Plötzliche Stille",
    "noise": "Rauschstoß",
    "glitch": "Fehlrauschen/Stottern",
    "decode": "Dekodierfehler",
}

ALL_KINDS = tuple(KIND_LABELS)

# Stufen: Jede Datei wird immer in voller Tiefe geprueft; jede Fundstelle bekommt
# die niedrigste Stufe, ab der sie angezeigt wird (0 = niedrig, 1 = mittel, 2 = hoch).
# Die Empfindlichkeit ist damit ein reiner Anzeigefilter.
# Kalibriert an 68 Benchmark-Tracks und an ~260 vom Nutzer durchgehoerten Fundstellen
# (4 echte Defekte, Rest Fehlalarme) aus D:\AudioExpert\Alben (FLAC)\_A_.
SENSITIVITIES = ("niedrig", "mittel", "hoch")
DETECT_S = 0.75
# Mindest-Score je Stufe (niedrig, mittel, hoch)
LEVEL_SCORE = {
    "noise": (7.0, 5.0, 2.0),     # Fehlalarme max. 4,8, Defekt 5,2
}
# Lauter Knackser je Stufe: (Spitze gegenueber lautestem Rest in dB, max. Raster-Bezug).
# Definition des Nutzers: ragt ueber den ganzen restlichen Track hinaus und liegt neben dem
# Raster -- das gilt auf allen Stufen. Kalibriert an 537 entfernten lauten Knacksern (keiner
# lag ueber dem Rest des Tracks) und einem synthetischen Knackser (+2 dB, neben dem Beat).
SPIKE_LEVELS = ((0.5, 0.5), (0.0, 0.5), (0.0, 0.8))
# Schnitt (Definition des Nutzers): Diskontinuitaet, nach der das Audio mitten im Takt
# wegfaellt oder aus der Taktzaehlung huepft. Je Stufe: (max. Raster-Bezug der Stelle,
# Wegfall in dB, Takt-Ebene pruefen, Staerke fuer "extremer Knacks" ohne weitere Bedingung).
# Kalibriert an 437 entfernten Knackser-Stellen (niedrig 17, mittel 20, hoch 36 bleiben).
CUT_LEVELS = ((0.3, -10.0, False, 30.0), (0.3, -6.0, False, 15.0), (0.4, -6.0, True, 15.0))
CUT_MIN_SCORE = 2.0
GLITCH_Z = (20.0, 16.0, 12.0)     # Flacker-Wert: Fehlalarm 17, Defekte 18-46
# Rauschstoss nur, wenn links/rechts weitgehend unkorreliert (Stereo); kalibriert an ~360
# vom Nutzer entfernten Rauschstoessen (Median 0,78) vs. echten Lesefehlern (-0,94 ... 0,30)
NOISE_MAX_CORR = 0.30
# Taktsprung je Stufe: (Signifikanz 4-s-Fenster, Signifikanz 8-s-Fenster,
# Toleranz Bestaendigkeit, max. Sprungweite in Schlaegen). Spruenge um ~0,5 Schlag
# sind fast immer Betonungswechsel Kick/Offbeat (2/3 aller Fehlalarme) -> erst auf "hoch".
# Dazu Rasterregel: Musikalische "Spruenge" (Betonungswechsel, Sechzehntel/Snare-Rolls,
# Triolen) landen auf 1/2, 1/4 oder 1/3 Schlag; ein echter Lesefehler-Sprung irgendwo
# mitten im Takt. Gefordert ist ein Mindestabstand zum naechsten Raster, ausser bei sehr
# hoher Signifikanz. Kalibriert an 386 vom Nutzer entfernten Taktspruengen (Stand 0.7).
#   (k16, k32, Toleranz, max. Sprungweite, min. Rasterabstand, Signifikanz-Ausnahme)
JUMP_PARAMS = (
    (60.0, 30.0, 0.25, 0.38, 0.05, 100.0),
    (30.0, 14.0, 0.25, 0.38, 0.04, 100.0),
    (15.0, 8.0, 0.35, 0.51, 0.02, float("inf")),
)
BEAT_GRID = (0.0, 0.25, 1.0 / 3.0, 0.5)


def level_index(name: str) -> int:
    return SENSITIVITIES.index(name) if name in SENSITIVITIES else 1


@dataclass
class Event:
    time: float
    kind: str
    score: float
    detail: str
    level: int = 0  # ab welcher Empfindlichkeit angezeigt (0 niedrig, 1 mittel, 2 hoch)


@dataclass
class Marker:
    start: float
    end: float
    center: float
    kinds: list[str]
    score: float
    detail: str
    level: int = 0
    kind_levels: dict = field(default_factory=dict)  # Art -> niedrigste Stufe


@dataclass
class AnalysisResult:
    path: str
    ok: bool = True
    error: str = ""
    duration: float = 0.0
    sr: int = 0
    channels: int = 0
    codec: str = ""
    tempo: float = 0.0
    peaks: bytes = b""
    events: list[Event] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# =================================================================== Merkmale

class _Features:
    """Streamende Merkmalsextraktion im HOP-Raster."""

    def __init__(self, sr: int):
        self.sr = sr
        self.rem = np.zeros(0, np.float32)       # Rest < HOP fuer Zeitbereich
        self.lpc_tail = None                         # letzte LPC_ORDER Samples (LPC-Vorlauf)
        self.stft_buf = np.zeros(0, np.float32)
        self.window = np.hanning(NFFT).astype(np.float32)
        freqs = np.fft.rfftfreq(NFFT, 1.0 / sr)
        hi = min(12000.0, 0.45 * sr)
        self.band = (freqs >= 1000.0) & (freqs <= hi)
        self.flux_band = (freqs >= 30.0) & (freqs <= min(8000.0, 0.45 * sr))
        self.prev_logmag = None
        self.samples = 0
        # Zeitbereich je HOP-Frame
        self.rms: list[np.ndarray] = []
        self.pmin: list[np.ndarray] = []
        self.pmax: list[np.ndarray] = []
        self.resmax: list[np.ndarray] = []
        self.resrms: list[np.ndarray] = []
        # Spektral je STFT-Frame
        self.flux: list[np.ndarray] = []
        self.flat: list[np.ndarray] = []
        self.band_db: list[np.ndarray] = []
        # Nullen-Laeufe (Start-Sample, Laenge)
        # Laeufe identischer Abtastwerte (Start-Sample, Laenge, Wert): digitale Nullen oder ein
        # vom Laufwerk "eingefrorener" Wert -- beides kommt in Musik praktisch nicht vor
        self.zero_runs: list[tuple[int, int, float]] = []
        self._zrun_start = -1
        self._zrun_val = 0.0
        # Stereo (Hochton > 2 kHz) je HOP-Frame: Summen L*R, L*L, R*R
        self.sos = butter(4, min(2000.0, 0.4 * sr), "hp", fs=sr, output="sos")
        self.zi = None
        self.rem2 = np.zeros((0, 2), np.float32)
        self.lr: list[np.ndarray] = []
        self.ll: list[np.ndarray] = []
        self.rr: list[np.ndarray] = []
        self.stereo = False

    # ---------------------------------------------------------------
    def feed(self, block: np.ndarray):
        """block: (frames,) mono oder (frames, 2) stereo, float32."""
        if block.shape[0] == 0:
            return
        if block.ndim == 2 and block.shape[1] == 2:
            self._feed_stereo(block)
            x = block.mean(axis=1, dtype=np.float32)
        else:
            x = block if block.ndim == 1 else block[:, 0]
        self._feed_zero_runs(x)
        self.samples += x.shape[0]
        self._feed_stft(x)
        buf = np.concatenate((self.rem, x)) if self.rem.size else x
        n = (buf.shape[0] // HOP) * HOP
        self.rem = buf[n:].copy()
        if n == 0:
            return
        data = buf[:n]
        fr = data.reshape(-1, HOP)
        self.rms.append(np.sqrt(np.mean(fr * fr, axis=1, dtype=np.float64)).astype(np.float32))
        self.pmin.append(fr.min(axis=1))
        self.pmax.append(fr.max(axis=1))
        res = np.abs(self._lpc_residual(data)).reshape(-1, HOP)
        self.resmax.append(res.max(axis=1))
        self.resrms.append(np.sqrt(np.mean(res * res, axis=1, dtype=np.float64)).astype(np.float32))

    def _feed_stereo(self, block: np.ndarray):
        self.stereo = True
        if self.zi is None:
            self.zi = np.zeros((self.sos.shape[0], 2, 2))
        y, self.zi = sosfilt(self.sos, block, axis=0, zi=self.zi)
        buf = np.concatenate((self.rem2, y.astype(np.float32))) if self.rem2.size else y.astype(np.float32)
        n = (buf.shape[0] // HOP) * HOP
        self.rem2 = buf[n:].copy()
        if n == 0:
            return
        l_ = buf[:n, 0].reshape(-1, HOP)
        r_ = buf[:n, 1].reshape(-1, HOP)
        self.lr.append((l_ * r_).sum(axis=1, dtype=np.float64))
        self.ll.append((l_ * l_).sum(axis=1, dtype=np.float64))
        self.rr.append((r_ * r_).sum(axis=1, dtype=np.float64))

    def _lpc_residual(self, data: np.ndarray) -> np.ndarray:
        """Vorhersagefehler einer blockweise angepassten LPC (Ordnung LPC_ORDER).

        Musik ist ueber wenige Samples gut vorhersagbar; eine Schnittkante oder ein
        Knackser nicht -- dort schiesst der Restfehler hoch. data ist ein
        Vielfaches von HOP; Bloecke von LPC_BLOCK Samples (Rest am Ende kuerzer).
        """
        out = np.empty_like(data)
        if self.lpc_tail is None:
            self.lpc_tail = np.full(LPC_ORDER, data[0], np.float32)
        for s in range(0, data.shape[0], LPC_BLOCK):
            blk = data[s:s + LPC_BLOCK]
            seg = np.concatenate((self.lpc_tail, blk)).astype(np.float64)
            w = seg * np.hanning(seg.shape[0])
            ac = np.correlate(w, w, "full")[seg.shape[0] - 1: seg.shape[0] + LPC_ORDER]
            if ac[0] <= 1e-12:
                out[s:s + blk.shape[0]] = blk
            else:
                ac[0] *= 1.0001  # leichte Regularisierung
                try:
                    a = solve_toeplitz(ac[:LPC_ORDER], ac[1:LPC_ORDER + 1])
                except np.linalg.LinAlgError:
                    a = np.zeros(LPC_ORDER)
                e = lfilter(np.r_[1.0, -a], [1.0], seg)
                out[s:s + blk.shape[0]] = e[LPC_ORDER:]
            self.lpc_tail = seg[-LPC_ORDER:].astype(np.float32)
        return out

    def _feed_zero_runs(self, x: np.ndarray):
        """Sammelt Laeufe konstanter Abtastwerte ab FLAT_RUN_STORE Samples (blockuebergreifend)."""
        n = x.shape[0]
        if n == 0:
            return
        base = self.samples
        # Segmentgrenzen: Stellen, an denen sich der Wert aendert
        cuts = np.flatnonzero(x[1:] != x[:-1]) + 1
        starts = np.concatenate(([0], cuts))
        ends = np.concatenate((cuts, [n]))
        # erstes Segment ggf. mit dem offenen Lauf des Vorblocks verbinden
        first_start = base
        if self._zrun_start >= 0:
            if x[0] == self._zrun_val:
                first_start = self._zrun_start
            elif base - self._zrun_start >= FLAT_RUN_STORE:
                self.zero_runs.append((self._zrun_start, base - self._zrun_start, self._zrun_val))
        self._zrun_start = -1
        lengths = ends - starts
        # mittlere (vollstaendige) Segmente: nur die langen behalten
        for i in np.flatnonzero(lengths[:-1] >= FLAT_RUN_STORE) if starts.size > 1 else []:
            a = base + int(starts[i]) if i > 0 else first_start
            self.zero_runs.append((a, base + int(ends[i]) - a, float(x[starts[i]])))
        if starts.size > 1 and lengths[0] < FLAT_RUN_STORE and first_start < base:
            a = first_start  # kurzes erstes Segment, aber mit Vorblock zusammen lang genug?
            if base + int(ends[0]) - a >= FLAT_RUN_STORE:
                self.zero_runs.append((a, base + int(ends[0]) - a, float(x[0])))
        # letztes Segment bleibt offen
        self._zrun_start = (base + int(starts[-1])) if starts.size > 1 else first_start
        self._zrun_val = float(x[-1])

    def _feed_stft(self, x: np.ndarray):
        buf = np.concatenate((self.stft_buf, x)) if self.stft_buf.size else x
        if buf.shape[0] < NFFT:
            self.stft_buf = buf.copy()
            return
        nfr = (buf.shape[0] - NFFT) // HOP + 1
        frames = np.lib.stride_tricks.sliding_window_view(buf, NFFT)[::HOP][:nfr]
        self.stft_buf = buf[nfr * HOP:].copy()
        # in Paketen, damit der Speicher klein bleibt
        for i in range(0, nfr, 256):
            spec = np.fft.rfft(frames[i:i + 256] * self.window, axis=1)
            mag = np.abs(spec).astype(np.float32)
            p = mag[:, self.band] ** 2 + 1e-12
            geo = np.exp(np.mean(np.log(p), axis=1))
            ari = np.mean(p, axis=1)
            self.flat.append((geo / ari).astype(np.float32))
            self.band_db.append((10.0 * np.log10(ari)).astype(np.float32))
            lm = np.log1p(1000.0 * mag[:, self.flux_band])
            prev = self.prev_logmag if self.prev_logmag is not None else lm[:1]
            d = np.diff(np.concatenate((prev, lm)), axis=0)
            self.flux.append(np.maximum(d, 0.0).sum(axis=1).astype(np.float32))
            self.prev_logmag = lm[-1:]

    def finish(self):
        if self._zrun_start >= 0 and self.samples - self._zrun_start >= FLAT_RUN_STORE:
            self.zero_runs.append((self._zrun_start, self.samples - self._zrun_start, self._zrun_val))
        self._zrun_start = -1

        def cat(lst):
            return np.concatenate(lst) if lst else np.zeros(0, np.float32)

        return {
            "rms": cat(self.rms), "pmin": cat(self.pmin), "pmax": cat(self.pmax),
            "resmax": cat(self.resmax), "resrms": cat(self.resrms),
            "flux": cat(self.flux), "flat": cat(self.flat), "band_db": cat(self.band_db),
            "lr": cat(self.lr), "ll": cat(self.ll), "rr": cat(self.rr),
        }


# =================================================================== Detektoren

def _db(x):
    return 20.0 * np.log10(np.maximum(x, 1e-10))


def _detect_clicks(f, sr, lo, hi, s) -> list[Event]:
    fps = sr / HOP
    resmax, resrms, rms = f["resmax"], f["resrms"], f["rms"]
    if resmax.size < 10:
        return []
    bg = median_filter(resrms, size=2 * int(0.5 * fps) + 1, mode="nearest")
    ratio = resmax / (bg + 1e-6)
    crest = resmax / (resrms + 1e-9)
    tf = (np.arange(ratio.size) * HOP + HOP / 2) / sr
    inside = (tf > lo + 0.3) & (tf < hi - 0.3) & (_db(rms) > -50.0)
    if not inside.any():
        return []
    # Spurrelative Schwelle: Material mit vielen harten Flanken (Gates, Chiptune,
    # Rechteck-Baesse) hebt die Messlatte an -- gesucht ist der Ausreisser des Tracks.
    typical = float(np.percentile(ratio[inside], 99.5))
    thr = max(14.0 * s, 2.0 * typical * s)
    cand = inside & (ratio > thr) & (crest > 7.0) & (resmax > 0.01)
    idx = np.flatnonzero(cand)
    if idx.size == 0:
        return []
    minutes = max((hi - lo) / 60.0, 0.5)
    if idx.size / minutes > 20:
        idx = idx[ratio[idx] > 2.0 * thr]
    events, last_t = [], -10.0
    for i in idx:
        ti = float(tf[i])
        if ti - last_t < 0.15:
            if events and ratio[i] / thr > events[-1].score:
                events[-1].score = float(ratio[i] / thr)
            continue
        events.append(Event(ti, "click", float(ratio[i] / thr),
                            f"Diskontinuität ×{ratio[i]:.0f} über Grundpegel"))
        last_t = ti
    return events


def _tempo(env, fps):
    """Globales Tempo per Autokorrelation; liefert (Periode in Frames, Konfidenz)."""
    n = env.size
    spec = np.fft.rfft(env, 2 * n)
    ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    lo_lag = int(60.0 * fps / 190.0)
    hi_lag = min(int(60.0 * fps / 70.0) + 1, n - 2)
    if hi_lag <= lo_lag + 2:
        return 0.0, 0.0
    seg = ac[lo_lag:hi_lag]
    k = int(np.argmax(seg)) + lo_lag
    # Parabel-Interpolation fuer Sub-Frame-Genauigkeit
    a, b, c = ac[k - 1], ac[k], ac[k + 1]
    denom = a - 2 * b + c
    off = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
    return k + float(np.clip(off, -0.5, 0.5)), float(b)


def _phase_hist(env, period, fps, w):
    """Onset-Energie, gefaltet auf eine Schlaglaenge (PHASE_BINS Bins), je Fenster
    [T-w, T+w) -- normiert. Liefert (T, h)."""
    n = env.size
    nb = PHASE_BINS
    pos = (np.arange(n) / period % 1.0) * nb
    b0 = np.floor(pos).astype(np.int64) % nb
    frac = (pos - np.floor(pos)).astype(np.float32)
    H = np.zeros((n + 1, nb), np.float32)
    rows = np.arange(1, n + 1)
    H[rows, b0] += env * (1.0 - frac)
    H[rows, (b0 + 1) % nb] += env * frac
    np.cumsum(H, axis=0, out=H)
    step = max(1, int(fps / 4))
    T = np.arange(w, n - w, step)
    h = H[T + w] - H[T - w]
    h -= h.mean(1, keepdims=True)
    h /= np.linalg.norm(h, axis=1, keepdims=True) + 1e-9
    return T, h


def _align(h, g):
    """Verschiebung (in Schlaegen, -0.5 ... 0.5) und Aehnlichkeit jedes Fensters zur Referenz g."""
    nb = PHASE_BINS
    X = np.fft.irfft(np.fft.rfft(h, axis=1) * np.conj(np.fft.rfft(g)), n=nb, axis=1)
    rows = np.arange(X.shape[0])
    k = X.argmax(1)
    cm = X[rows, k]
    xm = X[rows, (k - 1) % nb]
    xp = X[rows, (k + 1) % nb]
    den = xm - 2 * cm + xp
    safe = np.where(np.abs(den) > 1e-9, den, 1.0)
    off = np.where(np.abs(den) > 1e-9, 0.5 * (xm - xp) / safe, 0.0).clip(-0.5, 0.5)
    return ((k + off + nb / 2) % nb - nb / 2) / nb, cm


def _phase_track(f, sr):
    """Lage des Rhythmusrasters ueber den Track: (t, Verschiebung in Schlaegen,
    Aehnlichkeit, Tempo) oder None, wenn kein tragfaehiges Tempo erkennbar ist.
    Langsame Tempodrift wird per Regression herausgerechnet."""
    fps = sr / HOP
    flux = f["flux"].astype(np.float64)
    if flux.size < fps * 30:
        return None
    env = np.maximum(flux - uniform_filter1d(flux, size=max(3, int(fps)), mode="nearest"), 0.0)
    period, conf = _tempo(env - env.mean(), fps)
    if period <= 0 or conf < 0.08:
        return None
    env32 = env.astype(np.float32)
    w = int(3.0 * fps)
    for _ in range(3):
        T, h = _phase_hist(env32, period, fps, w)
        if T.size < 50:
            return None
        g = h[len(h) // 2].copy()
        for _ in range(2):  # Referenzmuster: alle guten Fenster ausgerichtet gemittelt
            sh, cm = _align(h, g)
            acc = np.zeros(PHASE_BINS)
            for row, k, c in zip(h, np.round(sh * PHASE_BINS).astype(int), cm):
                if c > 0.4:
                    acc += np.roll(row, -k)
            g = acc - acc.mean()
            g /= np.linalg.norm(g) + 1e-9
        sh, cm = _align(h, g)
        t = T / fps
        wgt = np.maximum(cm - 0.3, 0.0)
        slopes = np.linspace(-0.006, 0.006, 241)
        z = np.exp(2j * np.pi * sh)
        r = np.abs(np.exp(-2j * np.pi * np.outer(slopes, t)) @ (wgt * z))
        slope = slopes[int(np.argmax(r))]
        if abs(slope) < 1e-5:
            break
        period = 1.0 / (1.0 / period - slope / fps)
    return T / fps, sh, cm, round(60.0 * fps / period, 2)


def _detect_jumps(f, sr, lo, hi, params, result: AnalysisResult, track=None) -> list[Event]:
    """Verfolgt die Lage des Rhythmusrasters ueber den ganzen Track gegen ein
    Referenzmuster. Ein echter Sprung erzeugt eine *bleibende Stufe*: vorher
    ruhige Lage, danach ruhige, aber verschobene Lage. Betonungswechsel und
    Live-Timing wackeln dagegen nur oder kehren zurueck."""
    if track is None:
        track = _phase_track(f, sr)
    if track is None:
        return []
    t, sh, cm, result.tempo = track

    k16, k32, tol, dmax, gmin, sig_over = params
    events = []
    for L, k in ((16, k16), (32, k32)):
        # auch schwache Kandidaten begrenzen den Pruefhorizont (z. B. Ruecksprung)
        weak = _phase_steps(t, sh, cm, L, 3.0)
        for tb, ta, d, sig in weak:
            if sig <= k or d >= dmax:
                continue
            grid_dist = min(abs(d - q) for q in BEAT_GRID)
            if grid_dist < gmin and sig < sig_over:
                continue  # landet auf musikalischem Raster: eher Rhythmus als Fehler
            # Bestaendigkeit bis zum naechsten Kandidaten pruefen (max. 40 s, min. 8 s)
            nxt = min([w[0] for w in weak if w[0] > ta + 1.0], default=ta + 40.0)
            horizon = float(np.clip(nxt - ta, 8.0, 40.0))
            if not (lo + 5 < ta < hi - 5) or not _persists(t, sh, cm, ta, d, tol, horizon):
                continue
            detail = (f"Rhythmusraster springt bleibend um {d:.2f} Schlag "
                      f"(Tempo {result.tempo:.1f} BPM, Signifikanz ×{sig:.0f})")
            events.append(Event(float(ta), "jump", float(sig / k), detail))
            if ta - tb > 3.0:  # Sprung liegt irgendwo in einer rasterlosen Luecke
                events.append(Event(float(tb), "jump", float(sig / k), detail + ", Lücke davor"))
    events.sort(key=lambda e: e.time)
    out: list[Event] = []
    for e in events:  # beide Skalen finden oft dieselbe Stelle
        if out and e.time - out[-1].time < 4.0:
            if e.score > out[-1].score:
                out[-1] = e
            continue
        out.append(e)
    return out


def _phase_steps(t, sh, cm, L, k, minc=0.55, dmin=0.04, maxgap=20.0):
    """Stufen in der Rasterlage: Mittel der L guten Fenster vor vs. nach jeder Stelle
    (rasterlose Abschnitte wie Breaks werden uebersprungen). Signifikanz = Stufe /
    (Streuung vorher + Streuung nachher) -- in sauber quantisierter Musik reichen so
    schon kleine Stufen, in wackligem Live-Timing braucht es grosse."""
    g = np.flatnonzero(cm > minc)
    if g.size < 2 * L + 2:
        return []
    tg = t[g]
    cs = np.concatenate(([0], np.cumsum(np.exp(2j * np.pi * sh[g]))))
    idx = np.arange(L, g.size - L)
    zb = (cs[idx] - cs[idx - L]) / L
    za = (cs[idx + L] - cs[idx]) / L
    sb = np.sqrt(-2 * np.log(np.clip(np.abs(zb), 1e-9, 1))) / (2 * np.pi)
    sa = np.sqrt(-2 * np.log(np.clip(np.abs(za), 1e-9, 1))) / (2 * np.pi)
    d = np.abs(np.angle(za * np.conj(zb))) / (2 * np.pi)
    sig = d / (sb + sa + 1e-3)
    cand = (d > dmin) & (sig > k) & (tg[idx] - tg[idx - 1] < maxgap)
    out = []
    ii = np.flatnonzero(cand)
    i = 0
    while i < ii.size:
        j = i
        while j + 1 < ii.size and ii[j + 1] - ii[j] <= 2:
            j += 1
        run = ii[i:j + 1]
        q = run[np.argmax(sig[run])]
        out.append((float(tg[idx[q] - 1]), float(tg[idx[q]]), float(d[q]), float(sig[q])))
        i = j + 1
    return out


def _persists(t, sh, cm, ta, d, tol, horizon=40.0, minc=0.55):
    """Echter Sprung: die neue Rasterlage haelt ueber die folgenden horizon Sekunden."""
    def mean(t0, t1):
        m = (t >= t0) & (t < t1) & (cm > minc)
        return np.exp(2j * np.pi * sh[m]).mean() if m.sum() >= 8 else None
    short, long_ = mean(ta, ta + 4), mean(ta, ta + horizon)
    if short is None or long_ is None:
        return False
    dev = abs(np.angle(long_ * np.conj(short))) / (2 * np.pi)
    return bool(dev < tol * d and abs(long_) > 0.8)


def _detect_spikes(f, sr, lo, hi, s, tempo: float = 0.0) -> list[Event]:
    """Laute Knackser (Definition des Nutzers): eine einmalige Spitze, die ueber den
    Pegel des *gesamten restlichen Tracks* hinausragt und ausserhalb des BPM-Rasters
    liegt. Vorfilter: kurz, impulsartig und deutlich ueber der Umgebung."""
    fps = sr / HOP
    peak = np.maximum(f["pmax"], -f["pmin"])
    n = peak.size
    sec = max(1, int(fps))
    nb = n // sec
    if nb < 4:
        return []
    p98 = np.percentile(peak[:nb * sec].reshape(nb, sec), 98, axis=1)
    ref = np.empty(nb)
    for i in range(nb):
        nbh = np.r_[max(0, i - 3):i, i + 1:min(nb, i + 4)]
        ref[i] = np.median(p98[nbh])
    r = ref[np.minimum(np.arange(n) // sec, nb - 1)]
    bg = median_filter(f["resrms"], size=2 * int(0.5 * fps) + 1, mode="nearest")
    impulsive = f["resmax"] / (bg + 1e-6) > 6.0
    # Vorfilter nur grob (+1,6 dB ueber der Umgebung) -- entscheidend ist der Vergleich mit
    # dem lautesten Rest des ganzen Tracks weiter unten
    factor = 1.2
    tf = (np.arange(n) * HOP + HOP / 2) / sr
    over = ((peak > factor * r) & (peak > 0.05) & impulsive
            & (tf > lo + 0.3) & (tf < hi - 0.3))
    events, last = [], -10.0
    for i in np.flatnonzero(over):
        # kurz: hoechstens ~30 ms erhoeht, sonst eher ein lauter Schlag/Einsatz
        if (peak[max(0, i - 3):i + 4] > 1.2 * r[i]).sum() > 3:
            continue
        if tf[i] - last < 0.2:
            continue
        score = float(peak[i] / (factor * r[i]))
        p = float(peak[max(0, i - 2):i + 3].max())
        # Spitze gegenueber der lautesten Stelle des restlichen Tracks (ohne +-0,5 s)
        ex = int(0.5 * fps)
        rest = max(float(peak[:max(0, i - ex)].max(initial=0.0)), float(peak[i + ex:].max(initial=0.0)))
        glob_db = 20 * np.log10(p / max(rest, 1e-9))
        grid = _grid_strength(f, sr, tf[i], tempo)
        lvl = None
        for k, (gmin, gridmax) in enumerate(SPIKE_LEVELS):
            if glob_db >= gmin and (grid is None or grid <= gridmax):
                lvl = k
                break
        if lvl is None:
            continue
        db = 20 * np.log10(peak[i] / max(r[i], 1e-6))
        ginfo = "kein Tempo erkennbar" if grid is None else f"Raster-Bezug {grid:.2f}"
        events.append(Event(float(tf[i]), "spike", score,
                            f"Spitze {glob_db:+.1f} dB gegenüber dem lautesten Rest des Tracks, "
                            f"{db:+.1f} dB über der Umgebung, {ginfo}", level=lvl))
        last = tf[i]
    return events


def _classify_cut(f, sr, t, score, track):
    """Ordnet eine Diskontinuitaet als Schnitt ein: (Stufe oder None, Begruendung).
    Schnitt = Audio faellt danach mitten im Takt weg, oder es huepft aus der Taktzaehlung
    (Beat-Phase verschoben, nicht auf ein musikalisches Raster; auf 'hoch' auch um ganze
    Schlaege innerhalb des Takts). Sehr starke Knackser zaehlen auch ohne diese Bedingungen."""
    fps = sr / HOP
    rms_db = _db(f["rms"])
    i = int(t * fps)
    q, g = max(2, int(0.25 * fps)), max(1, int(0.01 * fps))
    drop = float(np.mean(rms_db[i + g:i + g + q]) - np.mean(rms_db[max(0, i - q):i])) if i > q else 0.0
    tempo = track[3] if track is not None else 0.0
    ongrid = _grid_strength(f, sr, t, tempo)
    beat_hop = False
    d = 0.0
    if track is not None:
        tt, sh, cm, _ = track
        mb = (tt > t - 5) & (tt < t - 0.5) & (cm > 0.5)
        ma = (tt > t + 0.5) & (tt < t + 5) & (cm > 0.5)
        if mb.sum() > 4 and ma.sum() > 4:
            zb = np.exp(2j * np.pi * sh[mb]).mean()
            za = np.exp(2j * np.pi * sh[ma]).mean()
            d = abs(float(np.angle(za * np.conj(zb)))) / (2 * np.pi)
            beat_hop = d >= 0.08 and min(abs(d - q_) for q_ in BEAT_GRID) >= 0.03
    bar_lag, bar_gain = _bar_shift(f, sr, t, tempo)
    bar_hop = bar_lag is not None and bar_lag % 4 != 0 and bar_gain >= 0.6
    for lvl, (gridmax, dropmax, use_bar, extreme) in enumerate(CUT_LEVELS):
        if score >= extreme:
            return lvl, "extrem starker Knacks"
        if ongrid is not None and ongrid > gridmax:
            continue
        if drop <= dropmax:
            return lvl, f"Audio bricht danach um {-drop:.0f} dB weg (neben dem Raster)"
        if beat_hop:
            return lvl, f"danach um {d:.2f} Schlag aus der Taktzählung gehüpft"
        if use_bar and bar_hop:
            return lvl, f"danach um {bar_lag:g} Schlag im Takt versetzt"
    return None, ""


def _bar_shift(f, sr, t, tempo, span=8.0, sub=12):
    """Onset-Muster eines 4/4-Takts vor und nach t vergleichen: bester Versatz in Schlaegen
    und Korrelationsgewinn gegenueber 'kein Versatz'. (None, 0) ohne Tempo."""
    if not tempo:
        return None, 0.0
    fps = sr / HOP
    flux = f["flux"].astype(np.float64)
    env = np.maximum(flux - uniform_filter1d(flux, size=max(3, int(fps)), mode="nearest"), 0.0)
    per = 60.0 / tempo * fps
    k = int((t * sr - NFFT / 2) / HOP)
    nb = 4 * sub
    w = int(span * fps)

    def fold(a, b):
        idx = np.arange(max(0, a), min(env.size, b))
        if idx.size < fps * 2:
            return None
        h = np.bincount(((idx / (4 * per)) % 1 * nb).astype(int) % nb, env[idx], nb)
        h = h - h.mean()
        n = np.linalg.norm(h)
        return h / n if n > 0 else None
    hb, ha = fold(k - w, k - int(0.15 * fps)), fold(k + int(0.15 * fps), k + w)
    if hb is None or ha is None:
        return None, 0.0
    X = np.fft.irfft(np.fft.rfft(ha) * np.conj(np.fft.rfft(hb)), n=nb)
    lag = int(np.argmax(X))
    return lag / sub, float(X[lag] - X[0])


def _grid_strength(f, sr, t, tempo):
    """Wie stark liegt Zeitpunkt t auf dem Rhythmusraster? Onset-Energie der +-4 s um t
    (ohne t selbst), gefaltet auf eine Schlaglaenge, an der Phase von t -- relativ zum
    staerksten Rasterpunkt (1 = Schlagzeug-Treffer auf dem Raster, ~0 = daneben).
    None, wenn kein Tempo erkennbar ist."""
    if not tempo:
        return None
    fps = sr / HOP
    flux = f["flux"].astype(np.float64)
    env = np.maximum(flux - uniform_filter1d(flux, size=max(3, int(fps)), mode="nearest"), 0.0)
    per = 60.0 / tempo * fps
    k = int((t * sr - NFFT / 2) / HOP)
    a, b = max(0, k - int(4 * fps)), min(env.size, k + int(4 * fps))
    idx = np.arange(a, b)
    keep = np.abs(idx - k) > int(0.12 * fps)
    if keep.sum() < fps:
        return None
    nb = PHASE_BINS
    h = np.bincount(((idx[keep] / per) % 1 * nb).astype(int) % nb, env[a:b][keep], nb)
    h = np.convolve(np.r_[h[-1], h, h[0]], [0.25, 0.5, 0.25], "valid")
    if h.max() <= 0:
        return None
    sb = int((k / per) % 1 * nb) % nb
    return float(h[[(sb - 1) % nb, sb, (sb + 1) % nb]].max() / h.max())


def _robust_z(v, sel):
    med = np.median(v[sel])
    mad = np.median(np.abs(v[sel] - med)) + 1e-9
    return (v - med) / (1.4826 * mad)


def _detect_glitch(f, sr, lo, hi, thr) -> list[Event]:
    """Fehlrauschen/Stottern: Der Hochtonpegel springt von Frame zu Frame extrem
    (Rauschfetzen wechseln mit glatten, hoehenlosen Stuecken), gemessen relativ zum
    eigenen Track. Bei Stereo muss zugleich die L/R-Korrelation einbrechen --
    Lesefehler-Muell ist zwischen den Kanaelen unkorreliert, Musik nicht.
    Kalibriert an EMF "Ballad of the Bishop" (3:07): Wert 31, normale Musik <= 12."""
    fps = sr / HOP
    bdb = np.maximum(f["band_db"], -100.0).astype(np.float64)
    n = bdb.size
    if n < fps * 10:
        return []
    k = max(3, int(0.5 * fps))
    d = np.diff(bdb, prepend=bdb[:1])
    J = np.sqrt(np.maximum(uniform_filter1d(d * d, k, mode="nearest"), 0.0))
    t = (np.arange(n) * HOP + NFFT / 2) / sr
    rms_db = _db(f["rms"])
    e = np.interp(np.arange(n), np.arange(rms_db.size), rms_db) if rms_db.size else np.zeros(n)
    sel = (t > lo + 2) & (t < hi - 2) & (e > -45.0)
    if sel.sum() < fps * 5:
        return []
    zJ = _robust_z(J, sel)
    m = min(n, f["lr"].size)
    stereo = m > 0
    if stereo:
        c = (uniform_filter1d(f["lr"][:m], k) /
             np.sqrt(uniform_filter1d(f["ll"][:m], k) * uniform_filter1d(f["rr"][:m], k) + 1e-12))
        zS = np.full(n, 0.0)
        zS[:m] = -_robust_z(c, sel[:m])
        cand = sel & (zJ > thr) & (zS > 2.0)
    else:
        cand = sel & (zJ > thr * 1.25)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], cand.astype(np.int8), [0]))))
    events = []
    for a, b in zip(edges[::2], edges[1::2]):
        i = a + int(np.argmax(zJ[a:b]))
        dur = (b - a) / fps
        events.append(Event(float(t[a]), "glitch", float(zJ[i] / thr),
                            f"Fehlrauschen/Stottern über {max(dur, 0.1):.1f} s "
                            f"(Flackern ×{zJ[i]:.0f}" + (f", Stereo-Zerfall ×{zS[i]:.0f})" if stereo else ")")))
    return events


def _detect_dropouts(zero_runs, f, sr, lo, hi, s) -> list[Event]:
    rms_db = _db(f["rms"])
    nfr = rms_db.size
    events = []
    min_len = int(0.004 * sr / s)
    ctx = max(2, int(0.15 * sr / HOP))
    # Stuecke, die nur durch wenige abweichende Samples getrennt sind, zusammenfassen
    merged: list[list] = []
    for run in sorted(zero_runs):
        start, length = run[0], run[1]
        value = run[2] if len(run) > 2 else 0.0
        if merged and start - (merged[-1][0] + merged[-1][1]) <= int(0.005 * sr)                 and (value == 0.0) == (merged[-1][2] == 0.0):
            merged[-1][1] = start + length - merged[-1][0]
        else:
            merged.append([start, length, value])
    for start, length, value in merged:
        frozen = value != 0.0
        if length < (int(FROZEN_MIN_S * sr) if frozen else min_len):
            continue
        t0, t1 = start / sr, (start + length) / sr
        if t0 < lo + 0.2 or t1 > hi - 0.2:
            continue
        fa = start // HOP
        fb = (start + length) // HOP + 1
        before = rms_db[max(0, fa - ctx):fa]
        after = rms_db[fb:min(nfr, fb + ctx)]
        if before.size == 0 or after.size == 0:
            continue
        if np.median(before) > -40.0 and np.median(after) > -40.0:
            ms = length / sr * 1000
            kind = "dropout" if ms < 1000 else "silence"
            what = (f"eingefrorener Signalwert ({value:+.3f}, für das Ohr Stille)" if frozen
                    else "digitale Nullen")
            events.append(Event(t0, kind, float(min(10.0, 1.0 + ms / 50.0)),
                                f"{ms:.0f} ms {what} im laufenden Material"))
    return events


def _detect_silence(f, sr, lo, hi, s) -> list[Event]:
    fps = sr / HOP
    rms_db = _db(f["rms"])
    quiet = rms_db < -65.0
    edges = np.flatnonzero(np.diff(np.concatenate(([0], quiet.astype(np.int8), [0]))))
    events = []
    min_frames = int(0.15 * fps * s)
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a < min_frames or a < 2 or b > rms_db.size - 2:
            continue
        t0 = a * HOP / sr
        if t0 < lo + 0.5 or b * HOP / sr > hi - 0.5:
            continue
        pre = rms_db[max(0, a - 3):a].max()
        post = rms_db[b:b + 3].max()
        # abrupt: vorher/nachher laut, kein Ausblenden
        fade = rms_db[max(0, a - int(0.3 * fps)):a]
        if pre > -35.0 and post > -35.0 and fade.size and fade.min() > -45.0:
            ms = (b - a) / fps * 1000
            events.append(Event(t0, "silence", float(min(8.0, 1.0 + ms / 300.0)),
                                f"{ms:.0f} ms Stille mit hartem Ab- und Einsatz"))
    return events


def _detect_noise(f, sr, lo, hi, s) -> list[Event]:
    fps = sr / HOP
    flat = f["flat"]
    bdb = f["band_db"]
    n = flat.size
    if n < fps * 4:
        return []
    k = max(3, int(0.05 * fps))
    fs = uniform_filter1d(flat, size=k, mode="nearest")
    es = uniform_filter1d(bdb, size=k, mode="nearest")
    # Referenz: 90. Perzentil je Sekunde, dann Median der Nachbarsekunden
    sec = max(1, int(fps))
    nb = n // sec
    if nb < 3:
        return []
    fp90 = np.percentile(fs[:nb * sec].reshape(nb, sec), 90, axis=1)
    ep90 = np.percentile(es[:nb * sec].reshape(nb, sec), 90, axis=1)
    ref_f = np.empty(nb)
    ref_e = np.empty(nb)
    for i in range(nb):
        nbh = np.r_[max(0, i - 5):i, i + 1:min(nb, i + 6)]
        ref_f[i] = np.median(fp90[nbh]) if nbh.size else fp90[i]
        ref_e[i] = np.median(ep90[nbh]) if nbh.size else ep90[i]
    bi = np.minimum(np.arange(n) // sec, nb - 1)
    cand = (fs > 0.35 * s) & (fs - ref_f[bi] > 0.12 * s) & (es - ref_e[bi] > 6.0 * s)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], cand.astype(np.int8), [0]))))
    events = []
    for a, b in zip(edges[::2], edges[1::2]):
        dur = (b - a) / fps
        if dur < 0.06 or dur > 4.0:
            continue
        t0 = (a * HOP + NFFT / 2) / sr
        if t0 < lo + 0.3 or t0 > hi - 0.3:
            continue
        excess = float(np.max(fs[a:b] - ref_f[bi[a:b]]))
        # Lesefehler-Rauschen ist zwischen links und rechts unkorreliert; Riser, Phasing,
        # Becken usw. sind gemischter Studioklang und damit stark korreliert.
        corr = _lr_corr(f, a, b + NFFT // HOP)
        if corr is not None and corr > NOISE_MAX_CORR:
            continue
        cinfo = f", L/R-Korrelation {corr:.2f}" if corr is not None else ""
        events.append(Event(t0, "noise", excess / (0.12 * s),
                            f"{dur * 1000:.0f} ms breitbandiges Rauschen (+{excess:.2f} Flachheit{cinfo})"))
    return events


def _lr_corr(f, a: int, b: int) -> float | None:
    """Korrelation links/rechts (Hochton > 2 kHz) ueber die HOP-Frames a..b; None bei Mono."""
    lr = f.get("lr")
    if lr is None or lr.size == 0:
        return None
    a, b = max(0, a), min(lr.size, max(b, a + 1))
    ll, rr = float(f["ll"][a:b].sum()), float(f["rr"][a:b].sum())
    if ll <= 1e-12 or rr <= 1e-12:
        return None
    return float(lr[a:b].sum() / np.sqrt(ll * rr))


# =================================================================== Marker

def build_markers(events: list[Event], duration: float) -> list[Marker]:
    if not events:
        return []
    evs = sorted(events, key=lambda e: e.time)
    groups: list[list[Event]] = [[evs[0]]]
    for e in evs[1:]:
        last_end = min(duration, groups[-1][-1].time + MARKER_POST)
        pre = MARKER_PRE_JUMP if e.kind == "jump" else MARKER_PRE
        if e.time - pre <= last_end:
            groups[-1].append(e)
        else:
            groups.append([e])
    markers = []
    for g in groups:
        pre = max(MARKER_PRE_JUMP if e.kind == "jump" else MARKER_PRE for e in g[:1])
        start = max(0.0, g[0].time - pre)
        end = min(duration, g[-1].time + MARKER_POST) if duration > 0 else g[-1].time + MARKER_POST
        top = max(g, key=lambda e: e.score)
        kinds = []
        for e in sorted(g, key=lambda e: -e.score):
            if e.kind not in kinds:
                kinds.append(e.kind)
        kind_levels: dict[str, int] = {}
        for e in g:
            kind_levels[e.kind] = min(kind_levels.get(e.kind, 9), e.level)
        detail = "\n".join(f"{_fmt(e.time)}  [{SENSITIVITIES[e.level]}] {KIND_LABELS[e.kind]}: {e.detail}"
                           for e in g)
        markers.append(Marker(start, end, top.time, kinds, float(top.score), detail,
                              level=min(kind_levels.values()), kind_levels=kind_levels))
    return markers


def _fmt(t: float) -> str:
    m, sec = divmod(round(max(0.0, t), 2), 60.0)  # erst runden, sonst "1:60.00"
    return f"{int(m)}:{sec:05.2f}"


def _level_from(value: float, limits) -> int | None:
    """Stufe 0/1/2 aus aufsteigend strengen Grenzen (niedrig, mittel, hoch)."""
    for lvl, lim in enumerate(limits):
        if value >= lim:
            return lvl
    return None


def detect_events(f: dict, zero_runs, sr: int, enabled, res: AnalysisResult) -> list[Event]:
    """Alle signalbasierten Detektoren in voller Tiefe; jede Fundstelle erhaelt ihre Stufe."""
    events: list[Event] = []
    s = DETECT_S
    rms_db = _db(f["rms"])
    loud = np.flatnonzero(rms_db > -45.0)
    if not loud.size:
        return events
    lo = loud[0] * HOP / sr
    hi = (loud[-1] + 1) * HOP / sr
    track = _phase_track(f, sr) if {"jump", "click", "spike"} & set(enabled) else None
    if track is not None:
        res.tempo = track[3]
    if "jump" in enabled:
        found: list[Event] = []
        for lvl, params in enumerate(JUMP_PARAMS):
            for e in _detect_jumps(f, sr, lo, hi, params, res, track):
                if any(abs(e.time - o.time) < 4.0 for o in found):
                    continue  # schon auf strengerer Stufe gefunden
                e.level = lvl
                found.append(e)
        events += found
    scored = []
    if "click" in enabled:
        for e in _detect_clicks(f, sr, lo, hi, s):
            if e.score < CUT_MIN_SCORE:
                continue
            lvl, why = _classify_cut(f, sr, e.time, e.score, track)
            if lvl is not None:
                e.level = lvl
                e.detail += f", {why}"
                events.append(e)
    if "spike" in enabled:
        events += _detect_spikes(f, sr, lo, hi, s, res.tempo)
    if "noise" in enabled:
        scored += _detect_noise(f, sr, lo, hi, s)
    for e in scored:
        lvl = _level_from(e.score, LEVEL_SCORE[e.kind])
        if lvl is not None:
            e.level = lvl
            events.append(e)
    if "glitch" in enabled:
        for e in _detect_glitch(f, sr, lo, hi, GLITCH_Z[-1]):
            lvl = _level_from(e.score * GLITCH_Z[-1], GLITCH_Z)
            if lvl is not None:
                e.level = lvl
                events.append(e)
    if "dropout" in enabled:
        events += [e for e in _detect_dropouts(zero_runs, f, sr, lo, hi, s)
                   if e.kind == "dropout" or "silence" in enabled]
    if "silence" in enabled:
        events += _detect_silence(f, sr, lo, hi, s)
    return events


# =================================================================== Einstieg

def analyze_file(path: str,
                 enabled: tuple[str, ...] = ALL_KINDS
                 ) -> dict:
    """Analysiert eine Datei. Laeuft im Worker-Prozess, liefert ein picklebares dict."""
    t_start = time.perf_counter()
    res = AnalysisResult(path=path)
    try:
        info = probe(path)
        res.sr, res.channels, res.codec = info.sr, info.channels, info.codec
        report = DecodeReport()
        feats = None
        for block, sr in iter_blocks(path, report):
            if feats is None:
                feats = _Features(sr)
                res.sr = sr
            feats.feed(block)
        if feats is None:
            res.ok = False
            res.error = report.errors[0][1] if report.errors else "keine Audiodaten"
            res.events = [Event(0.0, "decode", 10.0, res.error)]
            res.markers = build_markers(res.events, 0.0)
            return res.to_dict()
        sr = feats.sr
        f = feats.finish()
        res.duration = feats.samples / sr
        events: list[Event] = []

        if "decode" in enabled:
            for t_err, msg in report.errors:
                events.append(Event(t_err, "decode", 10.0, msg))
            if info.frames and feats.samples < info.frames - max(sr // 10, 4096):
                missing = (info.frames - feats.samples) / sr
                events.append(Event(res.duration, "decode", 10.0,
                                    f"Datei endet {missing:.1f} s vor der im Header angegebenen Länge"))

        events += detect_events(f, feats.zero_runs, sr, enabled, res)

        # Wellenform-Vorschau (min/max je Bin, float16)
        pmin, pmax = f["pmin"], f["pmax"]
        if pmin.size:
            nb = min(PEAK_BINS, pmin.size)
            edges = np.linspace(0, pmin.size, nb + 1).astype(np.int64)[:-1]
            peaks = np.empty((nb, 2), np.float16)
            peaks[:, 0] = np.minimum.reduceat(pmin, edges)
            peaks[:, 1] = np.maximum.reduceat(pmax, edges)
            res.peaks = peaks.tobytes()

        res.events = sorted(events, key=lambda e: e.time)
        res.markers = build_markers(res.events, res.duration)
    except Exception as exc:  # unlesbar o.ae.
        res.ok = False
        res.error = f"{type(exc).__name__}: {exc}"
        res.events = [Event(0.0, "decode", 10.0, res.error)]
        res.markers = build_markers(res.events, res.duration)
    finally:
        res.seconds = time.perf_counter() - t_start
    return res.to_dict()
