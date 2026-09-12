#!/usr/bin/env python
"""
app.py — Flask server for the DANA expert evaluation site.

    python app.py                 # serve on settings.toml [server] host:port
    python app.py --port 8080

Needs only Flask + the generated cases/ folder. Answers are stored in SQLite
(settings.toml [paths] db_path).
"""

import argparse
import json
import pathlib
import random
import re
import secrets
import sys
from datetime import timedelta

from flask import Flask, abort, g, jsonify, request, send_from_directory, session

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402
from evaluate import AGREE, evaluate_answer, load_case  # noqa: E402
from sampler import draw_session  # noqa: E402
from settings import load_settings  # noqa: E402

S = load_settings()
CASES_DIR = S["paths"]["cases_dir"]
EVAL_DIR = CASES_DIR / "eval"
EXPERIENCE = ("Operational forecaster", "Researcher", "Student / early career", "Other")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__, static_folder=None)


def _secret_key():
    p = S["paths"]["secret_key_file"]
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        p.chmod(0o600)
    return p.read_text().strip()


app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),
)


# ---------------------------------------------------------------------------
# Manifest and DB
# ---------------------------------------------------------------------------
_manifest = {"mtime": None, "data": None}


def manifest():
    p = CASES_DIR / "manifest.json"
    if not p.exists():
        abort(503, "No cases built yet: run build_cases.py")
    m = p.stat().st_mtime
    if _manifest["mtime"] != m:
        _manifest["data"] = json.loads(p.read_text())
        _manifest["mtime"] = m
        con = db.connect(S["paths"]["db_path"])
        db.init(con)
        db.sync_cases(con, EVAL_DIR, _manifest["data"]["cases"].keys())
        con.close()
    return _manifest["data"]


_db_ready = False


def get_db():
    global _db_ready
    if "db" not in g:
        g.db = db.connect(S["paths"]["db_path"])
        if not _db_ready:
            db.init(g.db)
            _db_ready = True
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def current_expert():
    eid = session.get("expert_id")
    if eid is None:
        return None
    return get_db().execute("SELECT * FROM experts WHERE id = ?", (eid,)).fetchone()


def require_expert():
    ex = current_expert()
    if ex is None:
        abort(401)
    return ex


def expert_public(ex):
    return {
        "id": ex["id"],
        "auth": ex["auth"],
        "name": ex["name"],
        "label": ex["name"] or ex["code"],
    }


