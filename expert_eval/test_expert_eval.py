"""
test_expert_eval.py — tests for the DANA expert evaluation site.

    cd expert_eval && python -m pytest -q test_expert_eval.py

The API test uses a built case pool; point EXPERT_EVAL_CASES_DIR at one
(e.g. from `build_cases.py --years 1970 --n-cases 20`) or it is skipped.
"""

import base64
import email
import email.policy
import importlib
import json
import math
import os
import pathlib
import random
import re
import sys
import zlib

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evaluate  # noqa: E402
from sampler import case_weight, draw_session  # noqa: E402
from settings import load_settings  # noqa: E402


def encode_mask(mask):  # same encoding as build_cases.encode_mask (kept import-light)
    return base64.b64encode(zlib.compress(np.packbits(mask.ravel()).tobytes())).decode()


def test_encode_mask_matches_build_cases():
    bc = pytest.importorskip("build_cases")
    rng = np.random.default_rng(0)
    m = rng.random((261, 201)) > 0.97
    assert evaluate.decode_mask(bc.encode_mask(m)) == evaluate.decode_mask(encode_mask(m))


def test_mask_roundtrip():
    rng = np.random.default_rng(1)
    m = rng.random((37, 23)) > 0.8
    bits = evaluate.decode_mask(encode_mask(m))
    n = m.size
    assert [evaluate.mask_bit(bits, k) for k in range(n)] == m.ravel().astype(int).tolist()
    assert evaluate.mask_indices(bits, n) == np.flatnonzero(m.ravel()).tolist()


GRID = {"lat0": 75.0, "dlat": -0.25, "nlat": 261, "lon0": -30.0, "dlon": 0.25, "nlon": 201}


def test_grid_index():
    assert evaluate.grid_index(GRID, 75.0, -30.0) == (0, 0)
    assert evaluate.grid_index(GRID, 10.0, 20.0) == (260, 200)
    assert evaluate.grid_index(GRID, 40.1, 0.1) == (140, 120)
    assert evaluate.grid_index(GRID, 9.0, 0.0) is None
    lat, lon = evaluate.cell_latlon(GRID, 140 * 201 + 120)
    assert (lat, lon) == (40.0, 0.0)


def _disc(lat_c, lon_c, radius_deg):
    lat = GRID["lat0"] + GRID["dlat"] * np.arange(GRID["nlat"])
    lon = GRID["lon0"] + GRID["dlon"] * np.arange(GRID["nlon"])
    lon2d, lat2d = np.meshgrid(lon, lat)
    return (lat2d - lat_c) ** 2 + (lon2d - lon_c) ** 2 <= radius_deg**2


def _case(cols=(), extra_cyclones=()):
    """Synthetic case: cols = [(id, lat, lon)], extra non-COL cyclones likewise."""
    rec = lambda failed: {"first_failed_criterion": failed, "failed_criteria": [failed] if failed else [],  # noqa: E731
                          "criteria": {"isolation": {"pass": failed != "isolation", "margin": -0.1}}}  # fmt: skip
    d = {
        "case_id": "test",
        "grid": GRID,
        "box": [-15, 10, 30, 45],
        "cols": [],
        "cyclones": [],
    }
    for cid, la, lo in cols:
        m = _disc(la, lo, 3)
        d["cols"].append({"id": cid, "mask": encode_mask(m), "zmin_lat": la, "zmin_lon": lo,
                          "centroid_lat": la, "centroid_lon": lo})  # fmt: skip
        d["cyclones"].append({"id": cid, "mask": encode_mask(_disc(la, lo, 4)), "is_col": True,
                              "zmin_lat": la, "zmin_lon": lo, "record": rec(None)})  # fmt: skip
    for cid, la, lo in extra_cyclones:
        d["cyclones"].append({"id": cid, "mask": encode_mask(_disc(la, lo, 3)), "is_col": False,
                              "zmin_lat": la, "zmin_lon": lo, "record": rec("isolation")})  # fmt: skip
    return evaluate.CaseData(d)


