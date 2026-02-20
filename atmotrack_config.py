"""
atmotrack_config.py — Configuration loader for AtmoTrack.

Reads ``config.toml`` (in the same directory as this file) and exposes every
parameter as a module-level attribute, so existing code that does

    import atmotrack_config as cfg
    cfg.DT

continues to work without modification.

Python ≥ 3.11 ships with ``tomllib`` in the standard library.
For Python 3.9 / 3.10, install the back-port:  pip install tomli
"""

import pathlib
import sys

try:
    import tomllib          # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError(
            "tomllib (Python ≥ 3.11) or the 'tomli' package is required.\n"
            "Install it with:  pip install tomli"
        ) from exc

_config_path = pathlib.Path(__file__).parent / "config.toml"

with open(_config_path, "rb") as _f:
    _cfg = tomllib.load(_f)

# Flatten all TOML sections into module-level attributes so that the
# existing `cfg.DT`, `cfg.col_min_dur`, … access pattern keeps working.
# Keys are assumed to be unique across sections; the order in config.toml
# determines which value wins if a name ever appears in two sections.
for _section_values in _cfg.values():
    for _key, _val in _section_values.items():
        globals()[_key] = _val

del _f, _cfg, _config_path, _section_values, _key, _val
