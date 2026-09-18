#!/usr/bin/env python
"""
admin.py — Manage and analyse the DANA expert evaluation database.

    python admin.py add-codes --n 5 --label AEMET     # create invite codes
    python admin.py list-codes
    python admin.py export responses.csv              # one row per answer
    python admin.py export-tuning tuning.csv          # one row per (answer, cyclone object / click)
    python admin.py report [--top 20]                 # disagreement analysis
    python admin.py sweep [--include-unsure]          # isolation-threshold sweep vs expert labels
    python admin.py high-impact [--csv events.csv]    # expert verdicts on the high-impact events
    python admin.py rescore [--force]                 # re-score answers against cases/ (local database)
    python admin.py compare [--old V] [--new V]       # before/after tuning, on the same answers

Standard library only (plus the generated cases/ folder for tuning exports).
"""

import argparse
import csv
import json
import pathlib
import secrets
import statistics
import sys
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402
from criteria_names import CRITERIA_ORDER, DZ_QUANTILE_LEVELS  # noqa: E402
from evaluate import AGREE, evaluate_answer, load_case  # noqa: E402
from settings import load_settings  # noqa: E402

S = load_settings()
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SEASONS = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
           6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}  # fmt: skip


def con():
    c = db.connect(S["paths"]["db_path"])
    db.init(c)
    return c


def manifest():
    p = S["paths"]["cases_dir"] / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ---------------------------------------------------------------------------
# Invite codes
# ---------------------------------------------------------------------------
def cmd_add_codes(args):
    c = con()
    out = []
    for i in range(args.n):
        code = "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(2))
        label = args.label if args.n == 1 or not args.label else f"{args.label}-{i + 1}"
        c.execute(
            "INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)",
            (code, label, db.now()),
        )
        out.append((code, label))
    c.commit()
    for code, label in out:
        print(f"{code}\t{label or ''}")


def cmd_list_codes(_args):
    rows = (
        con()
        .execute(
            """SELECT i.code, i.label, i.created_at, i.expert_id,
                  (SELECT COUNT(*) FROM responses r WHERE r.expert_id = i.expert_id) AS n
           FROM invite_codes i ORDER BY i.created_at"""
        )
        .fetchall()
    )
    print(f"{'code':10s} {'label':20s} {'used':5s} answers")
    for r in rows:
        used = "yes" if r["expert_id"] else "no"
        print(f"{r['code']:10s} {(r['label'] or ''):20s} {used:5s} {r['n']}")


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
RESPONSE_SQL = """
SELECT r.*, c.time AS case_time, c.category, c.tags, c.algo_n_cols,
       e.auth AS expert_auth, COALESCE(e.name, e.code) AS expert_label,
       e.experience, e.affiliation, s.algo_version,
       v.changed AS review_changed, v.reasons AS review_reasons, v.comment AS review_comment
FROM responses r
JOIN cases c    ON c.case_id = r.case_id
JOIN experts e  ON e.id = r.expert_id
JOIN sessions s ON s.id = r.session_id
LEFT JOIN reviews v ON v.response_id = r.id
ORDER BY r.id
"""


def cmd_export(args):
    rows = con().execute(RESPONSE_SQL).fetchall()
    cols = [
        "id", "session_id", "expert_id", "expert_auth", "expert_label", "experience", "affiliation",
        "case_id", "case_time", "category", "tags", "algo_n_cols", "position", "has_dana", "unsure",
        "clicks", "outcome", "n_systems", "n_matched", "n_algo_missed", "n_algo_extra",
        "frames_viewed", "response_ms", "created_at", "review_changed", "review_reasons",
        "review_comment", "algo_version",
    ]  # fmt: skip
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[k] for k in cols])
    print(f"Wrote {len(rows)} responses to {args.out}")


