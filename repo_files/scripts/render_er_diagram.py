"""Render the ER diagram (docs/er_diagram.png and docs/er_diagram.mmd) from the LIVE schema.

Reading tables, keys and foreign keys from a freshly migrated database guarantees
the diagram always matches the real schema.

    python scripts/render_er_diagram.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from core.db.connection import Database  # noqa: E402

GROUPS = {
    "Reference data": ("#E8EEF7", ["datasets", "courses", "app_settings", "audit_log"]),
    "Core records": ("#E7F4EC", ["students", "events", "weather_forecasts", "registrations"]),
    "Models": ("#F3EDF9", ["model_versions", "model_metrics"]),
    "Predictions (append-only)": ("#FDF1E4", ["prediction_runs", "predictions"]),
    "Actual outcomes & evaluation": ("#FBE9EC", ["attendance_records", "event_outcomes", "prediction_evaluations",
                                                 "prediction_evaluation_items"]),
}
# grid positions (column, row-top) in layout units, chosen so related tables sit side by side
POS = {
    "courses": (0, 0), "datasets": (0, 5.0), "app_settings": (0, 11.0), "audit_log": (0, 14.6),
    "students": (1, 0), "model_versions": (1, 10.0), "model_metrics": (1, 17.6),
    "registrations": (2, 0), "attendance_records": (2, 6.6), "predictions": (2, 11.6),
    "events": (3, 0), "prediction_evaluation_items": (3, 8.2), "prediction_evaluations": (3, 12.4),
    "weather_forecasts": (4, 0), "event_outcomes": (4, 5.2), "prediction_runs": (4, 10.4),
}
PROVENANCE_ONLY = {"dataset_id", "training_dataset_id", "input_dataset_id"}   # drawn as a tag, not a line
KEY_EXTRA = {   # a few informative non-key columns shown per table
    "students": ["year", "experience_level", "data_source"],
    "events": ["event_type", "event_date", "event_day", "time_slot", "status"],
    "weather_forecasts": ["forecast", "forecast_obtained_at", "source"],
    "registrations": ["registration_days_before", "has_exam_near_event", "status"],
    "predictions": ["probability", "predicted_attend", "threshold", "certainty", "forecast_used",
                    "input_snapshot", "predicted_utc"],
    "prediction_runs": ["run_type", "n_predictions", "created_utc"],
    "model_versions": ["model_key", "version", "is_active", "threshold", "artifact_sha256"],
    "model_metrics": ["evaluation_set", "metric", "value"],
    "attendance_records": ["attended", "source", "recorded_utc"],
    "event_outcomes": ["method", "actual_attendees", "walk_ins"],
    "prediction_evaluations": ["expected_attendance", "actual_attendees", "error", "percentage_error",
                               "accuracy", "target_is_simulated"],
    "datasets": ["kind", "sha256", "row_count"],
    "courses": ["name", "department"],
    "app_settings": ["value"],
    "audit_log": ["action", "entity"],
}
COL_W, ROW_H, BOX_W = 4.6, 0.42, 4.0


def schema(db):
    tables = {}
    for t in [r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table' "
                                          "AND name NOT IN ('sqlite_sequence','schema_migrations') ORDER BY name")]:
        cols = db.query(f'PRAGMA table_info("{t}")')
        fks = db.query(f'PRAGMA foreign_key_list("{t}")')
        uniques = []
        for idx in db.query(f'PRAGMA index_list("{t}")'):
            if idx["unique"] and idx["origin"] == "u":
                uniques.append([c["name"] for c in db.query(f'PRAGMA index_info("{idx["name"]}")')])
        tables[t] = {"cols": cols, "fks": fks, "uniques": uniques}
    return tables


def render_png(tables, out: Path):
    fig, ax = plt.subplots(figsize=(25, 22))
    ax.set_xlim(-0.3, 5 * COL_W + 0.6)
    ax.set_ylim(-23.5, 1.6)
    ax.axis("off")
    colour = {t: c for _, (c, ts) in GROUPS.items() for t in ts}
    anchor = {}
    for t, info in tables.items():
        cx, top = POS[t]
        x, y0 = cx * COL_W, -top
        fk_cols = {f["from"] for f in info["fks"]}
        pk = [c["name"] for c in info["cols"] if c["pk"]]
        shown = pk + [c for c in fk_cols if c not in pk] + [c for c in KEY_EXTRA.get(t, []) if c not in pk]
        hidden = len(info["cols"]) - len(set(shown))
        lines = []
        rows = {}
        for c in dict.fromkeys(shown):
            tag = " ".join(x for x, ok in (("PK", c in pk), ("FK", c in fk_cols)) if ok)
            if any(c in u for u in info["uniques"]):
                tag = (tag + " UQ").strip()
            if c in PROVENANCE_ONLY and c in fk_cols:
                c = c + "  → datasets"
            rows[c.split()[0]] = len(lines)
            lines.append((tag, c))
        if hidden > 0:
            lines.append(("", f"+ {hidden} more column(s)"))
        h = (len(lines) + 1) * ROW_H + 0.15
        ax.add_patch(FancyBboxPatch((x, y0 - h), BOX_W, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    fc=colour.get(t, "#eee"), ec="#2d3a4a", lw=1.3))
        ax.add_patch(FancyBboxPatch((x, y0 - ROW_H - 0.05), BOX_W, ROW_H + 0.05,
                                    boxstyle="round,pad=0.02,rounding_size=0.12", fc="#1f2b3d", ec="#1f2b3d"))
        ax.text(x + 0.15, y0 - ROW_H / 2 - 0.03, t, color="white", fontsize=11.5, weight="bold", va="center")
        for i, (tag, c) in enumerate(lines):
            yy = y0 - ROW_H * (i + 1.5) - 0.08
            ax.text(x + 0.15, yy, tag, fontsize=8.5, color="#a33" if "PK" in tag else "#2459a8",
                    weight="bold", va="center")
            ax.text(x + 0.85, yy, c, fontsize=9.5, color="#555" if c.startswith("+") else "#111",
                    style="italic" if c.startswith("+") else "normal", va="center")
        anchor[t] = (x, y0, h, rows)
    # relationships: from the FK row of the child to the PK row of the parent
    def row_y(tbl, col):
        x, y0, h, rows = anchor[tbl]
        return y0 - ROW_H * (rows.get(col, 0) + 1.5) - 0.08
    for t, info in tables.items():
        for f in info["fks"]:
            if f["from"] in PROVENANCE_ONLY:
                continue
            p = f["table"]
            (x1, _, _, _), (x2, _, _, _) = anchor[t], anchor[p]
            ya, yb = row_y(t, f["from"]), row_y(p, f["to"])
            if abs(x1 - x2) < 0.1:          # same column: loop round the right-hand side
                a, b, style = (x1 + BOX_W, ya), (x2 + BOX_W, yb), "arc3,rad=0.45"
            elif x2 < x1:
                a, b, style = (x1, ya), (x2 + BOX_W, yb), "arc3,rad=0.0"
            else:
                a, b, style = (x1 + BOX_W, ya), (x2, yb), "arc3,rad=0.0"
            ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="-|>", color="#33507a", lw=1.2, alpha=0.8,
                                                            connectionstyle=style, shrinkA=1, shrinkB=1))
            ax.plot(*a, "o", ms=3.5, color="#33507a")
    ax.text(0, 1.2, "EventPulse AI — database schema (generated from the live SQLite schema)",
            fontsize=17, weight="bold", color="#1f2b3d")
    ax.text(0, 0.65, "Arrows point from the child table (holding the FK) to the parent. "
                     "PK = primary key, FK = foreign key, UQ = part of a UNIQUE constraint. "
                     "Optional dataset provenance links are shown as  → datasets.",
            fontsize=10.5, color="#444")
    for i, (g, (c, _)) in enumerate(GROUPS.items()):
        ax.add_patch(FancyBboxPatch((i * 4.3, -23.2), 0.35, 0.35, boxstyle="round,pad=0.02", fc=c, ec="#2d3a4a"))
        ax.text(i * 4.3 + 0.5, -23.03, g, fontsize=10, va="center")
    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_mermaid(tables) -> str:
    out = ["erDiagram"]
    for t, info in tables.items():
        fk_cols = {f["from"] for f in info["fks"]}
        out.append(f"    {t} {{")
        for c in info["cols"]:
            typ = (c["type"] or "TEXT").split("(")[0]
            keys = ",".join(k for k, ok in (("PK", c["pk"]), ("FK", c["name"] in fk_cols),
                                            ("UK", any(c["name"] in u for u in info["uniques"]))) if ok)
            out.append(f"        {typ} {c['name']}{(' ' + keys) if keys else ''}")
        out.append("    }")
    for t, info in tables.items():
        for f in info["fks"]:
            col = next(c for c in info["cols"] if c["name"] == f["from"])
            unique_fk = any(u == [f["from"]] for u in info["uniques"]) or (col["pk"] and t == "event_outcomes")
            left = "||" if col["notnull"] or col["pk"] else "|o"
            right = "o|" if unique_fk else "o{"
            out.append(f'    {f["table"]} {left}--{right} {t} : "{f["from"]}"')
    return "\n".join(out) + "\n"


def main():
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "er.db")
        tables = schema(db)
        db.close()
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "er_diagram.mmd").write_text(render_mermaid(tables))
    render_png(tables, docs / "er_diagram.png")
    print(f"{len(tables)} tables, {sum(len(t['fks']) for t in tables.values())} foreign keys -> docs/er_diagram.png")


if __name__ == "__main__":
    main()
