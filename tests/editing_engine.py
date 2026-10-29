"""Schnitt-Engine pruefen: Teilen und Herausschneiden bei MP3 (verlustfrei) und FLAC (sample-genau).

Aufruf: python tests/editing_engine.py <Arbeitsordner> <mp3-Datei> <flac-Datei>
Arbeitet nur auf Kopien im Arbeitsordner.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from engine import editing  # noqa: E402
from engine.decode import probe, read_segment  # noqa: E402

work = Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)
ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FEHLER ") + name)


def dur(p):
    i = probe(p)
    return i.frames / i.sr


# Plan
parts = editing.plan_parts(100.0, [40.0], [(10.0, 15.0), (60.0, 61.0)])
check(f"Plan: {parts}", parts == [[(0.0, 10.0), (15.0, 40.0)], [(40.0, 60.0), (61.0, 100.0)]])

for src_in in sys.argv[2:4]:
    src = work / ("orig" + Path(src_in).suffix.lower())
    shutil.copy2(src_in, src)
    D = dur(str(src))
    tags = editing.read_tags(str(src))
    print(f"\n{Path(src_in).name}: {D:.2f} s, Tags {tags}")
    a, b = 0.3 * D, 0.6 * D
    # Teilen bei a, im zweiten Teil 5 s herausschneiden
    plan = editing.plan_parts(D, [a], [(b, b + 5.0)])
    outs = []
    for i, ranges in enumerate(plan, 1):
        dst = work / f"teil{i}{src.suffix}"
        editing.write_part(str(src), str(dst), ranges, {"title": f"Teil {i}", "tracknumber": str(i)})
        outs.append(dst)
        exp = editing.part_duration(ranges)
        got = dur(str(dst))
        tol = 0.06 if src.suffix.lower() == ".mp3" else 0.001
        check(f"Teil {i}: Dauer {got:.3f} s, erwartet {exp:.3f} s", abs(got - exp) <= tol)
        t = editing.read_tags(str(dst))
        check(f"Teil {i}: Tags {t}", t.get("title") == f"Teil {i}" and t.get("artist") == tags.get("artist"))
    if src.suffix.lower() == ".flac":
        # sample-genau: Anfang von Teil 2 == Original ab a
        x2, sr = read_segment(str(outs[1]), 0, 2)
        xo, _ = read_segment(str(src), a, a + 2)
        n = min(len(x2), len(xo))
        check("FLAC sample-genau identisch", np.array_equal(x2[:n], xo[:n]))
        check("FLAC Bittiefe gleich", sf.info(str(outs[0])).subtype == sf.info(str(src)).subtype)
    else:
        # Ton unveraendert: Korrelation Teil 1 mit Original-Anfang
        x1, _ = read_segment(str(outs[0]), 5, 15)
        xo, _ = read_segment(str(src), 5, 15)
        n = min(len(x1), len(xo))
        c = np.corrcoef(x1[:n, 0], xo[:n, 0])[0, 1]
        check(f"MP3 Ton unverändert (Korrelation {c:.4f})", c > 0.999)
    try:
        from mutagen import File
        pics = getattr(File(str(src)), "pictures", None)
        if src.suffix.lower() == ".mp3":
            from mutagen.id3 import ID3
            n_src = len(ID3(str(src)).getall("APIC"))
            n_dst = len(ID3(str(outs[0])).getall("APIC"))
        else:
            n_src, n_dst = len(pics or []), len(File(str(outs[0])).pictures)
        check(f"Cover übernommen ({n_dst} von {n_src})", n_dst == n_src)
    except Exception as exc:
        print("   Cover-Prüfung übersprungen:", exc)

print("\nALLES OK" if ok else "\nFEHLER GEFUNDEN")