def flatten_record(rec, prefix=""):
    """Criteria record -> flat dict of values/margins for CSV and sweeps."""
    out = {}
    if not rec:
        return out
    crit = rec.get("criteria", {})
    for name in CRITERIA_ORDER:
        c = crit.get(name)
        if not c:
            continue
        out[f"{prefix}{name}_pass"] = int(c["pass"])
        for k in ("value", "margin"):
            if k in c:
                out[f"{prefix}{name}_{k}"] = c[k]
    iso = crit.get("isolation", {})
    for k in ("dz_at_required_fraction", "dz_margin", "dz_median", "dz_min", "n_ring"):
        out[f"{prefix}isolation_{k}"] = iso.get(k)
    for lvl, q in zip(DZ_QUANTILE_LEVELS, iso.get("dz_quantiles") or []):
        out[f"{prefix}isolation_dz_q{int(round(lvl * 100)):02d}"] = q
    reg = crit.get("region", {})
    out[f"{prefix}com_lat"] = reg.get("com_lat")
    out[f"{prefix}com_lon"] = reg.get("com_lon")
    for k in ("zmin_lat", "zmin_lon", "zmin_dam", "area_km2", "n_cells", "cy_life_steps",
              "step_in_cy_life", "first_failed_criterion"):  # fmt: skip
        out[f"{prefix}{k}"] = rec.get(k)
    out[f"{prefix}failed_criteria"] = "|".join(rec.get("failed_criteria") or [])
    return out


def tuning_rows(c, cases_dir):
    """One row per (response, cyclone object) plus one per expert click without a cyclone."""
    eval_dir = cases_dir / "eval"
    diag = defaultdict(dict)
    for d in c.execute("SELECT response_id, click_idx, diagnostics FROM click_diagnostics"):
        diag[d["response_id"]][d["click_idx"]] = json.loads(d["diagnostics"])
    rows = []
    for r in c.execute(RESPONSE_SQL).fetchall():
        base = {
            "response_id": r["id"], "expert_id": r["expert_id"], "experience": r["experience"],
            "case_id": r["case_id"], "case_time": r["case_time"], "category": r["category"],
            "tags": r["tags"], "outcome": r["outcome"], "has_dana": r["has_dana"],
            "unsure": r["unsure"], "review_changed": r["review_changed"],
        }  # fmt: skip
        try:
            case = load_case(eval_dir, r["case_id"])
        except FileNotFoundError:
            continue
        clicks = json.loads(r["click_details"])
        algo = json.loads(r["algo_details"])
        matched_cols = {col["id"] for col in algo["cols"] if col["matched_click"] is not None}
        near_ids = {
            d["nearest_cyclone"]["id"]
            for d in clicks
            if d["matched_col_id"] is None and d["nearest_cyclone"] and "id" in d["nearest_cyclone"]
        }
        box = case.box
        e = S["build"]["expand_box_deg"]
        for cy in case.cyclones:
            near_box = (box[0] - e <= cy["zmin_lon"] <= box[1] + e) and (
                box[2] - e <= cy["zmin_lat"] <= box[3] + e
            )
            if not (cy["is_col"] or near_box or cy["id"] in near_ids):
                continue
            expert_dana = int(
                cy["id"] in matched_cols or (not cy["is_col"] and cy["id"] in near_ids)
            )
            rows.append(
                {
                    **base,
                    "kind": "cyclone_object",
                    "object_id": cy["id"],
                    "algo_col": int(cy["is_col"]),
                    "expert_dana": expert_dana,
                    "agree": int(expert_dana == int(cy["is_col"])),
                    **flatten_record(cy.get("record")),
                }
            )
        for d in clicks:
            if (
                d["matched_col_id"] is None
                and d["duplicate_of_col"] is None
                and ((d["nearest_cyclone"] or {}).get("no_cyclone_object"))
            ):
                dd = diag.get(r["id"], {}).get(d["idx"]) or {}
                iso = dd.get("isolation") or {}
                rows.append(
                    {
                        **base,
                        "kind": "expert_click_no_cyclone",
                        "object_id": None,
                        "algo_col": 0,
                        "expert_dana": 1,
                        "agree": 0,
                        "zmin_lat": dd.get("local_min_lat", d["lat"]),
                        "zmin_lon": dd.get("local_min_lon", d["lon"]),
                        "zmin_dam": dd.get("local_min_dam"),
                        "isolation_value": iso.get("value"),
                        "isolation_margin": iso.get("margin"),
                        "isolation_dz_at_required_fraction": iso.get("dz_at_required_fraction"),
                        "eastward_flow_value": (dd.get("eastward_flow") or {}).get("value"),
                        "region_margin": (dd.get("region") or {}).get("margin"),
                    }
                )
    return rows


