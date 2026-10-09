"""AtmoTrack — atmospheric system tracking from CF-compliant NetCDF data.

Tracks Cut-Off Lows, upper-level and surface cyclones/anticyclones, tropical
cyclones, jet streams, atmospheric rivers, mesoscale convective systems and
fronts.  Configuration is read from ``config.toml`` (see :mod:`atmotrack.config`).

Typical use::

    from atmotrack import config as cfg
    from atmotrack.tracking import COL_tracking, CY_ACY_z500_tracking
"""

from importlib.metadata import PackageNotFoundError, version

from atmotrack.tracking import (
    AR_850hPa_tracking,
    AR_IVT_tracking,
    COL_tracking,
    CY_ACY_slp_tracking,
    CY_ACY_z500_tracking,
    Front_tracking,
    MCS_tracking,
    TC_tracking,
    jetstream_tracking,
)

try:
    __version__ = version("atmotrack")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0+unknown"

__all__ = [
    "__version__",
    "AR_850hPa_tracking",
    "AR_IVT_tracking",
    "COL_tracking",
    "CY_ACY_slp_tracking",
    "CY_ACY_z500_tracking",
    "Front_tracking",
    "MCS_tracking",
    "TC_tracking",
    "jetstream_tracking",
]
