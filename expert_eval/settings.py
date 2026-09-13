"""settings.py — load expert_eval/settings.toml and resolve paths.

Environment overrides (handy for tests or a second deployment):
    EXPERT_EVAL_SETTINGS   path to an alternative settings.toml
    EXPERT_EVAL_CASES_DIR  overrides [paths] cases_dir
    EXPERT_EVAL_DB         overrides [paths] db_path
"""

import json
import os
import pathlib
import tomllib

HERE = pathlib.Path(__file__).resolve().parent
_ENV = {"cases_dir": "EXPERT_EVAL_CASES_DIR", "db_path": "EXPERT_EVAL_DB"}


def load_settings(path=None):
    path = pathlib.Path(path or os.environ.get("EXPERT_EVAL_SETTINGS") or HERE / "settings.toml")
    with open(path, "rb") as f:
        s = tomllib.load(f)
    for key, env in _ENV.items():
        if os.environ.get(env):
            s["paths"][key] = os.environ[env]
    for key, val in s["paths"].items():
        p = pathlib.Path(val)
        s["paths"][key] = p if p.is_absolute() else HERE / p
    return s


def server_settings_json(path=None):
    """Sections the PHP server needs, as JSON (written to settings.json on deploy)."""
    s = load_settings(path)
    return json.dumps({k: s[k] for k in ("session", "sampling", "review", "email")}, indent=1)


if __name__ == "__main__":  # python settings.py > settings.json
    print(server_settings_json())
