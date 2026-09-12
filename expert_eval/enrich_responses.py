#!/usr/bin/env python
"""
enrich_responses.py — Offline click diagnostics for the expert answers.

For every expert click, recompute the COL criteria (ring isolation, poleward
u200, box membership) around the local z500 minimum nearest to the click,
using the fields stored by build_cases.py (cases/eval/fields/<case_id>.npz).
This is most informative where the expert marked a DANA but no tracked z500
cyclone exists, and it can be re-run after the thresholds change.

    python enrich_responses.py            # only clicks not yet processed
    python enrich_responses.py --all      # recompute everything
"""

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402
from criteria import ColParams, grid_spacing, local_click_diagnostics  # noqa: E402
from settings import load_settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--all", action="store_true", help="recompute already processed clicks")
    ap.add_argument("--search-km", type=float, default=300.0)
    args = ap.parse_args()

    S = load_settings()
    cases_dir = S["paths"]["cases_dir"]
    m = json.loads((cases_dir / "manifest.json").read_text())
    params = ColParams(**m["col_params"])
    version = m.get("git_hash")

    con = db.connect(S["paths"]["db_path"])
    db.init(con)
    done = set()
    if not args.all:
        done = {
            (r["response_id"], r["click_idx"])
            for r in con.execute("SELECT response_id, click_idx FROM click_diagnostics")
        }
    rows = con.execute(
        "SELECT id, case_id, click_details FROM responses WHERE has_dana = 1 ORDER BY case_id"
    ).fetchall()

    cache = {"case": None}
    n = 0
    for r in rows:
        clicks = json.loads(r["click_details"])
        todo = [d for d in clicks if (r["id"], d["idx"]) not in done]
        if not todo:
            continue
        if cache["case"] != r["case_id"]:
            f = np.load(cases_dir / "eval" / "fields" / f"{r['case_id']}.npz")
            lon2d, lat2d = np.meshgrid(f["lon"].astype(float), f["lat"].astype(float))
            z = (f["z500_m_minus_5500"].astype(np.float64) + 5500.0) * params.g
            u = f["u200"].astype(np.float64)
            _, gs = grid_spacing(lat2d, lon2d)
            cache.update(case=r["case_id"], lat2d=lat2d, lon2d=lon2d, z=z, u=u, gs=gs)
        for d in todo:
            diag = local_click_diagnostics(
                cache["z"], cache["u"], cache["lat2d"], cache["lon2d"],
                d["lat"], d["lon"], params, cache["gs"], search_km=args.search_km,
            )  # fmt: skip
            con.execute(
                """INSERT INTO click_diagnostics (response_id, click_idx, diagnostics, algo_version, computed_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(response_id, click_idx) DO UPDATE SET diagnostics = excluded.diagnostics,
                       algo_version = excluded.algo_version, computed_at = excluded.computed_at""",
                (r["id"], d["idx"], json.dumps(diag), version, db.now()),
            )
            n += 1
    con.commit()
    print(f"Computed diagnostics for {n} clicks")


if __name__ == "__main__":
    main()
