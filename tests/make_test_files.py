"""Erzeugt synthetische Testtracks mit bekannten Fehlern.

Aufruf: python tests/make_test_files.py <Zielordner>
"""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
BPM = 128.0
DUR = 90.0


def synth(seed=1):
    rng = np.random.default_rng(seed)
    n = int(SR * DUR)
    t = np.arange(n) / SR
    beat = 60.0 / BPM
    out = np.zeros(n)
    # Kick auf jeder Zaehlzeit
    kl = int(0.25 * SR)
    kt = np.arange(kl) / SR
    kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 12)
    hl = int(0.05 * SR)
    for b in np.arange(0, DUR, beat):
        i = int(b * SR)
        out[i:i + kl] += 0.8 * kick[: n - i]
        j = int((b + beat / 2) * SR)  # Offbeat-Hihat
        if j < n:
            hat = rng.standard_normal(hl) * np.exp(-np.arange(hl) / SR * 80)
            hat = np.diff(hat, prepend=0)  # hochpassig
            out[j:j + hl] += 0.15 * hat[: n - j]
    # Bass (Achtel) und Pad
    notes = [55.0, 55.0, 65.4, 49.0]
    bar = 4 * beat
    f = np.array([notes[int(x // bar) % 4] for x in t])
    gate = ((t / (beat / 2)) % 1.0) < 0.6
    out += 0.25 * np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * gate * 0.5
    pad = sum(np.sin(2 * np.pi * fr * t) for fr in (220, 277.2, 329.6)) * 0.05
    out += pad
    stereo = np.stack([out, np.roll(out, 7)], axis=1)
    return (stereo / np.max(np.abs(stereo)) * 0.8).astype(np.float32)


def main(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    base = synth()
    sf.write(target / "01_sauber.flac", base, SR)

    # Sprung: bei 40 s fehlen 0,37 s, bei 60 s wiederholt sich ein Stueck -> Laenge bleibt
    a = base.copy()
    i0, cut = int(40 * SR), int(0.37 * SR)
    j0 = int(60 * SR)
    a = np.concatenate([a[:i0], a[i0 + cut:j0], a[j0 - cut:]])
    assert a.shape == base.shape
    sf.write(target / "02_spruenge_40s_60s.flac", a, SR)

    b = base.copy()
    b[int(50 * SR):int(50.03 * SR)] = 0.0
    sf.write(target / "03_aussetzer_50s.flac", b, SR)

    c = base.copy()
    rng = np.random.default_rng(7)
    k = int(0.4 * SR)
    c[int(55 * SR):int(55 * SR) + k] += (rng.standard_normal((k, 2)) * 0.3).astype(np.float32)
    sf.write(target / "04_rauschen_55s.flac", np.clip(c, -1, 1), SR)

    d = base.copy()
    d[int(65 * SR):int(65.6 * SR)] *= 1e-4
    sf.write(target / "05_stille_65s.flac", d, SR)

    # Datei mit zerschossenen Bytes in der Mitte
    sf.write(target / "06_kaputte_bytes.flac", base, SR)
    raw = bytearray((target / "06_kaputte_bytes.flac").read_bytes())
    mid = len(raw) // 2
    for p in range(mid, mid + 4000, 3):
        raw[p] ^= 0xA5
    (target / "06_kaputte_bytes.flac").write_bytes(bytes(raw))

    # Abgeschnitten (Header behauptet volle Laenge)
    sf.write(target / "07_abgeschnitten.flac", base, SR)
    raw = (target / "07_abgeschnitten.flac").read_bytes()
    (target / "07_abgeschnitten.flac").write_bytes(raw[: int(len(raw) * 0.7)])

    # Lauter Knackser: kurze Spitze ueber den Maximalpegel des Tracks, neben dem Beat
    k = base.copy()
    i = int(100.37 * 60.0 / BPM * SR)  # 0,37 Schlag nach einem Beat
    k[i:i + 3] = [[0.98, 0.98], [-0.97, -0.97], [0.6, 0.6]]
    sf.write(target / "10_lauter_knackser_47s.flac", k, SR)

    # Eingefrorener Signalwert: Laufwerk haelt 3 s lang den letzten Wert (klingt wie Stille)
    z = base.copy()
    i = int(70 * SR)
    z[i:i + 3 * SR] = z[i - 1]
    sf.write(target / "11_eingefroren_70s.flac", z, SR)

    # MP3 mit Spruengen (via PyAV/LAME)
    sub = target / "unterordner"
    sub.mkdir(exist_ok=True)
    _write_mp3(sub / "08_spruenge.mp3", a)
    _write_mp3(sub / "09_sauber.mp3", base)
    print("Testdateien in", target)


def _write_mp3(path: Path, data: np.ndarray):
    import av
    with av.open(str(path), "w") as out:
        st = out.add_stream("libmp3lame", rate=SR)
        st.layout = "stereo"
        st.bit_rate = 192000
        pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
        for i in range(0, pcm.shape[0], 1152 * 10):
            chunk = np.ascontiguousarray(pcm[i:i + 1152 * 10].reshape(1, -1))
            fr = av.AudioFrame.from_ndarray(chunk, format="s16", layout="stereo")
            fr.sample_rate = SR
            for pkt in st.encode(fr):
                out.mux(pkt)
        for pkt in st.encode(None):
            out.mux(pkt)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
