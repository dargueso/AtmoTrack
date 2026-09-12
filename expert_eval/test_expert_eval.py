"""
test_expert_eval.py — tests for the DANA expert evaluation site.

    cd expert_eval && python -m pytest -q test_expert_eval.py

The API test uses a built case pool; point EXPERT_EVAL_CASES_DIR at one
(e.g. from `build_cases.py --years 1970 --n-cases 20`) or it is skipped.
"""

import base64
import importlib
import json
import math
import os
import pathlib
import random
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
@pytest.fixture()
def client(tmp_path, monkeypatch):
    cases_dir = os.environ.get("EXPERT_EVAL_CASES_DIR")
    if not cases_dir or not (pathlib.Path(cases_dir) / "manifest.json").exists():
        pytest.skip("set EXPERT_EVAL_CASES_DIR to a built case pool")
    monkeypatch.setenv("EXPERT_EVAL_DB", str(tmp_path / "test.sqlite"))
    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c, app_module


def test_api_flow(client):
    c, app_module = client
    assert c.post("/api/session", json={"n_cases": 10}).status_code == 401
    r = c.post("/api/login", json={"name": "Test", "email": "bad"})
    assert r.status_code == 400
    r = c.post("/api/login", json={"name": "Test Expert", "email": "T@example.org",
                                   "experience": "Researcher"})  # fmt: skip
    assert r.status_code == 200
    cfg = c.get("/api/config").get_json()
    assert cfg["expert"]["label"] == "Test Expert"

    s = c.post("/api/session", json={"n_cases": 10}).get_json()
    sid, cases = s["session_id"], s["cases"]
    assert len(cases) == 10
    frame = c.get(cases[0]["frames"][cases[0]["center_index"]])
    assert frame.status_code == 200 and frame.data[:4] == b"\x89PNG"
    assert c.get(f"/api/overlay/{cases[0]['case_id']}.png").status_code == 403

    eval_dir = app_module.EVAL_DIR
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
        r = c.post("/api/answer", json={"session_id": sid, "case_id": case["case_id"], "unsure": i == 3,
                                        "frames_viewed": [0, 6], "response_ms": 1234, **body})  # fmt: skip
        assert r.status_code == 200, r.get_json()
    # duplicate answer is rejected
    dup = c.post("/api/answer", json={"session_id": sid, "case_id": cases[0]["case_id"], "has_dana": False})
    assert dup.status_code == 409
    # yes without clicks is rejected
    r = c.post("/api/answer", json={"session_id": sid, "case_id": cases[8]["case_id"], "has_dana": True})
    assert r.status_code == 400

    summ = c.post(f"/api/session/{sid}/end", json={}).get_json()
    assert summ["end_reason"] == "ended_early" and summ["n_answered"] == 7
    con = app_module.db.connect(os.environ["EXPERT_EVAL_DB"])
    got = {r["case_id"]: r["outcome"] for r in con.execute("SELECT case_id, outcome FROM responses")}
    assert got == expected
    agree = sum(o in ("agree_hit", "agree_null") for o in expected.values())
    assert summ["agree_maps"] == agree
    assert len(summ["disagreements"]) == 7 - agree
    assert summ["n_unsure"] == 1

    assert c.get(f"/api/overlay/{cases[0]['case_id']}.png").status_code == 200
    if summ["disagreements"]:
        d = summ["disagreements"][0]
        r = c.post("/api/review", json={"response_id": d["response_id"], "changed": False,
                                        "reasons": d["reason_options"][:1] + ["not allowed"], "comment": "hm"})  # fmt: skip
        assert r.status_code == 200
        rv = con.execute("SELECT * FROM reviews").fetchone()
        assert json.loads(rv["reasons"]) == d["reason_options"][:1] and rv["changed"] == 0

    more = c.post(f"/api/session/{sid}/extend", json={"n_cases": 5}).get_json()
    answered = set(expected)
    assert more["offset"] == 10 and not answered & {x["case_id"] for x in more["cases"]}

    # invite code login is independent of profile
    con.execute("INSERT INTO invite_codes (code, label, created_at) VALUES ('ABCD-EFGH', 'Lab-1', 'now')")
    con.commit()
    c.post("/api/logout", json={})
    r = c.post("/api/login", json={"code": "abcd-efgh"}).get_json()
    assert r["expert"]["label"] == "Lab-1" and r["expert"]["auth"] == "code"
    assert math.isfinite(summ["all_experts"]["agreement_rate"])
