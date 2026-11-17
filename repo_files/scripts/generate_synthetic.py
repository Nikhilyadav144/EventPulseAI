"""Generate the clearly-labelled SYNTHETIC supplementary dataset.

Usage:
    python scripts/generate_synthetic.py [--students 600] [--events 48] [--seed 2026]

End-term (CSL236) training dataset, 1,323 rows:
    python scripts/generate_synthetic.py --students 1000 --events 60 --seed 2026 \
        --out data/event_attendance.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core import synthetic  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=600)
    ap.add_argument("--events", type=int, default=48)
    ap.add_argument("--seed", type=int, default=settings.SYNTHETIC_SEED)
    ap.add_argument("--out", default=None,
                    help="CSV path (default data/synthetic/synthetic_registrations.csv); the metadata "
                         "is written next to it as <name>.metadata.json")
    a = ap.parse_args()
    r = synthetic.generate(a.students, a.events, a.seed)
    out = Path(a.out) if a.out else None
    path = synthetic.save(r, out, out.with_suffix(".metadata.json") if out else None)
    m = r.metadata
    print(f"SYNTHETIC dataset written to {path}")
    print(f"  {m['rows']} registrations, {m['n_students']} students, {m['n_events']} events, seed {m['seed']}")
    print(f"  class balance {m['class_balance']}, intercept {m['intercept']}")


if __name__ == "__main__":
    main()