def test_outcomes():
    one = _case(cols=[(5, 38, -5)], extra_cyclones=[(7, 42, 5)])
    none = _case(extra_cyclones=[(7, 42, 5)])
    two = _case(cols=[(5, 38, -5), (6, 36, 8)])

    r = evaluate.evaluate_answer(one, True, [{"lat": 38.5, "lon": -5.5}])
    assert r["outcome"] == "agree_hit" and r["n_matched"] == 1
    assert r["click_details"][0]["inside_col_id"] == 5

    r = evaluate.evaluate_answer(one, True, [{"lat": 42, "lon": 5}])
    assert r["outcome"] == "location_mismatch"
    nc = r["click_details"][0]["nearest_cyclone"]
    assert nc["id"] == 7 and nc["first_failed_criterion"] == "isolation"
    assert r["click_details"][0]["cols"][0]["dist_zmin_km"] > 500

    r = evaluate.evaluate_answer(none, True, [{"lat": 42.2, "lon": 5.1}])
    assert r["outcome"] == "algo_miss" and r["click_details"][0]["inside_cy_id"] == 7

    r = evaluate.evaluate_answer(none, True, [{"lat": 60, "lon": -25}])
    assert r["click_details"][0]["nearest_cyclone"] == {"no_cyclone_object": True}
    assert r["click_details"][0]["outside_box"]

    r = evaluate.evaluate_answer(one, False, [])
    assert r["outcome"] == "algo_false_alarm" and r["n_algo_extra"] == 1
    assert r["algo_details"]["cols"][0]["record"]["first_failed_criterion"] is None

    assert evaluate.evaluate_answer(none, False, [])["outcome"] == "agree_null"

    r = evaluate.evaluate_answer(two, True, [{"lat": 38, "lon": -5}])
    assert r["outcome"] == "partial_match" and r["n_algo_extra"] == 1

    r = evaluate.evaluate_answer(one, True, [{"lat": 38, "lon": -5}, {"lat": 38.3, "lon": -4.8}])
    assert r["outcome"] == "agree_hit"
    assert r["click_details"][1]["duplicate_of_col"] == 5 and r["n_systems"] == 1


def _pool(n_each=40):
    rows = []
    for cat in ("col_clear", "col_borderline", "nocol_clear", "nocol_borderline"):
        for i in range(n_each):
            rows.append({"case_id": f"{cat}-{i}", "category": cat, "tags": ["onset"] if "border" in cat else [],
                         "algo_n_cols": int(cat.startswith("col")), "n": 0, "n_disagree": 0, "n_unsure": 0})  # fmt: skip
    return rows


def test_sampler_positive_share():
    S = load_settings()
    rng = random.Random(3)
    rows = _pool()
    for _ in range(300):
        ids, n_pos = draw_session(rows, 10, set(), S, rng)
        assert len(ids) == len(set(ids)) == 10
        pos = sum(i.startswith("col_") for i in ids)
        assert 4 <= pos <= 6 and pos == n_pos
    ids, _ = draw_session(rows, 20, set(), S, rng)
    assert 8 <= sum(i.startswith("col_") for i in ids) <= 12


def test_sampler_exclusion_and_shortage():
    S = load_settings()
    rows = _pool(4)  # 16 cases; COL class only has 4 left after excluding col_clear
    exclude = {r["case_id"] for r in rows if r["category"] == "col_clear"}
    for seed in range(50):
        ids, n_pos = draw_session(rows, 10, exclude, S, random.Random(seed))
        assert not exclude & set(ids) and len(ids) == len(set(ids)) == 10
    ids, _ = draw_session(rows[:12], 10, exclude, S, random.Random(0))
    assert len(ids) == 8  # only 8 unseen cases left


def test_sampler_prefers_disputed_cases():
    S = load_settings()
    sp = S["sampling"]
    assert case_weight(10, 9, 0, sp) > case_weight(10, 0, 0, sp)
    rows = _pool(20)
    hard = rows[0]  # a col_clear case experts keep disagreeing with
    hard.update(n=10, n_disagree=9)
    easy = rows[1]
    easy.update(n=10, n_disagree=0)
    rng = random.Random(5)
    counts = {"hard": 0, "easy": 0}
    for _ in range(2000):
        ids, _ = draw_session(rows, 10, set(), S, rng)
        counts["hard"] += hard["case_id"] in ids
        counts["easy"] += easy["case_id"] in ids
    assert counts["hard"] > 1.5 * counts["easy"]


