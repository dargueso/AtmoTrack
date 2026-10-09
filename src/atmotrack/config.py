"""
atmotrack.config — Configuration loader for AtmoTrack.

Reads a ``config.toml`` file and exposes every parameter as a module-level
attribute, so code can do::

    from atmotrack import config as cfg
    cfg.DT

The file is looked up, in order:

1. the path in the ``ATMOTRACK_CONFIG`` environment variable;
2. ``config.toml`` in the current working directory;
3. the default ``config.toml`` shipped inside the package.

Call :func:`load` with an explicit path to switch configuration at runtime
(the module attributes are replaced in place, so modules that imported this
module see the new values).  ``atmotrack-config`` prints the file in use and
``atmotrack-config --init`` copies the packaged default into the current
directory as a starting point.

Python ≥ 3.11 ships with ``tomllib`` in the standard library.
"""

from __future__ import annotations

import os
import pathlib
from importlib import resources

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError(
            "tomllib (Python ≥ 3.11) or the 'tomli' package is required.\n"
            "Install it with:  pip install tomli"
        ) from exc

ENV_VAR = "ATMOTRACK_CONFIG"
FILENAME = "config.toml"

#: Path of the configuration file currently loaded (set by :func:`load`).
config_path: pathlib.Path

_loaded_keys: set[str] = set()


def default_path() -> pathlib.Path:
    """Path of the ``config.toml`` shipped with the package."""
    return pathlib.Path(str(resources.files("atmotrack") / FILENAME))


def resolve_path(path: str | os.PathLike | None = None) -> pathlib.Path:
    """Return the configuration file to use (see module docstring for the order)."""
    if path is not None:
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"AtmoTrack config file not found: {p}")
        return p

    env = os.environ.get(ENV_VAR)
    if env:
        p = pathlib.Path(env).expanduser()
        if not p.is_file():
            raise FileNotFoundError(
                f"{ENV_VAR} points to a file that does not exist: {p}\n"
                f"Unset {ENV_VAR} or point it at a valid config.toml."
            )
        return p

    cwd = pathlib.Path.cwd() / FILENAME
    if cwd.is_file():
        return cwd

    return default_path()


def read(path: str | os.PathLike) -> dict:
    """Parse *path* and return the raw TOML sections as a dict."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def load(path: str | os.PathLike | None = None) -> pathlib.Path:
    """(Re)load the configuration from *path* (or the resolved default).

    Every TOML section is flattened into module-level attributes so that the
    ``cfg.DT``, ``cfg.col_min_dur``, … access pattern works.  Keys are assumed
    to be unique across sections; the order in the file determines which value
    wins if a name ever appears in two sections.  Attributes from a previous
    load that are absent from the new file are removed.

    Returns the path that was loaded.
    """
    global config_path, _loaded_keys

    p = resolve_path(path)
    sections = read(p)

    g = globals()
    for key in _loaded_keys:
        g.pop(key, None)

    new_keys: set[str] = set()
    for section_values in sections.values():
        for key, val in section_values.items():
            g[key] = val
            new_keys.add(key)

    # Keep data_era5 and data_input in sync so that code using either name
    # works: users who set only data_era5 (ERA5 default) get data_input for
    # free, and users who set only data_input (non-ERA5 sources) still satisfy
    # the ERA5 downloader.
    if "data_input" not in new_keys and "data_era5" in new_keys:
        g["data_input"] = g["data_era5"]
        new_keys.add("data_input")
    elif "data_era5" not in new_keys and "data_input" in new_keys:
        g["data_era5"] = g["data_input"]
        new_keys.add("data_era5")

    _loaded_keys = new_keys
    config_path = p
    return p


def get(key: str, default=None, *, renamed_from: str | None = None):
    """Return configuration key *key*.

    When the key is missing but the old name *renamed_from* is present, raise a
    ``KeyError`` that tells the user how to update the file.  When neither is
    present, return *default* (or raise when *default* is None).
    """
    g = globals()
    if key in g and key in _loaded_keys:
        return g[key]
    if renamed_from and renamed_from in _loaded_keys:
        raise KeyError(
            f"config key '{renamed_from}' was renamed to '{key}' (now in hours). "
            f"Update {config_path}."
        )
    if default is None:
        raise KeyError(f"config key '{key}' missing from {config_path}")
    return default


load()