def cmd_export_tuning(args):
    rows = tuning_rows(con(), S["paths"]["cases_dir"])
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.out}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _rate_table(title, groups, min_n=1):
    print(f"\n## {title}")
    print(f"{'group':38s} {'n':>5s} {'disagree':>9s} {'unsure':>7s}")
    for key, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < min_n:
            continue
        n = len(rs)
        dis = sum(r["outcome"] not in AGREE for r in rs)
        uns = sum(r["unsure"] for r in rs)
        print(f"{str(key)[:38]:38s} {n:5d} {100 * dis / n:8.0f}% {100 * uns / n:6.0f}%")


def _dist(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n=0"
    vals.sort()
    q = lambda p: vals[min(len(vals) - 1, int(p * (len(vals) - 1) + 0.5))]  # noqa: E731
    return f"n={len(vals):4d}  p10={q(0.1):8.2f}  median={statistics.median(vals):8.2f}  p90={q(0.9):8.2f}"


def cmd_report(args):
    c = con()
    rows = c.execute(RESPONSE_SQL).fetchall()
    if not rows:
        print("No responses yet.")
        return
    n_exp = c.execute("SELECT COUNT(*) FROM experts").fetchone()[0]
    n_ses = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    print(
        f"# DANA expert evaluation report\n\nexperts={n_exp} sessions={n_ses} responses={len(rows)}"
    )
    oc = Counter(r["outcome"] for r in rows)
    print("\n## Outcomes (expert = reference)")
    for k, v in oc.most_common():
        print(f"{k:20s} {v:5d} {100 * v / len(rows):5.1f}%")

    groups = defaultdict(list)
    for r in rows:
        groups[r["category"]].append(r)
    _rate_table("Disagreement by case category", groups)
    groups = defaultdict(list)
    for r in rows:
        for t in json.loads(r["tags"]) or ["(no tag)"]:
            groups[t].append(r)
    _rate_table("Disagreement by case tag", groups)
    groups = defaultdict(list)
    for r in rows:
        groups[SEASONS[int(r["case_time"][5:7])]].append(r)
    _rate_table("Disagreement by season", groups)
    groups = defaultdict(list)
    for r in rows:
        groups[r["experience"] or f"({r['expert_auth']})"].append(r)
    _rate_table("Disagreement by expert experience", groups)
    groups = defaultdict(list)
    for r in rows:
        groups[f"{r['expert_id']}: {r['expert_label']}"].append(r)
    _rate_table("Disagreement by expert", groups)

    # algorithm misses: which criteria rejected the system the expert marked
    first, anyf, nocy = Counter(), Counter(), 0
    n_clicks = 0
    for r in rows:
        if r["outcome"] not in ("algo_miss", "partial_match", "location_mismatch"):
            continue
        for d in json.loads(r["click_details"]):
            if d["matched_col_id"] is not None or d["duplicate_of_col"] is not None:
                continue
            n_clicks += 1
            nc = d["nearest_cyclone"] or {}
            if nc.get("no_cyclone_object"):
                nocy += 1
            elif nc.get("is_col"):
                first["(nearest object is a COL: location offset)"] += 1
            else:
                first[nc.get("first_failed_criterion") or "(passes all)"] += 1
                for f in nc.get("failed_criteria") or []:
                    anyf[f] += 1
    print(f"\n## Expert systems the algorithm did not detect (unmatched clicks: {n_clicks})")
    print(f"no tracked z500 cyclone within 300 km: {nocy}")
    print("first failed criterion of the nearest cyclone:")
    for k, v in first.most_common():
        print(f"   {k:45s} {v}")
    print("any failed criterion:")
    for k, v in anyf.most_common():
        print(f"   {k:45s} {v}")

    # margins of algorithm COLs the experts did / did not accept
    fa, ok = defaultdict(list), defaultdict(list)
    for r in rows:
        for col in json.loads(r["algo_details"])["cols"]:
            rec = col.get("record") or {}
            crit = rec.get("criteria", {})
            vals = {
                "isolation ring fraction margin": (crit.get("isolation") or {}).get("margin"),
                "isolation dz margin (m)": (crit.get("isolation") or {}).get("dz_margin"),
                "region margin (deg)": (crit.get("region") or {}).get("margin"),
                "min u200 poleward (m/s)": (crit.get("eastward_flow") or {}).get("value"),
                "z500 min (dam)": rec.get("zmin_dam"),
                "area (km2)": col.get("area_km2"),
                "COL life (h)": col.get("life_hours"),
                "hours since onset": col.get("hours_since_onset"),
                "hours to decay": col.get("hours_to_decay"),
            }
            target = ok if col["matched_click"] is not None else (fa if not r["has_dana"] else None)
            if target is None:
                continue
            for k, v in vals.items():
                target[k].append(v)
    print("\n## Algorithm COLs: rejected by experts (false alarms) vs confirmed (hits)")
    for k in fa.keys() | ok.keys():
        print(f"{k:32s} FA  {_dist(fa[k])}")
        print(f"{'':32s} HIT {_dist(ok[k])}")

    # location offsets
    off_mis, off_hit, same_parent = [], [], []
    for r in rows:
        for d in json.loads(r["click_details"]):
            if not d["cols"]:
                continue
            nearest = min(d["cols"], key=lambda x: x["dist_zmin_km"])
            if d["matched_col_id"] is not None:
                off_hit.append(nearest["dist_zmin_km"])
            elif r["outcome"] in ("location_mismatch", "partial_match"):
                off_mis.append(nearest["dist_zmin_km"])
                off_mis_mask = nearest["dist_mask_km"]
                same_parent.append((nearest["same_parent_cyclone"], off_mis_mask))
    print("\n## Location: distance from expert click to algorithm z500 minimum (km)")
    print(f"matched clicks     {_dist(off_hit)}")
    print(f"mismatched clicks  {_dist(off_mis)}")
    if same_parent:
        sp = sum(1 for s, _ in same_parent if s)
        print(f"mismatched clicks inside the COL's parent cyclone object: {sp}/{len(same_parent)}")
        print(
            f"mismatched clicks, distance to nearest COL cell: {_dist([m for _, m in same_parent])}"
        )

    # expert reviews
    rv = Counter()
    ch = Counter()
    for r in rows:
        if r["review_reasons"] is None:
            continue
        ch[(r["outcome"], r["review_changed"])] += 1
        for reason in json.loads(r["review_reasons"]):
            rv[(r["outcome"], reason)] += 1
    if ch:
        print("\n## Post-session reviews")
        for (o, changed), v in sorted(ch.items(), key=str):
            label = {1: "would change", 0: "keeps answer", None: "no choice"}[changed]
            print(f"{o:20s} {label:14s} {v}")
        for (o, reason), v in sorted(rv.items()):
            print(f"   {o:20s} {reason:40s} {v}")

    print_high_impact(c, args.min_answers)

    # most disputed cases
    print(f"\n## Most disputed cases (min {args.min_answers} answers)")
    agg = c.execute(
        """SELECT a.*, c.time, c.tags FROM case_agreement a JOIN cases c USING (case_id)
           WHERE a.n >= ? ORDER BY a.disagree_rate DESC, a.n DESC LIMIT ?""",
        (args.min_answers, args.top),
    ).fetchall()
    per_case = defaultdict(Counter)
    for r in rows:
        per_case[r["case_id"]][r["outcome"]] += 1
    for a in agg:
        oc = ", ".join(f"{k}:{v}" for k, v in per_case[a["case_id"]].most_common())
        print(f"{a['case_id']}  {a['time'][:13]}  {a['category']:17s} n={a['n']:3d} "
              f"disagree={100 * (a['disagree_rate'] or 0):3.0f}%  [{oc}]  tags={','.join(json.loads(a['tags']))}")  # fmt: skip


# ---------------------------------------------------------------------------
# High-impact events
# ---------------------------------------------------------------------------
def _pct(a, b):
    return round(100 * a / b) if b else None


def high_impact_rows(c):
    """One row per high-impact event (manifest "high_impact"), with the expert verdicts so far."""
    hi = manifest().get("high_impact", {})
    cases = {
        r["case_id"]: r for r in c.execute("SELECT case_id, time, category, algo_n_cols FROM cases")
    }
    per = defaultdict(list)
    for r in c.execute(RESPONSE_SQL).fetchall():
        if r["case_id"] in hi:
            per[r["case_id"]].append(r)
    out = []
    for cid, info in sorted(hi.items()):
        rs, case = per.get(cid, []), cases.get(cid)
        n = len(rs)
        yes = sum(r["has_dana"] for r in rs)
        both = [r for r in rs if r["has_dana"] and r["algo_n_cols"]]
        algo_col = bool(case and case["algo_n_cols"])
        share = yes / n if n else None
        majority = (
            None if not n else ("DANA" if share > 0.6 else "no DANA" if share < 0.4 else "split")
        )
        out.append(
            {
                "event": info["event"],
                "case_id": cid,
                "case_time": case["time"][:13] if case else cid,
                "slide_maps": " ".join(info.get("maps", [])),
                "algorithm": "COL" if algo_col else "no COL",
                "category": case["category"] if case else None,
                "answers": n,
                "experts_dana_pct": _pct(yes, n),
                "agree_pct": _pct(sum(r["outcome"] in AGREE for r in rs), n),
                "located_pct": _pct(sum(r["n_matched"] > 0 for r in both), len(both)),
                "unsure_pct": _pct(sum(r["unsure"] for r in rs), n),
                "expert_majority": majority,
                "algo_matches_majority": (
                    None if majority in (None, "split") else (majority == "DANA") == algo_col
                ),
                "reviews": sum(r["review_reasons"] is not None for r in rs),
                "comments": " | ".join(r["review_comment"] for r in rs if r["review_comment"]),
            }
        )
    return out


def print_high_impact(c, min_answers=2):
    rows = high_impact_rows(c)
    if not rows:
        return
    fmt = lambda v: "-" if v is None else f"{v}%"  # noqa: E731
    print(
        f"\n## High-impact events ({len(rows)} events; answers needed for a verdict: {min_answers})"
    )
    print(f"{'event':11s} {'case time':14s} {'algorithm':9s} {'answers':>7s} {'DANA':>5s} {'agree':>6s} "
          f"{'located':>7s} {'unsure':>6s}  majority  algo=majority")  # fmt: skip
    for r in rows:
        ok = r["algo_matches_majority"] if r["answers"] >= min_answers else None
        print(f"{r['event']:11s} {r['case_time']:14s} {r['algorithm']:9s} {r['answers']:7d} "
              f"{fmt(r['experts_dana_pct']):>5s} {fmt(r['agree_pct']):>6s} {fmt(r['located_pct']):>7s} "
              f"{fmt(r['unsure_pct']):>6s}  {(r['expert_majority'] or '-'):8s}  "
              f"{'-' if ok is None else 'yes' if ok else 'NO'}")  # fmt: skip
    judged = [
        r for r in rows if r["answers"] >= min_answers and r["algo_matches_majority"] is not None
    ]
    print(f"algorithm matches the expert majority on {sum(r['algo_matches_majority'] for r in judged)}"
          f"/{len(judged)} events with a clear majority; "
          f"{sum(r['answers'] < min_answers for r in rows)} events still need answers")  # fmt: skip
    for r in rows:
        if r["comments"]:
            print(f"   {r['event']}: {r['comments']}")


def cmd_high_impact(args):
    c = con()
    print_high_impact(c, args.min_answers)
    if args.csv:
        rows = high_impact_rows(c)
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["event"])
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} events to {args.csv}")