# ---------------------------------------------------------------------------
# API flow on a real (small) case pool
# ---------------------------------------------------------------------------
def cases_dir_or_skip():
    cases_dir = os.environ.get("EXPERT_EVAL_CASES_DIR")
    if not cases_dir or not (pathlib.Path(cases_dir) / "manifest.json").exists():
        pytest.skip("set EXPERT_EVAL_CASES_DIR to a built case pool")
    return pathlib.Path(cases_dir)


CODE_RE = re.compile(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}\b")


def flow_block(body):
    """Decode the [DANA-KEY]/[DANA-TO]/[DANA-BODY] block the Power Automate flow reads."""

    def between(tag):
        return body.split(f"[{tag}]")[-1].split(f"[/{tag}]")[0].strip()

    return {
        "key": between("DANA-KEY"),
        "to": base64.b64decode(between("DANA-TO")).decode(),
        "body": base64.b64decode(between("DANA-BODY")).decode(),
    }


def parse_outbox(raw_messages):
    """Emails written to DANA_MAIL_OUTBOX (in order) -> [{"to", "body", "code"}]."""
    out = []
    for raw in raw_messages:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        body = msg.get_body(("plain",)).get_content() if msg.is_multipart() else msg.get_content()
        m = CODE_RE.search(body)
        out.append({"to": str(msg["To"]).strip(), "reply_to": str(msg["Reply-To"] or "").strip(),
                    "subject": str(msg["Subject"]), "body": body, "code": m.group(0) if m else None})  # fmt: skip
    return out


class FlaskClient:
    """Adapter so the same API flow runs against Flask and the PHP server (test_php_backend.py)."""

    def __init__(self, test_client, db_path, eval_dir, outbox_dir):
        self.c, self.db_path, self.eval_dir, self.outbox_dir = (
            test_client,
            db_path,
            eval_dir,
            outbox_dir,
        )

    def outbox(self):
        files = (
            sorted(pathlib.Path(self.outbox_dir).glob("*.eml")) if self.outbox_dir.exists() else []
        )
        return parse_outbox([f.read_bytes() for f in files])

    def post(self, path, payload=None):
        r = self.c.post("/" + path, json=payload if payload is not None else {})
        return r.status_code, r.get_json(silent=True)

    def get(self, path):
        r = self.c.get("/" + path)
        return r.status_code, r.data

    def get_json(self, path):
        return json.loads(self.get(path)[1])

    def query(self, sql, params=()):
        import sqlite3

        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, params)]
        finally:
            con.close()

    def execute(self, sql, params=()):
        import sqlite3

        con = sqlite3.connect(self.db_path)
        try:
            con.execute(sql, params)
            con.commit()
        finally:
            con.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cases_dir = cases_dir_or_skip()
    db_path = tmp_path / "test.sqlite"
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("EXPERT_EVAL_DB", str(db_path))
    monkeypatch.setenv("DANA_MAIL_OUTBOX", str(outbox))
    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield FlaskClient(c, db_path, cases_dir / "eval", outbox)


def test_api_flow(client):
    api_flow(client)


