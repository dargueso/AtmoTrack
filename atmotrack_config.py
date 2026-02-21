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

try:
    import tomllib  # Python 3.11+
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

# Keep data_era5 and data_input in sync so that code using either name works.
# Users who set only data_era5 (ERA5 default) get data_input for free, and
# users who set only data_input (non-ERA5 sources) still satisfy download_ERA5.py.
_g = globals()
if "data_input" not in _g and "data_era5" in _g:
    _g["data_input"] = _g["data_era5"]
elif "data_era5" not in _g and "data_input" in _g:
    _g["data_era5"] = _g["data_input"]
del _g

del _f, _cfg, _config_path, _section_values, _key, _val

__version__ = "1.0.0"