# ---------------------------------------------------------------------------
# Algorithm versions: re-score and compare
# ---------------------------------------------------------------------------
SCORE_COLS = (
    "outcome",
    "n_systems",
    "n_matched",
    "n_algo_missed",
    "n_algo_extra",
    "click_details",
    "algo_details",
)


def cmd_rescore(args):
    """Same as `php admin.php rescore` on the host, for a local (Flask) database."""
    c = con()
    ver = manifest().get("algo_version") or "unversioned"
    have = defaultdict(set)
    for x in c.execute("SELECT response_id, algo_version FROM response_scores"):
        have[x["response_id"]].add(x["algo_version"])
    rows = c.execute(
        """SELECT r.*, s.algo_version AS session_version FROM responses r
           JOIN sessions s ON s.id = r.session_id ORDER BY r.case_id, r.id"""
    ).fetchall()
    ins = f"""INSERT OR REPLACE INTO response_scores (response_id, algo_version, {", ".join(SCORE_COLS)}, scored_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    changes, done = Counter(), 0
    for r in rows:
        if not have[r["id"]]:
            orig = (json.loads(r["session_version"] or "{}") or {}).get("version") or "initial"
            c.execute(ins, (r["id"], orig, *[r[k] for k in SCORE_COLS], r["created_at"]))
            have[r["id"]].add(orig)
        if ver in have[r["id"]] and not args.force:
            continue
        case = load_case(S["paths"]["cases_dir"] / "eval", r["case_id"])
        res = evaluate_answer(case, bool(r["has_dana"]), json.loads(r["clicks"]))
        vals = [
            res[k] if k not in ("click_details", "algo_details") else json.dumps(res[k])
            for k in SCORE_COLS
        ]
        c.execute(ins, (r["id"], ver, *vals, db.now()))
        c.execute(
            f"UPDATE responses SET {', '.join(k + ' = ?' for k in SCORE_COLS)} WHERE id = ?",
            (*vals, r["id"]),
        )
        if res["outcome"] != r["outcome"]:
            changes[f"{r['outcome']} -> {res['outcome']}"] += 1
        done += 1
    c.commit()
    print(f"Re-scored {done} of {len(rows)} answers for algorithm version {ver}")
    for k, v in sorted(changes.items()):
        print(f"  {k:40s} {v}")
    if not changes:
        print("  no outcome changed")


def _version_scores(c):
    """{version: {response_id: score row}}, versions in the order they were first scored."""
    scores, first = defaultdict(dict), {}
    q = """SELECT x.*, r.has_dana, r.case_id, r.unsure, cs.tags FROM response_scores x
           JOIN responses r ON r.id = x.response_id JOIN cases cs ON cs.case_id = r.case_id"""
    for x in c.execute(q):
        scores[x["algo_version"]][x["response_id"]] = x
        first[x["algo_version"]] = min(first.get(x["algo_version"], x["scored_at"]), x["scored_at"])
    # answers never re-scored: their only score is the one in responses
    q = """SELECT r.*, r.id AS response_id, s.algo_version AS session_version, cs.tags, r.created_at AS scored_at
           FROM responses r JOIN sessions s ON s.id = r.session_id JOIN cases cs ON cs.case_id = r.case_id
           WHERE r.id NOT IN (SELECT response_id FROM response_scores)"""
    for r in c.execute(q):
        v = (json.loads(r["session_version"] or "{}") or {}).get("version") or "initial"
        scores[v][r["response_id"]] = r
        first[v] = min(first.get(v, r["scored_at"]), r["scored_at"])
    return {v: scores[v] for v in sorted(scores, key=first.get)}


PCT_KEYS = {
    "agreement",
    "expert DANA maps detected",
    "expert systems located",
    "false alarms on expert no-DANA maps",
}


def _summary(rows):
    n = len(rows)
    yes = [r for r in rows if r["has_dana"]]
    no = [r for r in rows if not r["has_dana"]]
    oc = Counter(r["outcome"] for r in rows)
    return {
        "answers": n,
        "agreement": _pct(oc["agree_hit"] + oc["agree_null"], n),
        "expert DANA maps detected": _pct(
            sum(r["outcome"] in ("agree_hit", "partial_match", "location_mismatch") for r in yes),
            len(yes),
        ),
        "expert systems located": _pct(
            sum(r["n_matched"] for r in yes), sum(r["n_systems"] for r in yes)
        ),
        "false alarms on expert no-DANA maps": _pct(oc["algo_false_alarm"], len(no)),
        **{
            o: oc[o]
            for o in (
                "agree_hit",
                "partial_match",
                "location_mismatch",
                "algo_miss",
                "algo_false_alarm",
                "agree_null",
            )
        },
    }


def cmd_compare(args):
    c = con()
    versions = _version_scores(c)
    if len(versions) < 2 and not (args.old and args.new):
        print(f"Only one algorithm version scored so far: {list(versions) or 'none'}. "
              "Refresh the cases, deploy and run rescore first.")  # fmt: skip
        return
    old = args.old or list(versions)[0]
    new = args.new or list(versions)[-1]
    common = sorted(set(versions[old]) & set(versions[new]))
    if args.include_unsure is False:
        common = [i for i in common if not versions[new][i]["unsure"]]
    a = _summary([versions[old][i] for i in common])
    b = _summary([versions[new][i] for i in common])
    print(f"# Algorithm {old} -> {new} on the same {len(common)} answers"
          f" (unsure {'included' if args.include_unsure else 'excluded'})\n")  # fmt: skip
    print(f"{'':40s} {old:>12s} {new:>12s}")
    for k in a:
        va, vb = a[k], b[k]
        unit = "%" if k in PCT_KEYS else ""
        print(
            f"{k:40s} {('-' if va is None else f'{va}{unit}'):>12s} {('-' if vb is None else f'{vb}{unit}'):>12s}"
        )
    trans = Counter(
        (versions[old][i]["outcome"], versions[new][i]["outcome"])
        for i in common
        if versions[old][i]["outcome"] != versions[new][i]["outcome"]
    )
    print("\n## Outcome changes (better = towards agree_hit / agree_null)")
    for (o1, o2), n in trans.most_common():
        mark = (
            "better"
            if o2 in AGREE and o1 not in AGREE
            else "worse"
            if o1 in AGREE and o2 not in AGREE
            else ""
        )
        print(f"  {o1:18s} -> {o2:18s} {n:5d}  {mark}")
    if not trans:
        print("  none")
    hi = manifest().get("high_impact", {})
    if hi:
        print("\n## High-impact events: agreement with the experts")
        print(f"{'event':11s} {'answers':>7s} {old:>12s} {new:>12s}")
        for cid, info in sorted(hi.items()):
            ids = [i for i in common if versions[new][i]["case_id"] == cid]
            ag = lambda v: _pct(sum(versions[v][i]["outcome"] in AGREE for i in ids), len(ids))  # noqa: E731
            fa, fb = ag(old), ag(new)
            print(
                f"{info['event']:11s} {len(ids):7d} {('-' if fa is None else f'{fa}%'):>12s} {('-' if fb is None else f'{fb}%'):>12s}"
            )


# ---------------------------------------------------------------------------
# Threshold sweep
# ---------------------------------------------------------------------------
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cmd_sweep(args):
    c = con()
    m = manifest()
    p = m.get("col_params") or {}
    rows = [
        r for r in tuning_rows(c, S["paths"]["cases_dir"]) if args.include_unsure or not r["unsure"]
    ]
    if not rows:
        print("No labelled rows yet.")
        return
    other = [k for k in CRITERIA_ORDER if k not in ("isolation", "cy_duration", "col_duration")]
    pcts = [round(0.60 + 0.05 * i, 2) for i in range(8)]  # 0.60 .. 0.95
    thrs = list(range(20, 141, 10))
    dt_h = p.get("DT", 1)
    durs = sorted({p.get("col_min_dur", 24)} | set(args.durations))

    def predict(r, pct, thr, dur_h):
        if r["kind"] != "cyclone_object":
            return 0
        if any(r.get(f"{k}_pass") == 0 for k in other):
            return 0
        lvl = int(round((1 - pct) * 100))
        q = _num(r.get(f"isolation_dz_q{lvl:02d}"))
        if q is None or not q > thr:
            return 0
        # approximate duration: COL life under current thresholds, capped by cyclone life
        life = _num(r.get("col_duration_value")) or 0
        cy_life = _num(r.get("cy_life_steps")) or 0
        steps = dur_h / dt_h
        if (
            r.get("isolation_pass") == 0
        ):  # would-be COL under new thresholds: best case = cyclone life
            life = cy_life
        return int(life >= steps and cy_life >= steps)

    results = []
    for dur in durs:
        for pct in pcts:
            for thr in thrs:
                hit = miss = fa = cn = 0
                for r in rows:
                    y, yhat = r["expert_dana"], predict(r, pct, thr, dur)
                    hit += y and yhat
                    miss += y and not yhat
                    fa += (not y) and yhat
                    cn += (not y) and not yhat
                csi = hit / (hit + miss + fa) if hit + miss + fa else 0
                pod = hit / (hit + miss) if hit + miss else 0
                far = fa / (hit + fa) if hit + fa else 0
                results.append((csi, pod, far, hit, miss, fa, cn, pct, thr, dur))
    cur = (p.get("col_percent_isolation"), p.get("col_thres_isolation"), p.get("col_min_dur"))
    print(f"rows={len(rows)} (unsure {'included' if args.include_unsure else 'excluded'}); "
          f"current thresholds pct={cur[0]} dz={cur[1]} m min_dur={cur[2]} h (DT={dt_h})")  # fmt: skip
    print(
        "NOTE: duration is approximated from stored lifetimes; treat as a hint, then re-run tracking."
    )
    print(
        f"{'pct':>5s} {'dz_m':>5s} {'dur_h':>5s} {'CSI':>6s} {'POD':>6s} {'FAR':>6s} {'hit':>5s} {'miss':>5s} {'fa':>5s} {'cn':>6s}"
    )
    for res in sorted(results, reverse=True)[: args.top]:
        csi, pod, far, hit, miss, fa, cn, pct, thr, dur = res
        mark = "  <- current" if (pct, thr, dur) == cur else ""
        print(
            f"{pct:5.2f} {thr:5d} {dur:5.0f} {csi:6.3f} {pod:6.3f} {far:6.3f} {hit:5d} {miss:5d} {fa:5d} {cn:6d}{mark}"
        )
    for res in results:
        if (res[7], res[8], res[9]) == cur:
            csi, pod, far, hit, miss, fa, cn, pct, thr, dur = res
            print(
                f"current: CSI={csi:.3f} POD={pod:.3f} FAR={far:.3f} hit={hit} miss={miss} fa={fa} cn={cn}"
            )


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-codes", help="create invite codes")
    a.add_argument("--n", type=int, default=1)
    a.add_argument("--label", default=None, help="who the codes are for, e.g. AEMET")
    a.set_defaults(func=cmd_add_codes)
    sub.add_parser("list-codes").set_defaults(func=cmd_list_codes)
    a = sub.add_parser("export", help="responses as CSV")
    a.add_argument("out")
    a.set_defaults(func=cmd_export)
    a = sub.add_parser("export-tuning", help="criteria values + expert labels as CSV")
    a.add_argument("out")
    a.set_defaults(func=cmd_export_tuning)
    a = sub.add_parser("report", help="disagreement analysis")
    a.add_argument("--top", type=int, default=20)
    a.add_argument("--min-answers", type=int, default=2)
    a.set_defaults(func=cmd_report)
    a = sub.add_parser("high-impact", help="expert verdicts on the high-impact events")
    a.add_argument("--csv", default=None)
    a.add_argument("--min-answers", type=int, default=3)
    a.set_defaults(func=cmd_high_impact)
    a = sub.add_parser(
        "rescore", help="re-score answers against the cases/ algorithm version (local DB)"
    )
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_rescore)
    a = sub.add_parser("compare", help="compare two algorithm versions on the same answers")
    a.add_argument("--old", default=None)
    a.add_argument("--new", default=None)
    a.add_argument("--include-unsure", action="store_true")
    a.set_defaults(func=cmd_compare)
    a = sub.add_parser("sweep", help="isolation threshold sweep against expert labels")
    a.add_argument("--include-unsure", action="store_true")
    a.add_argument(
        "--durations", type=float, nargs="*", default=[], help="extra col_min_dur values [h]"
    )
    a.add_argument("--top", type=int, default=15)
    a.set_defaults(func=cmd_sweep)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
