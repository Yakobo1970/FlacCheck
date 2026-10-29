"""Analysiert alle Audiodateien eines Ordners und druckt die Ereignisse.

Aufruf: python tests/run_engine.py <Ordner>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.analyzer import KIND_LABELS, SENSITIVITIES, analyze_file  # noqa: E402
from engine.decode import AUDIO_EXTENSIONS  # noqa: E402


def main():
    root = Path(sys.argv[1])
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS)
    for p in files:
        r = analyze_file(str(p))
        print(f"\n{p.relative_to(root)}  {r['duration']:.1f}s  {r['tempo']} BPM  "
              f"{r['seconds']:.2f}s Analyse  {'' if r['ok'] else 'FEHLER: ' + r['error']}")
        for e in r["events"]:
            print(f"   {e['time']:8.2f}s  [{SENSITIVITIES[e['level']]:7}] {KIND_LABELS[e['kind']]:<18} {e['score']:5.2f}  {e['detail']}")


if __name__ == "__main__":
    main()