def api_flow(c):
    """Full expert flow. c provides post(path, payload) -> (status, json), get(path) -> (status, bytes),
    get_json(path), query(sql) -> list of dicts, execute(sql) and eval_dir."""

    assert c.post("api/session", {"n_cases": 10})[0] == 401
    # sign-in is by code only
    assert c.post("api/login", {"name": "Test Expert", "experience": "Researcher"})[0] == 400
    assert c.post("api/login", {"code": "NOPE-NOPE"})[0] == 400

    # request a code by email
    who = {"name": "Test Expert", "affiliation": "UIB", "experience": "Researcher"}
    assert c.post("api/request-code", {**who, "email": "not-an-email"})[0] == 400
    assert c.post("api/request-code", {"email": "t.expert@example.org"})[0] == 400
    status, r = c.post("api/request-code", {**who, "email": " T.Expert@Example.org "})
    assert status == 200 and r["ok"], r
    mails = c.outbox()
    cfg = load_settings()["email"]
    assert len(mails) == 1 and mails[0]["code"]
    if (
        cfg.get("mode", "send") == "notify"
    ):  # request goes to the organisers, reply reaches the expert
        assert (
            mails[0]["to"] == cfg["notify_address"]
            and mails[0]["reply_to"] == "t.expert@example.org"
        )
        for text in ("Test Expert", "UIB", "Researcher", "t.expert@example.org"):
            assert text in mails[0]["body"]
        # automation block for the Power Automate flow
        auto = flow_block(mails[0]["body"])
        assert re.fullmatch(r"[0-9a-f]{32}", auto["key"])
        assert auto["to"] == "t.expert@example.org"
        assert mails[0]["code"] in auto["body"] and "Hello Test Expert" in auto["body"]
    else:  # code goes straight to the expert
        assert mails[0]["to"] == "t.expert@example.org" and "Test Expert" in mails[0]["body"]
    code = mails[0]["code"]
    status, _ = c.post("api/request-code", {**who, "email": "bot@example.org", "website": "x"})
    assert status == 200 and len(c.outbox()) == 1  # spam trap: nothing sent

    status, r = c.post("api/login", {"code": code.lower()})
    assert status == 200 and r["expert"]["auth"] == "code"
    assert c.get_json("api/config")["expert"]["label"] == "Test Expert"

    s = c.post("api/session", {"n_cases": 10})[1]
    sid, cases = s["session_id"], s["cases"]
    assert len(cases) == 10
    status, data = c.get(cases[0]["frames"][cases[0]["center_index"]])
    assert status == 200 and data[:4] == b"\x89PNG"
    assert c.get(f"api/overlay/{cases[0]['case_id']}.png")[0] == 403

    eval_dir = c.eval_dir
    expected = {}
    for i, case in enumerate(cases[:7]):
        ev = json.loads((eval_dir / f"{case['case_id']}.json").read_text())
        if i % 2 == 0 and ev["cols"]:  # click on the algorithm z500 minimum -> hit
            clicks = [{"lat": col["zmin_lat"], "lon": col["zmin_lon"]} for col in ev["cols"]]
            body = {"has_dana": True, "clicks": clicks}
            expected[case["case_id"]] = "agree_hit"
        elif i % 2 == 0:  # expert sees a DANA far away where the algorithm has none
            body = {"has_dana": True, "clicks": [{"lat": 37.0, "lon": -3.0}]}
            expected[case["case_id"]] = "algo_miss"
        else:
            body = {"has_dana": False, "clicks": []}
            expected[case["case_id"]] = "algo_false_alarm" if ev["cols"] else "agree_null"
        status, r = c.post("api/answer", {"session_id": sid, "case_id": case["case_id"], "unsure": i == 3,
                                          "frames_viewed": [0, 6], "response_ms": 1234, **body})  # fmt: skip
        assert status == 200, r
    # duplicate answer is rejected
    dup = c.post(
        "api/answer", {"session_id": sid, "case_id": cases[0]["case_id"], "has_dana": False}
    )
    assert dup[0] == 409
    # yes without clicks is rejected
    r = c.post("api/answer", {"session_id": sid, "case_id": cases[8]["case_id"], "has_dana": True})
    assert r[0] == 400

    summ = c.post(f"api/session/{sid}/end", {})[1]
    assert summ["end_reason"] == "ended_early" and summ["n_answered"] == 7
    got = {r["case_id"]: r["outcome"] for r in c.query("SELECT case_id, outcome FROM responses")}
    assert got == expected
    agree = sum(o in ("agree_hit", "agree_null") for o in expected.values())
    assert summ["agree_maps"] == agree
    assert len(summ["disagreements"]) == 7 - agree
    assert summ["n_unsure"] == 1

    assert c.get(f"api/overlay/{cases[0]['case_id']}.png")[0] == 200
    if summ["disagreements"]:
        d = summ["disagreements"][0]
        for comment in ("first", "updated"):  # second save updates the same review
            status, _ = c.post("api/review", {"response_id": d["response_id"], "changed": False,
                                              "reasons": d["reason_options"][:1] + ["not allowed"],
                                              "comment": comment})  # fmt: skip
            assert status == 200
        rv = c.query("SELECT * FROM reviews")
        assert len(rv) == 1 and rv[0]["comment"] == "updated"
        assert json.loads(rv[0]["reasons"]) == d["reason_options"][:1] and rv[0]["changed"] == 0

    more = c.post(f"api/session/{sid}/extend", {"n_cases": 5})[1]
    answered = set(expected)
    assert more["offset"] == 10 and not answered & {x["case_id"] for x in more["cases"]}

    # invite code login is independent of profile
    c.execute(
        "INSERT INTO invite_codes (code, label, created_at) VALUES ('ABCD-EFGH', 'Lab-1', 'now')"
    )
    c.post("api/logout", {})
    assert c.post("api/session", {"n_cases": 10})[0] == 401
    r = c.post("api/login", {"code": "abcd-efgh"})[1]
    assert r["expert"]["label"] == "Lab-1" and r["expert"]["auth"] == "code"
    assert math.isfinite(summ["all_experts"]["agreement_rate"])

    # the expert created from a requested code carries the request details
    ex = c.query("SELECT name, affiliation, experience FROM experts WHERE code = ?", (code,))[0]
    assert ex == {"name": "Test Expert", "affiliation": "UIB", "experience": "Researcher"}

    # lost code: the same address (any case) gets the same code again; 3 emails per address per day
    for addr in ("t.expert@example.org", "T.EXPERT@example.org"):
        assert c.post("api/request-code", {**who, "email": addr})[0] == 200
    assert [m["code"] for m in c.outbox()] == [code] * 3
    if cfg.get("mode", "send") == "notify":
        assert "repeat" in c.outbox()[-1]["subject"]
    assert c.post("api/request-code", {**who, "email": "t.expert@example.org"})[0] == 429
    assert len(c.outbox()) == 3
    # another address gets another code and another identity
    assert c.post("api/request-code", {**who, "email": "colleague@example.org"})[0] == 200
    other = c.outbox()[-1]["code"]
    assert other != code
    c.post("api/logout", {})
    r = c.post("api/login", {"code": other})[1]
    assert r["expert"]["id"] != c.query("SELECT id FROM experts WHERE code = ?", (code,))[0]["id"]
    assert c.query("SELECT n_sent FROM code_requests WHERE code = ?", (code,))[0]["n_sent"] == 3

    # email addresses are never stored anywhere in the database
    tables = [r["name"] for r in c.query("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        for row in c.query(f"SELECT * FROM {table}"):
            assert not any("@" in str(v) for v in row.values()), (table, row)


# ---------------------------------------------------------------------------
# SMTP transport against a local fake server (no real mail is sent)
# ---------------------------------------------------------------------------
class FakeSMTP:
    """Tiny SMTP server: EHLO, AUTH LOGIN, MAIL, RCPT, DATA, QUIT (no TLS). Records what it receives."""

    def __init__(self, password="app-password"):
        import socket
        import threading

        self.password, self.messages, self.logins = password, [], []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn, conn.makefile("rb") as rf:
                send = lambda s: conn.sendall(s.encode() + b"\r\n")  # noqa: E731
                send("220 fake ESMTP")
                env = {}
                while True:
                    line = rf.readline().decode().rstrip("\r\n")
                    if not line:
                        break
                    verb = line.split(" ")[0].upper()
                    if verb == "EHLO":
                        send("250-fake\r\n250 AUTH LOGIN")
                    elif verb == "AUTH":
                        parts = line.split(" ")
                        if len(parts) > 2:  # initial response (Python smtplib)
                            user = base64.b64decode(parts[2]).decode()
                        else:
                            send("334 VXNlcm5hbWU6")
                            user = base64.b64decode(rf.readline().strip()).decode()
                        send("334 UGFzc3dvcmQ6")
                        pw = base64.b64decode(rf.readline().strip()).decode()
                        self.logins.append((user, pw))
                        if pw != self.password:
                            send("535 5.7.8 bad credentials")
                            break
                        send("235 ok")
                    elif verb == "MAIL":
                        env["from"] = line.split(":", 1)[1].strip("<> ")
                        send("250 ok")
                    elif verb == "RCPT":
                        env["to"] = line.split(":", 1)[1].strip("<> ")
                        send("250 ok")
                    elif verb == "DATA":
                        send("354 go")
                        data = b""
                        while not data.endswith(b"\r\n.\r\n"):
                            chunk = rf.readline()
                            if not chunk:
                                break
                            data += chunk
                        self.messages.append({**env, "raw": data[:-5].replace(b"\r\n..", b"\r\n.")})
                        send("250 queued")
                    elif verb == "QUIT":
                        send("221 bye")
                        break
                    else:
                        send("250 ok")

    def close(self):
        self.sock.close()


def check_smtp_delivery(fake, post, query):
    """Shared assertions for the SMTP transport (Flask and PHP)."""
    who = {"name": "Ana Núñez", "affiliation": "AEMET", "experience": "Operational forecaster"}
    status, r = post("api/request-code", {**who, "email": "ana@example.org"})
    assert status == 200, r
    assert fake.logins[-1] == ("dana.site@example.com", "app-password")
    m = fake.messages[-1]
    assert m["from"] == "dana.site@example.com" and m["to"] == "ana@example.org"
    msg = email.message_from_bytes(m["raw"], policy=email.policy.default)
    assert msg["To"] == "ana@example.org" and "dana.site@example.com" in msg["From"]
    assert msg["Reply-To"] == "dana.site@example.com"  # replies go to the dedicated mailbox
    body = msg.get_content()
    code = CODE_RE.search(body).group(0)
    assert "Ana Núñez" in body and query("SELECT code FROM code_requests")[0]["code"] == code
    # wrong password: error for the visitor, nothing kept
    fake.password = "other"
    status, r = post("api/request-code", {**who, "email": "bob@example.org"})
    assert status == 500 and "could not send" in r["error"]
    assert len(query("SELECT code FROM code_requests")) == 1


def smtp_settings(tmp_path, fake):
    """settings.toml copy using the fake SMTP server, plus a credentials file."""
    import tomllib

    raw = (HERE / "settings.toml").read_text()
    s = tomllib.loads(raw)
    s["email"].update(mode="send", transport="smtp", smtp_host="127.0.0.1", smtp_port=fake.port,
                      smtp_security="none", from_address="", reply_to="")  # fmt: skip
    cred = tmp_path / "smtp_credentials.json"
    cred.write_text(json.dumps({"username": "dana.site@example.com", "password": "app-password"}))
    return s, cred


def test_smtp_transport_flask(tmp_path, monkeypatch):
    fake = FakeSMTP()
    try:
        s, cred = smtp_settings(tmp_path, fake)
        monkeypatch.setenv("EXPERT_EVAL_DB", str(tmp_path / "t.sqlite"))
        monkeypatch.setenv("DANA_SMTP_CREDENTIALS", str(cred))
        monkeypatch.delenv("DANA_MAIL_OUTBOX", raising=False)
        import app as app_module

        app_module = importlib.reload(app_module)
        app_module.S["email"].update(s["email"])
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            fc = FlaskClient(c, tmp_path / "t.sqlite", None, tmp_path / "outbox")
            check_smtp_delivery(fake, fc.post, fc.query)
    finally:
        fake.close()


def high_impact_pool():
    rows = _pool(
        20
    )  # col_clear 0-19, col_borderline 20-39, nocol_clear 40-59, nocol_borderline 60-79
    his = [rows[0], rows[25], rows[45], rows[70]]
    for r in his:
        r["tags"] = r["tags"] + ["high_impact"]
    his[0]["n"] = 50  # already well answered: should come up much less
    return rows, [h["case_id"] for h in his]


def check_high_impact_draws(draws, hi_ids):
    from collections import Counter

    counts = Counter()
    for ids, n_pos in draws:
        hi_in = [i for i in ids if i in hi_ids]
        assert (
            len(ids) == len(set(ids)) == 10 and len(hi_in) >= 1
        )  # at least 10 % of a 10-map session
        pos = sum(i.startswith("col_") for i in ids)
        assert 4 <= pos <= 6 and pos == n_pos
        counts.update(hi_in)
    assert counts[hi_ids[0]] < 0.3 * counts[hi_ids[1]]


def test_sampler_high_impact_share():
    S = load_settings()
    assert S["session"]["high_impact_fraction"] == 0.1
    rows, hi_ids = high_impact_pool()
    rng = random.Random(9)
    check_high_impact_draws([draw_session(rows, 10, set(), S, rng) for _ in range(2000)], hi_ids)
    # all high-impact cases already answered by this expert: sessions still complete
    ids, _ = draw_session(rows, 10, set(hi_ids), S, rng)
    assert len(ids) == 10 and not set(hi_ids) & set(ids)


# ---------------------------------------------------------------------------
# After tuning: refreshed cases, rescore, compare
# ---------------------------------------------------------------------------
def test_rescore_and_compare(tmp_path, monkeypatch, capsys):
    import shutil
    import types

    src = cases_dir_or_skip()
    man = json.loads((src / "manifest.json").read_text())
    evals = {cid: json.loads((src / "eval" / f"{cid}.json").read_text()) for cid in man["cases"]}
    with_col = [cid for cid, e in evals.items() if e["cols"]][:6]
    without = [cid for cid, e in evals.items() if not e["cols"]][:6]
    keep = with_col + without
    cases = tmp_path / "cases"
    (cases / "eval").mkdir(parents=True)
    for cid in keep:
        shutil.copy(src / "eval" / f"{cid}.json", cases / "eval")
    m = {
        **man,
        "cases": {k: man["cases"][k] for k in keep},
        "high_impact": {},
        "algo_version": "v1",
    }
    (cases / "manifest.json").write_text(json.dumps(m))
    monkeypatch.setenv("EXPERT_EVAL_CASES_DIR", str(cases))
    monkeypatch.setenv("EXPERT_EVAL_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("DANA_MAIL_OUTBOX", str(tmp_path / "outbox"))
    import admin as admin_module
    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.S["session"]["high_impact_fraction"] = 0
    with app_module.app.test_client() as c:
        with app_module.app.app_context():
            con = app_module.get_db()
            app_module.manifest()
            con.execute(
                "INSERT INTO invite_codes (code, label, created_at) VALUES ('RSCR-TEST', 'r', 'now')"
            )
            con.commit()
        c.post("/api/login", json={"code": "RSCR-TEST"})
        s = c.post("/api/session", json={"n_cases": 12}).get_json()
        for case in s[
            "cases"
        ]:  # click on the algorithm centre when it has one -> agree_hit / agree_null
            e = evals[case["case_id"]]
            body = {"has_dana": bool(e["cols"]),
                    "clicks": [{"lat": e["cols"][0]["zmin_lat"], "lon": e["cols"][0]["zmin_lon"]}] if e["cols"] else []}  # fmt: skip
            assert (
                c.post(
                    "/api/answer",
                    json={"session_id": s["session_id"], "case_id": case["case_id"], **body},
                ).status_code
                == 200
            )

    # "tuned" algorithm v2: the first COL case loses its detection
    tuned = with_col[0]
    e = json.loads((cases / "eval" / f"{tuned}.json").read_text())
    e["cols"], e["algo_n_cols"] = [], 0
    (cases / "eval" / f"{tuned}.json").write_text(json.dumps(e))
    m["algo_version"] = "v2"
    (cases / "manifest.json").write_text(json.dumps(m))
    admin_module = importlib.reload(admin_module)
    importlib.reload(evaluate)._load_case.cache_clear()
    admin_module.load_case = evaluate.load_case
    admin_module.evaluate_answer = evaluate.evaluate_answer

    admin_module.cmd_rescore(types.SimpleNamespace(force=False))
    out = capsys.readouterr().out
    assert "Re-scored 12 of 12" in out and "agree_hit -> algo_miss" in out
    con = admin_module.con()
    assert (
        con.execute("SELECT outcome FROM responses WHERE case_id = ?", (tuned,)).fetchone()[0]
        == "algo_miss"
    )
    versions = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT algo_version, COUNT(*) FROM response_scores GROUP BY algo_version"
        )
    }
    assert versions == {"v1": 12, "v2": 12}
    admin_module.cmd_rescore(types.SimpleNamespace(force=False))  # nothing left to do
    assert "Re-scored 0 of 12" in capsys.readouterr().out

    admin_module.cmd_compare(types.SimpleNamespace(old=None, new=None, include_unsure=False))
    out = capsys.readouterr().out
    assert (
        "v1 -> v2 on the same 12 answers" in out
        and "agree_hit          -> algo_miss" in out
        and "worse" in out
    )
