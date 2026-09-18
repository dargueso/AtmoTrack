"""
test_php_backend.py — the PHP server (php/) must behave like the Python one.

Local PHP (needs the zlib extension):
    EXPERT_EVAL_CASES_DIR=cases PHP_BIN=/path/to/php python -m pytest -q test_php_backend.py

On the web host, after `./host.sh deploy` (same PHP and SQLite 3.7.17 as the live site):
    EXPERT_EVAL_CASES_DIR=cases PHP_SSH_HOST=meteorologia@meteorologia.uib.es \\
    PHP_SSH_KEY=~/.ssh/meteo python -m pytest -q test_php_backend.py

Checks:
- evaluate.php vs evaluate.py on generated clicks over real cases (outcomes, matches, distances)
- sampler.php: positive share, exclusion, preference for disputed cases
- the full API flow of test_expert_eval.api_flow against `php -S` with php/dev_router.php
  (on the host the test database lives on the same NFS disk as the real one, and is removed after)
"""

import http.cookiejar
import json
import os
import pathlib
import random
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evaluate  # noqa: E402
from settings import server_settings_json  # noqa: E402
from test_expert_eval import (  # noqa: E402
    FakeSMTP,
    _pool,
    api_flow,
    cases_dir_or_skip,
    check_high_impact_draws,
    check_smtp_delivery,
    high_impact_pool,
    parse_outbox,
    smtp_settings,
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url, proc, tries=150):
    for _ in range(tries):
        if proc.poll() is not None:
            raise RuntimeError(
                f"PHP server exited: {proc.stderr.read().decode(errors='replace')[-2000:]}"
            )
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("PHP server did not start")