def body():
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------------------
# Static content
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(HERE / "static", "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(HERE / "static", name)


@app.get("/frames/<fid>.png")
def frame(fid):
    if not re.fullmatch(r"\d{10}", fid):
        abort(404)
    resp = send_from_directory(CASES_DIR / "frames", f"{fid}.png")
    resp.cache_control.max_age = 86400
    return resp


@app.get("/api/overlay/<case_id>.png")
def overlay(case_id):
    ex = require_expert()
    ok = get_db().execute(
        "SELECT 1 FROM responses WHERE expert_id = ? AND case_id = ?", (ex["id"], case_id)
    ).fetchone()
    if not ok:
        abort(403)
    return send_from_directory(CASES_DIR / "overlays", f"{case_id}.png")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.get("/api/config")
def config():
    m = manifest()
    ex = current_expert()
    return jsonify(
        {
            "geometry": m["geometry"],
            "box": m["box"],
            "loop_hours": m.get("loop_hours"),
            "n_cases_pool": len(m["cases"]),
            "session_sizes": S["session"]["size_options"],
            "default_cases": S["session"]["default_cases"],
            "extend_by": S["session"]["extend_by"],
            "experience_options": EXPERIENCE,
            "expert": expert_public(ex) if ex else None,
        }
    )


@app.post("/api/login")
def login():
    b = body()
    con = get_db()
    code = (b.get("code") or "").strip().upper()
    if code:
        row = con.execute("SELECT * FROM invite_codes WHERE code = ?", (code,)).fetchone()
        if row is None:
            return jsonify({"error": "Unknown invite code"}), 400
        if row["expert_id"] is None:
            cur = con.execute(
                "INSERT INTO experts (auth, code, name, created_at) VALUES ('code', ?, ?, ?)",
                (code, row["label"], db.now()),
            )
            con.execute(
                "UPDATE invite_codes SET expert_id = ? WHERE code = ?", (cur.lastrowid, code)
            )
            con.commit()
            eid = cur.lastrowid
        else:
            eid = row["expert_id"]
    else:
        name = (b.get("name") or "").strip()
        email = (b.get("email") or "").strip().lower()
        experience = (b.get("experience") or "").strip()
        if not name or not EMAIL_RE.match(email) or experience not in EXPERIENCE:
            return jsonify({"error": "Please give your name, a valid email and your experience"}), 400
        row = con.execute("SELECT id FROM experts WHERE email = ?", (email,)).fetchone()
        if row:
            eid = row["id"]
            con.execute(
                "UPDATE experts SET name = ?, affiliation = ?, experience = ? WHERE id = ?",
                (name, (b.get("affiliation") or "").strip() or None, experience, eid),
            )
        else:
            eid = con.execute(
                """INSERT INTO experts (auth, name, email, affiliation, experience, created_at)
                   VALUES ('profile', ?, ?, ?, ?, ?)""",
                (name, email, (b.get("affiliation") or "").strip() or None, experience, db.now()),
            ).lastrowid
        con.commit()
    session.permanent = True
    session["expert_id"] = eid
    return jsonify({"expert": expert_public(current_expert())})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _case_payload(m, cid):
    c = m["cases"][cid]
    return {
        "case_id": cid,
        "month": c["month"],
        "frames": [f"/frames/{f}.png" for f in c["frames"]],
        "center_index": c["center_index"],
        "dt_hours": c["dt_hours"],
    }


def _draw(ex, n, extra_exclude=()):
    m = manifest()
    con = get_db()
    rows = con.execute(
        """SELECT c.case_id, c.category, c.tags, c.algo_n_cols,
                  a.n, a.n_disagree, a.n_unsure
           FROM cases c JOIN case_agreement a ON a.case_id = c.case_id"""
    ).fetchall()
    rows = [r for r in rows if r["case_id"] in m["cases"]]
    answered = {
        r["case_id"]
        for r in con.execute("SELECT case_id FROM responses WHERE expert_id = ?", (ex["id"],))
    }
    return draw_session(rows, n, answered | set(extra_exclude), S, random.Random())


def _own_session(sid, ex):
    row = get_db().execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row is None or row["expert_id"] != ex["id"]:
        abort(404)
    return row


@app.post("/api/session")
def new_session():
    ex = require_expert()
    m = manifest()
    try:
        n = int(body().get("n_cases") or S["session"]["default_cases"])
    except (TypeError, ValueError):
        n = S["session"]["default_cases"]
    n = max(1, min(n, 100))
    case_ids, n_pos = _draw(ex, n)
    if not case_ids:
        return jsonify({"error": "You have already answered every case in the pool. Thank you!"}), 409
    algo_version = json.dumps({"git_hash": m.get("git_hash"), "col_params": m.get("col_params")})
    con = get_db()
    sid = con.execute(
        """INSERT INTO sessions (expert_id, n_requested, n_pos_planned, case_order, algo_version, started_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ex["id"], n, n_pos, json.dumps(case_ids), algo_version, db.now()),
    ).lastrowid
    con.commit()
    return jsonify({"session_id": sid, "cases": [_case_payload(m, c) for c in case_ids]})


@app.post("/api/session/<int:sid>/extend")
def extend_session(sid):
    ex = require_expert()
    m = manifest()
    row = _own_session(sid, ex)
    order = json.loads(row["case_order"])
    n = int(body().get("n_cases") or S["session"]["extend_by"])
    new_ids, n_pos = _draw(ex, max(1, min(n, 100)), extra_exclude=order)
    if not new_ids:
        return jsonify({"error": "No more unseen cases in the pool. Thank you!"}), 409
    con = get_db()
    con.execute(
        """UPDATE sessions SET case_order = ?, n_requested = n_requested + ?,
           n_pos_planned = n_pos_planned + ?, ended_at = NULL, end_reason = NULL WHERE id = ?""",
        (json.dumps(order + new_ids), len(new_ids), n_pos, sid),
    )
    con.commit()
    return jsonify(
        {"session_id": sid, "offset": len(order), "cases": [_case_payload(m, c) for c in new_ids]}
    )


@app.post("/api/answer")
def answer():
    ex = require_expert()
    b = body()
    row = _own_session(int(b.get("session_id", -1)), ex)
    order = json.loads(row["case_order"])
    cid = str(b.get("case_id"))
    if cid not in order:
        abort(400, "Case not in this session")
    has_dana = bool(b.get("has_dana"))
    clicks = []
    for c in b.get("clicks") or []:
        try:
            clicks.append({"lat": float(c["lat"]), "lon": float(c["lon"])})
        except (KeyError, TypeError, ValueError):
            abort(400, "Bad click")
    if has_dana and not clicks:
        return jsonify({"error": "Mark the centre of each DANA on the map"}), 400
    case = load_case(EVAL_DIR, cid)
    res = evaluate_answer(case, has_dana, clicks)
    con = get_db()
    try:
        con.execute(
            """INSERT INTO responses (session_id, expert_id, case_id, position, has_dana, unsure,
                   clicks, click_details, algo_details, outcome, n_systems, n_matched,
                   n_algo_missed, n_algo_extra, frames_viewed, response_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["id"], ex["id"], cid, order.index(cid), int(has_dana),
                int(bool(b.get("unsure"))), json.dumps(clicks if has_dana else []),
                json.dumps(res["click_details"]), json.dumps(res["algo_details"]),
                res["outcome"], res["n_systems"], res["n_matched"], res["n_algo_missed"],
                res["n_algo_extra"], json.dumps(b.get("frames_viewed") or []),
                int(b.get("response_ms") or 0) or None, db.now(),
            ),
        )  # fmt: skip
    except db.sqlite3.IntegrityError:
        return jsonify({"error": "This case was already answered"}), 409
    con.commit()
    n_done = con.execute("SELECT COUNT(*) FROM responses WHERE session_id = ?", (row["id"],)).fetchone()[0]
    return jsonify({"ok": True, "n_answered": n_done, "n_total": len(order)})


REASONS = {
    "algo_false_alarm": "reasons_false_alarm",
    "algo_miss": "reasons_miss",
    "location_mismatch": "reasons_location",
}


def _reasons_for(outcome):
    r = S["review"]
    if outcome == "partial_match":
        return r["reasons_location"][:-1] + [
            "Algorithm missed one of my systems",
            "Algorithm found a system I do not consider a DANA",
            "Other",
        ]
    return r[REASONS[outcome]] if outcome in REASONS else []


def summary(sid, ex):
    con = get_db()
    row = _own_session(sid, ex)
    m = manifest()
    geo = m["geometry"]
    resp = con.execute(
        """SELECT r.*, c.algo_n_cols, v.changed, v.reasons, v.comment
           FROM responses r JOIN cases c ON c.case_id = r.case_id
           LEFT JOIN reviews v ON v.response_id = r.id
           WHERE r.session_id = ? ORDER BY r.position""",
        (sid,),
    ).fetchall()
    yes = [r for r in resp if r["has_dana"]]
    no = [r for r in resp if not r["has_dana"]]
    by_outcome = {}
    for r in resp:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    disagreements = []
    for r in resp:
        if r["outcome"] in AGREE:
            continue
        c = m["cases"].get(r["case_id"])
        disagreements.append(
            {
                "response_id": r["id"],
                "case_id": r["case_id"],
                "outcome": r["outcome"],
                "month": c["month"] if c else None,
                "frame": f"/frames/{c['frames'][c['center_index']]}.png" if c else None,
                "overlay": f"/api/overlay/{r['case_id']}.png",
                "clicks": json.loads(r["clicks"]),
                "click_matched": [d["matched_col_id"] is not None
                                  for d in json.loads(r["click_details"])],  # fmt: skip
                "unsure": bool(r["unsure"]),
                "n_matched": r["n_matched"],
                "n_algo_missed": r["n_algo_missed"],
                "n_algo_extra": r["n_algo_extra"],
                "algo_n_cols": r["algo_n_cols"],
                "reason_options": _reasons_for(r["outcome"]),
                "review": (
                    {"changed": r["changed"], "reasons": json.loads(r["reasons"] or "[]"),
                     "comment": r["comment"]}
                    if r["reasons"] is not None else None
                ),  # fmt: skip
            }
        )
    overall = con.execute(
        """SELECT COUNT(*) AS n, SUM(outcome IN ('agree_hit','agree_null')) AS agree FROM responses"""
    ).fetchone()
    return {
        "session_id": sid,
        "end_reason": row["end_reason"],
        "n_planned": len(json.loads(row["case_order"])),
        "n_answered": len(resp),
        "n_unsure": sum(r["unsure"] for r in resp),
        "expert_yes_maps": len(yes),
        "expert_systems": sum(r["n_systems"] for r in yes),
        "algo_found_maps": sum(1 for r in yes if r["algo_n_cols"] > 0),
        "systems_located": sum(r["n_matched"] for r in yes),
        "expert_no_maps": len(no),
        "algo_flagged_maps": sum(1 for r in no if r["algo_n_cols"] > 0),
        "algo_extra_systems": sum(r["n_algo_extra"] for r in yes),
        "agree_maps": sum(1 for r in resp if r["outcome"] in AGREE),
        "by_outcome": by_outcome,
        "disagreements": disagreements,
        "geometry": geo,
        "all_experts": {
            "n_responses": overall["n"],
            "agreement_rate": (overall["agree"] / overall["n"]) if overall["n"] else None,
        },
    }


@app.post("/api/session/<int:sid>/end")
def end_session(sid):
    ex = require_expert()
    row = _own_session(sid, ex)
    con = get_db()
    n_done = con.execute("SELECT COUNT(*) FROM responses WHERE session_id = ?", (sid,)).fetchone()[0]
    reason = "completed" if n_done >= len(json.loads(row["case_order"])) else "ended_early"
    con.execute(
        "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?", (db.now(), reason, sid)
    )
    con.commit()
    return jsonify(summary(sid, ex))


@app.post("/api/review")
def review():
    ex = require_expert()
    b = body()
    con = get_db()
    r = con.execute(
        "SELECT id, outcome FROM responses WHERE id = ? AND expert_id = ?",
        (int(b.get("response_id", -1)), ex["id"]),
    ).fetchone()
    if r is None:
        abort(404)
    allowed = set(_reasons_for(r["outcome"]))
    reasons = [x for x in (b.get("reasons") or []) if x in allowed]
    changed = b.get("changed")
    changed = None if changed is None else int(bool(changed))
    comment = (b.get("comment") or "").strip()[:2000] or None
    con.execute(
        """INSERT INTO reviews (response_id, changed, reasons, comment, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(response_id) DO UPDATE SET changed = excluded.changed,
               reasons = excluded.reasons, comment = excluded.comment,
               created_at = excluded.created_at""",
        (r["id"], changed, json.dumps(reasons), comment, db.now()),
    )
    con.commit()
    return jsonify({"ok": True})


@app.errorhandler(401)
def _unauth(_e):
    return jsonify({"error": "Please sign in"}), 401


def main():
    ap = argparse.ArgumentParser(description="DANA expert evaluation server")
    ap.add_argument("--host", default=S["server"]["host"])
    ap.add_argument("--port", type=int, default=S["server"]["port"])
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    con = db.connect(S["paths"]["db_path"])
    db.init(con)
    con.close()
    manifest()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