class LocalRunner:
    remote = False

    def __init__(self, exe, cases_dir, tmp):
        self.exe, self.cases_dir, self.tmp = exe, cases_dir.resolve(), tmp
        self.settings = tmp / "settings.json"
        self.settings.write_text(server_settings_json())

    def devtool(self, job, env=None):
        out = subprocess.run([self.exe, str(HERE / "php" / "devtool.php")], input=json.dumps(job),
                             capture_output=True, text=True, env={**os.environ, **(env or {})})  # fmt: skip
        assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
        return json.loads(out.stdout)

    def start_server(self):
        data = self.tmp / f"data_{secrets.token_hex(4)}"
        port = _free_port()
        env = {**os.environ, "DANA_APP_DIR": str(HERE / "php" / "app"), "DANA_CASES_DIR": str(self.cases_dir),
               "DANA_DATA_DIR": str(data), "DANA_SETTINGS": str(self.settings),
               "DANA_SCHEMA": str(HERE / "schema.sql"), "DANA_COOKIE_PATH": "/",
               "DANA_EMAIL_TEMPLATE": str(HERE / "email_code.txt"), "DANA_MAIL_OUTBOX": str(data / "outbox")}  # fmt: skip
        proc = subprocess.Popen([self.exe, "-S", f"127.0.0.1:{port}", str(HERE / "php" / "dev_router.php")],
                                cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # fmt: skip
        base = f"http://127.0.0.1:{port}/"
        _wait_http(base, proc)

        def stop():
            proc.terminate()
            proc.wait(timeout=10)

        return base, str(data / "responses.sqlite"), str(data / "outbox"), stop

    def read_outbox(self, outbox):
        files = sorted(pathlib.Path(outbox).glob("*.eml")) if pathlib.Path(outbox).exists() else []
        return [f.read_bytes() for f in files]


class RemoteRunner:
    remote = True

    def __init__(self, host, key, app):
        self.host, self.key, self.app = host, os.path.expanduser(key), app.rstrip("/")
        self.site = os.path.dirname(self.app)
        self.cases_dir = f"{self.app}/cases"
        self.settings = f"{self.app}/settings.json"
        self.public = os.environ.get("PHP_REMOTE_PUBLIC", f"{self.site}/web/dana")
        # reuse one SSH connection: the host limits rapid new connections
        mux = os.path.expanduser("~/.ssh/cm-dana-%r@%h:%p")
        self.ssh = ["ssh", "-o", "BatchMode=yes", "-i", self.key, "-o", "ControlMaster=auto",
                    "-o", f"ControlPath={mux}", "-o", "ControlPersist=120"]  # fmt: skip

    def run(self, cmd, stdin=None, check=True):
        out = subprocess.run(
            [*self.ssh, self.host, cmd], input=stdin, capture_output=True, text=True, timeout=600
        )
        if check:
            assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
        return out.stdout

    def devtool(self, job, env=None):
        exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in (env or {}).items())
        return json.loads(
            self.run(f"cd {self.app}/dev && env {exports} php devtool.php", json.dumps(job))
        )

    def start_server(self):
        data = f"{self.site}/dana_test_{secrets.token_hex(6)}"
        rport, lport = random.randint(30000, 60000), _free_port()
        env = {"DANA_APP_DIR": self.app, "DANA_CASES_DIR": self.cases_dir, "DANA_DATA_DIR": data,
               "DANA_SETTINGS": self.settings, "DANA_SCHEMA": f"{self.app}/schema.sql", "DANA_COOKIE_PATH": "/",
               "DANA_STATIC_ROOT": self.public, "DANA_FRAMES_DIR": f"{self.public}/frames",
               "DANA_MAIL_OUTBOX": f"{data}/outbox"}  # fmt: skip
        exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        cmd = (f"mkdir -p {data} && chmod 700 {data} && cd {self.app}/dev && "
               f"exec env {exports} timeout 900 php -S 127.0.0.1:{rport} dev_router.php")  # fmt: skip
        proc = subprocess.Popen([*self.ssh, "-o", "ExitOnForwardFailure=yes", "-L", f"{lport}:127.0.0.1:{rport}",
                                 self.host, cmd], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # fmt: skip
        base = f"http://127.0.0.1:{lport}/"
        _wait_http(base, proc)

        def stop():
            proc.terminate()
            proc.wait(timeout=10)
            self.run(
                f"pkill -f '[p]hp -S 127.0.0.1:{rport}'; sleep 1 ; rm -rf {shlex.quote(data)}",
                check=False,
            )

        return base, f"{data}/responses.sqlite", f"{data}/outbox", stop

    def read_outbox(self, outbox):
        import base64

        out = self.run(
            f'ls {shlex.quote(outbox)}/*.eml 2>/dev/null | sort | while read f; do base64 -w0 "$f"; echo; done'
        )
        return [base64.b64decode(line) for line in out.split()]


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    cases_dir = cases_dir_or_skip()
    if os.environ.get("PHP_SSH_HOST"):
        app = os.environ.get("PHP_REMOTE_APP", "/srv/www/meteorologia.uib.es/dana_app")
        r = RemoteRunner(
            os.environ["PHP_SSH_HOST"], os.environ.get("PHP_SSH_KEY", "~/.ssh/meteo"), app
        )
    else:
        exe = os.environ.get("PHP_BIN") or shutil.which("php")
        if not exe:
            pytest.skip("no PHP binary (set PHP_BIN or PHP_SSH_HOST)")
        r = LocalRunner(exe, cases_dir, tmp_path_factory.mktemp("php"))
    info = r.devtool({"cmd": "info"})
    if not info["zlib"]:
        pytest.skip(f"PHP at hand lacks zlib ({info}); test on the host with PHP_SSH_HOST")
    print("PHP under test:", info)
    return r


# ---------------------------------------------------------------------------
# evaluate parity
# ---------------------------------------------------------------------------
def _jobs(cases_dir, n_cases=80, seed=11):
    rng = random.Random(seed)
    ids = sorted(p.stem for p in (cases_dir / "eval").glob("*.json"))
    rng.shuffle(ids)
    jobs = []
    for cid in ids[:n_cases]:
        ev = json.loads((cases_dir / "eval" / f"{cid}.json").read_text())
        pts = [(col["zmin_lat"], col["zmin_lon"]) for col in ev["cols"]]
        pts += [
            (cy["zmin_lat"] + rng.uniform(-3, 3), cy["zmin_lon"] + rng.uniform(-3, 3))
            for cy in ev["cyclones"][:3]
        ]
        pts += [(rng.uniform(10, 75), rng.uniform(-30, 20)) for _ in range(2)]
        pts += [(rng.uniform(30, 45), rng.uniform(-15, 10)), (5.0, 0.0)]  # in box; outside grid
        clicks = [{"lat": la, "lon": lo} for la, lo in pts]
        jobs.append({"case_id": cid, "has_dana": True, "clicks": clicks})
        jobs.append({"case_id": cid, "has_dana": True, "clicks": clicks[:1] * 2})  # duplicate click
        jobs.append({"case_id": cid, "has_dana": True, "clicks": clicks[-3:-2]})
        jobs.append({"case_id": cid, "has_dana": False, "clicks": []})
    return jobs


def _close(a, b, tol):
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol


def test_evaluate_parity(runner):
    cases_dir = cases_dir_or_skip()
    jobs = _jobs(cases_dir)
    got = runner.devtool(
        {"cmd": "evaluate", "jobs": jobs}, {"DANA_CASES_DIR": str(runner.cases_dir)}
    )
    assert len(got) == len(jobs)
    n_clicks = 0
    for job, pr in zip(jobs, got):
        py = evaluate.evaluate_answer(
            evaluate.load_case(cases_dir / "eval", job["case_id"]), job["has_dana"], job["clicks"]
        )
        ctx = f"case {job['case_id']} clicks {job['clicks']}"
        for k in ("outcome", "n_systems", "n_matched", "n_algo_missed", "n_algo_extra"):
            assert pr[k] == py[k], (k, ctx)
        assert len(pr["click_details"]) == len(py["click_details"])
        for a, b in zip(pr["click_details"], py["click_details"]):
            n_clicks += 1
            for k in ("idx", "outside_box", "outside_grid", "inside_col_id", "inside_cy_id",
                      "matched_col_id", "duplicate_of_col"):  # fmt: skip
                assert a[k] == b[k], (k, ctx)
            na, nb = a["nearest_cyclone"], b["nearest_cyclone"]
            assert na.get("id") == nb.get("id"), ctx
            assert na.get("no_cyclone_object") == nb.get("no_cyclone_object"), ctx
            if "id" in nb:
                assert _close(na["dist_km"], nb["dist_km"], 0.11), ctx
                assert na["first_failed_criterion"] == nb["first_failed_criterion"]
                assert na["failed_criteria"] == nb["failed_criteria"]
            for ca, cb in zip(a["cols"], b["cols"]):
                assert (
                    ca["id"] == cb["id"] and ca["same_parent_cyclone"] == cb["same_parent_cyclone"]
                )
                for k in ("dist_mask_km", "dist_zmin_km", "dist_centroid_km"):
                    assert _close(ca[k], cb[k], 0.11), (k, ctx)
                diff = abs(ca["bearing_click_to_zmin_deg"] - cb["bearing_click_to_zmin_deg"]) % 360
                assert min(diff, 360 - diff) <= 1
        pa, pb = pr["algo_details"], py["algo_details"]
        assert [c["matched_click"] for c in pa["cols"]] == [c["matched_click"] for c in pb["cols"]]
        assert [c["id"] for c in pa["cyclones_near_clicks"]] == [
            c["id"] for c in pb["cyclones_near_clicks"]
        ]
        assert [c["record"] for c in pa["cols"]] == [c["record"] for c in pb["cols"]]
    assert n_clicks > 300


# ---------------------------------------------------------------------------
# sampler
# ---------------------------------------------------------------------------
def test_sampler_php(runner):
    env = {"DANA_SETTINGS": str(runner.settings)}
    draws = runner.devtool({"cmd": "draw", "rows": _pool(), "n_cases": 10, "repeat": 400}, env)
    for ids, n_pos in draws:
        assert len(ids) == len(set(ids)) == 10
        pos = sum(i.startswith("col_") for i in ids)
        assert 4 <= pos <= 6 and pos == n_pos
    assert {n for _, n in draws} == {4, 5, 6}

    rows4 = _pool(4)
    excl = [r["case_id"] for r in rows4 if r["category"] == "col_clear"]
    job = {"cmd": "draw", "rows": rows4, "n_cases": 10, "exclude": excl, "repeat": 50}
    for ids, _ in runner.devtool(job, env):
        assert len(ids) == 10 and not set(excl) & set(ids)

    rows = _pool(20)
    rows[0].update(n=10, n_disagree=9)  # disputed
    rows[1].update(n=10, n_disagree=0)  # settled
    draws = runner.devtool({"cmd": "draw", "rows": rows, "n_cases": 10, "repeat": 2000}, env)
    hard = sum(rows[0]["case_id"] in ids for ids, _ in draws)
    easy = sum(rows[1]["case_id"] in ids for ids, _ in draws)
    assert hard > 1.5 * easy

    rows, hi_ids = high_impact_pool()
    draws = runner.devtool({"cmd": "draw", "rows": rows, "n_cases": 10, "repeat": 2000}, env)
    check_high_impact_draws([tuple(d) for d in draws], hi_ids)


# ---------------------------------------------------------------------------
# full API flow through php -S
# ---------------------------------------------------------------------------
class HttpClient:
    def __init__(self, base, runner, db_path, eval_dir, outbox_dir):
        self.base, self.runner, self.db_path, self.eval_dir = base, runner, db_path, eval_dir
        self.outbox_dir = outbox_dir
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def _open(self, req):
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def post(self, path, payload=None):
        data = json.dumps(payload if payload is not None else {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})  # fmt: skip
        status, body = self._open(req)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, None

    def get(self, path):
        return self._open(urllib.request.Request(self.base + path))

    def get_json(self, path):
        return json.loads(self.get(path)[1])

    def outbox(self):
        return parse_outbox(self.runner.read_outbox(self.outbox_dir))

    def query(self, sql, params=()):
        return self.runner.devtool(
            {"cmd": "sql", "db": self.db_path, "sql": sql, "params": list(params)}
        )

    def execute(self, sql, params=()):
        self.query(sql, params)


def test_api_flow_php(runner):
    cases_dir = cases_dir_or_skip()
    base, db_path, outbox_dir, stop = runner.start_server()
    try:
        c = HttpClient(base, runner, db_path, cases_dir / "eval", outbox_dir)
        status, page = c.get("")
        assert status == 200 and b"DANA Expert Check" in page
        assert c.get("static/app.js")[0] == 200
        api_flow(c)
        assert c.get("api/nope")[0] == 404
        # rescore on this PHP/SQLite: keeps the original score, re-scores for the deployed version
        if runner.remote:
            data = str(pathlib.PurePosixPath(db_path).parent)
            n_resp = c.query("SELECT COUNT(*) AS n FROM responses")[0]["n"]
            out = runner.run(f"cd {runner.app} && DANA_DATA_DIR={data} php admin.php rescore")
            assert "no outcome changed" in out, out  # same cases: nothing may change
            scored = c.query("SELECT COUNT(DISTINCT response_id) AS n FROM response_scores")[0]["n"]
            assert scored == n_resp > 0, (scored, n_resp)  # every answer keeps a score row
            out = runner.run(
                f"cd {runner.app} && DANA_DATA_DIR={data} php admin.php rescore --force"
            )
            assert f"Re-scored {n_resp} of {n_resp}" in out and "no outcome changed" in out, out
        # the database created by PHP must stay readable by the Python analysis tools
        tables = {
            r["name"]
            for r in c.query("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        assert {"responses", "reviews", "case_agreement", "click_diagnostics"} <= tables
    finally:
        stop()


# ---------------------------------------------------------------------------
# SMTP transport (local PHP only: the fake SMTP server runs on this machine)
# ---------------------------------------------------------------------------
def test_smtp_transport_php(tmp_path):
    exe = os.environ.get("PHP_BIN") or shutil.which("php")
    if not exe:
        pytest.skip("no local PHP binary (set PHP_BIN)")
    fake = FakeSMTP()
    proc = None
    try:
        s, cred = smtp_settings(tmp_path, fake)
        settings_json = tmp_path / "settings.json"
        settings_json.write_text(
            json.dumps({k: s[k] for k in ("session", "sampling", "review", "email")})
        )
        data = tmp_path / "data"
        port = _free_port()
        env = {**os.environ, "DANA_APP_DIR": str(HERE / "php" / "app"), "DANA_DATA_DIR": str(data),
               "DANA_SETTINGS": str(settings_json), "DANA_SCHEMA": str(HERE / "schema.sql"),
               "DANA_EMAIL_TEMPLATE": str(HERE / "email_code.txt"), "DANA_SMTP_CREDENTIALS": str(cred),
               "DANA_COOKIE_PATH": "/"}  # fmt: skip
        env.pop("DANA_MAIL_OUTBOX", None)
        proc = subprocess.Popen([exe, "-S", f"127.0.0.1:{port}", str(HERE / "php" / "dev_router.php")],
                                cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # fmt: skip
        base = f"http://127.0.0.1:{port}/"
        _wait_http(base, proc)
        (tmp_path / "runner").mkdir()
        runner = LocalRunner(
            exe, cases_dir_or_skip(), tmp_path / "runner"
        )  # own dir: writes settings.json
        c = HttpClient(base, runner, str(data / "responses.sqlite"), None, None)
        check_smtp_delivery(fake, c.post, c.query)
    finally:
        fake.close()
        if proc:
            proc.terminate()
            proc.wait(timeout=10)
