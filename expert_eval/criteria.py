"""
criteria.py — Per-timestep re-evaluation of the COL criteria in tracking/col.py.

``col_step_criteria`` mirrors, check by check, the loop body of
``tracking.col.COL_tracking`` (index windows included, so ring truncation and
the poleward u200 column are identical) but evaluates *every* criterion instead
of stopping at the first failure, and returns values, thresholds and margins.
The result feeds the expert-evaluation database so disagreements can be traced
to specific criteria and thresholds can be swept afterwards.

Only numpy/scipy are needed; the physical constants and thresholds are passed
in through a ``ColParams`` instance so this module is usable without the repo.
"""

from dataclasses import asdict, dataclass

import numpy as np
from criteria_names import CRITERIA_ORDER, DZ_QUANTILE_LEVELS
from scipy import ndimage

EARTH_RADIUS_M = 6371000.0
G = 9.81


@dataclass
class ColParams:
    col_buffer: float
    col_region: list
    col_min_dur: float
    DT: float
    col_z500_threshold_min: float
    col_percent_isolation: float
    col_ring_isolation: float
    col_thres_isolation: float
    col_min_lat: float
    col_max_lat: float
    col_min_lon: float
    col_max_lon: float
    g: float = G

    @classmethod
    def from_cfg(cls, cfg, g=G):
        return cls(
            col_buffer=cfg.col_buffer,
            col_region=list(cfg.col_region),
            col_min_dur=cfg.col_min_dur,
            DT=cfg.DT,
            col_z500_threshold_min=cfg.col_z500_threshold_min,
            col_percent_isolation=cfg.col_percent_isolation,
            col_ring_isolation=cfg.col_ring_isolation,
            col_thres_isolation=cfg.col_thres_isolation,
            col_min_lat=cfg.col_min_lat,
            col_max_lat=cfg.col_max_lat,
            col_min_lon=cfg.col_min_lon,
            col_max_lon=cfg.col_max_lon,
            g=g,
        )

    def asdict(self):
        return asdict(self)

    @property
    def min_steps(self):
        return self.col_min_dur / self.DT


def haversine(lat1, lon1, lat2, lon2, radius=EARTH_RADIUS_M):
    """Great-circle distance [m] (same formula as tracking/shared.py)."""
    lon1, lon2, lat1, lat2 = map(np.radians, (lon1, lon2, lat1, lat2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * radius * np.arcsin(np.sqrt(a))


def grid_spacing(lat2d, lon2d):
    """Mean grid distance [m] as in tracking.shared.calc_grid_distance_area."""
    dy = np.zeros(lat2d.shape)
    dx = np.zeros(lon2d.shape)
    dx[:, 1:] = haversine(lat2d[:, 1:], lon2d[:, 1:], lat2d[:, :-1], lon2d[:, :-1])
    dy[1:, :] = haversine(lat2d[1:, :], lon2d[1:, :], lat2d[:-1, :], lon2d[:-1, :])
    dx[:, 0] = dx[:, 1]
    dy[0, :] = dy[1, :]
    return dx * dy, float(np.mean(np.append(dy[:, :, None], dx[:, :, None], axis=2)))


def _crit(passed, value=None, threshold=None, margin=None, **extra):
    d = {"pass": bool(passed)}
    if value is not None:
        d["value"] = _r(value)
    if threshold is not None:
        d["threshold"] = _r(threshold)
    if margin is not None:
        d["margin"] = _r(margin)
    for k, v in extra.items():
        d[k] = _r(v)
    return d


def _r(v, nd=3):
    if isinstance(v, (list, tuple)):
        return [_r(x, nd) for x in v]
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        v = float(v)
        return None if not np.isfinite(v) else round(v, nd)
    return v


def isolation_metrics(z_reg, lat_reg, lon_reg, ila, ilo, min_z, p, gs):
    """Ring-isolation test around (ila, ilo) inside the regional window."""
    rdist = haversine(lat_reg[ila, ilo], lon_reg[ila, ilo], lat_reg, lon_reg)
    ring = (rdist >= p.col_ring_isolation - gs * 2) & (rdist <= p.col_ring_isolation + gs * 2)
    dz = (z_reg[ring] - min_z) / p.g
    n_ring = int(ring.sum())
    n_above = int(np.sum(dz > p.col_thres_isolation))
    passed = not (n_above < n_ring * p.col_percent_isolation)
    if n_ring:
        frac = n_above / n_ring
        q = np.quantile(dz, DZ_QUANTILE_LEVELS)
        dz_at_pct = float(np.quantile(dz, 1 - p.col_percent_isolation))
    else:
        frac, q, dz_at_pct = float("nan"), [float("nan")] * len(DZ_QUANTILE_LEVELS), float("nan")
    return _crit(
        passed,
        value=frac,
        threshold=p.col_percent_isolation,
        margin=frac - p.col_percent_isolation,
        dz_threshold=p.col_thres_isolation,
        dz_at_required_fraction=dz_at_pct,
        dz_margin=dz_at_pct - p.col_thres_isolation,
        dz_median=float(np.median(dz)) if n_ring else float("nan"),
        dz_min=float(dz.min()) if n_ring else float("nan"),
        n_ring=n_ring,
        dz_quantiles=list(q),
    )


def eastward_flow_metrics(u_reg, lat_c, ila, ilo):
    """Poleward u200 column test (latitude arrays descend, index 0 = north)."""
    col = u_reg[0:ila, ilo] if lat_c > 0 else u_reg[ila:-1, ilo]
    if col.shape[0] == 0:
        return _crit(False, value=None, threshold=0.0, n_points=0, note="upper boundary")
    umin = float(np.min(col))
    return _crit(not (umin > 0), value=umin, threshold=0.0, margin=-umin, n_points=int(col.shape[0]))


def col_step_criteria(obj2d, z2d, u2d, lat2d, lon2d, crop, full_shape, cy_life_steps, p, gs, area2d):
    """Evaluate the COL step criteria for one cyclone object at one time step.

    Parameters
    ----------
    obj2d : bool array, object mask inside the bbox+buffer crop (object_slice[tt])
    z2d, u2d : geopotential and u200 inside the same crop
    lat2d, lon2d, area2d : coordinates / cell areas inside the crop
    crop : (lat_start, lat_stop, lon_start, lon_stop) of the crop in the full grid
    full_shape : (ny, nx) of the full grid
    cy_life_steps : time extent of the cyclone object's bbox
    """
    lat_start, lat_stop, lon_start, lon_stop = crop
    ny, nx = full_shape
    buf = int(p.col_buffer / gs)

    zobj = np.where(obj2d, z2d, np.nan)
    min_la, min_lo = np.unravel_index(np.nanargmin(zobj), zobj.shape)
    la0, lo0 = max(min_la - buf, 0), max(min_lo - buf, 0)
    la1, lo1 = min(min_la + buf + 1, zobj.shape[0]), min(min_lo + buf + 1, zobj.shape[1])

    lat_reg = lat2d[la0:la1, lo0:lo1]
    lon_reg = lon2d[la0:la1, lo0:lo1]
    z_reg = z2d[la0:la1, lo0:lo1]
    u_reg = u2d[la0:la1, lo0:lo1]
    obj_reg = obj2d[la0:la1, lo0:lo1]
    min_z = z2d[min_la, min_lo]
    ila, ilo = min_la - la0, min_lo - lo0
    lat_c, lon_c = float(lat_reg[ila, ilo]), float(lon_reg[ila, ilo])

    crit = {}
    crit["cy_duration"] = _crit(
        not (cy_life_steps < p.min_steps),
        value=cy_life_steps,
        threshold=p.min_steps,
        margin=cy_life_steps - p.min_steps,
    )
    crit["latitude"] = _crit(
        20 <= abs(lat_c) <= 70, value=lat_c, margin=min(abs(lat_c) - 20, 70 - abs(lat_c))
    )
    crit["pole"] = _crit(not (np.max(np.abs(lat_reg)) > 88), value=float(np.max(np.abs(lat_reg))))

    ola, olo = lat_reg[obj_reg], lon_reg[obj_reg]
    b = {
        "lat_max": float(ola.max()),
        "lat_min": float(ola.min()),
        "lon_max": float(olo.max()),
        "lon_min": float(olo.min()),
    }
    bmargin = min(
        p.col_max_lat - b["lat_max"],
        b["lat_min"] - p.col_min_lat,
        p.col_max_lon - b["lon_max"],
        b["lon_min"] - p.col_min_lon,
    )
    crit["object_bounds"] = _crit(bmargin >= 0, margin=bmargin, **b)

    crit["isolation"] = isolation_metrics(z_reg, lat_reg, lon_reg, ila, ilo, min_z, p, gs)
    crit["eastward_flow"] = eastward_flow_metrics(u_reg, lat_c, ila, ilo)

    depth_m = float(min_z / p.g)
    crit["z500_threshold"] = _crit(
        not (depth_m > p.col_z500_threshold_min),
        value=depth_m,
        threshold=p.col_z500_threshold_min,
        margin=p.col_z500_threshold_min - depth_m,
    )

    com = ndimage.center_of_mass(obj2d)
    ci, cj = int(round(com[0])), int(round(com[1]))
    com_lat, com_lon = float(lat2d[ci, cj]), float(lon2d[ci, cj])
    r = p.col_region  # lon_min, lon_max, lat_min, lat_max
    rmargin = min(com_lat - r[2], r[3] - com_lat, com_lon - r[0], r[1] - com_lon)
    crit["region"] = _crit(
        not (com_lat < r[2] or com_lat > r[3] or com_lon < r[0] or com_lon > r[1]),
        margin=rmargin,
        com_lat=com_lat,
        com_lon=com_lon,
    )

    touches = (
        (lat_start == 0 and bool(obj2d[0, :].any()))
        or (lat_stop == ny and bool(obj2d[-1, :].any()))
        or (lon_start == 0 and bool(obj2d[:, 0].any()))
        or (lon_stop == nx and bool(obj2d[:, -1].any()))
    )
    crit["border"] = _crit(not touches)

    step_pass = all(crit[k]["pass"] for k in CRITERIA_ORDER if k in crit)
    return {
        "criteria": crit,
        "step_pass": step_pass,
        "zmin_lat": lat_c,
        "zmin_lon": lon_c,
        "zmin_ij": [int(lat_start + min_la), int(lon_start + min_lo)],
        "zmin_dam": _r(depth_m / 10.0, 2),
        "com_lat": com_lat,
        "com_lon": com_lon,
        "n_cells": int(obj2d.sum()),
        "area_km2": _r(float(area2d[obj2d].sum()) / 1e6, 0),
    }


def finalize_failures(rec):
    """Add first_failed_criterion / failed_criteria to a record in col.py order."""
    failed = [k for k in CRITERIA_ORDER if k in rec["criteria"] and not rec["criteria"][k]["pass"]]
    rec["failed_criteria"] = failed
    rec["first_failed_criterion"] = failed[0] if failed else None
    return rec


def local_click_diagnostics(z2d, u2d, lat2d, lon2d, click_lat, click_lon, p, gs, search_km=300.0):
    """Criteria around the local z500 minimum nearest to an expert click.

    Used when the expert marks a centre where no tracked cyclone exists, so we
    can still see how far the field is from satisfying isolation / flow tests.
    """
    ny, nx = z2d.shape
    d = haversine(click_lat, click_lon, lat2d, lon2d)
    near = d <= search_km * 1000.0
    if not near.any():
        return None
    zn = np.where(near, z2d, np.nan)
    min_la, min_lo = np.unravel_index(np.nanargmin(zn), zn.shape)
    buf = int(p.col_buffer / gs)
    la0, lo0 = max(min_la - buf, 0), max(min_lo - buf, 0)
    la1, lo1 = min(min_la + buf + 1, ny), min(min_lo + buf + 1, nx)
    lat_reg, lon_reg = lat2d[la0:la1, lo0:lo1], lon2d[la0:la1, lo0:lo1]
    ila, ilo = min_la - la0, min_lo - lo0
    min_z = z2d[min_la, min_lo]
    lat_c, lon_c = float(lat2d[min_la, min_lo]), float(lon2d[min_la, min_lo])
    r = p.col_region
    rmargin = min(lat_c - r[2], r[3] - lat_c, lon_c - r[0], r[1] - lon_c)
    # A local minimum on the edge of the search disc is not a closed centre
    on_edge = bool(d[min_la, min_lo] > 0.8 * search_km * 1000.0)
    return {
        "local_min_lat": lat_c,
        "local_min_lon": lon_c,
        "local_min_dam": _r(float(min_z / p.g / 10.0), 2),
        "click_to_min_km": _r(float(d[min_la, min_lo]) / 1000.0, 1),
        "min_on_search_edge": on_edge,
        "isolation": isolation_metrics(
            z2d[la0:la1, lo0:lo1], lat_reg, lon_reg, ila, ilo, min_z, p, gs
        ),
        "eastward_flow": eastward_flow_metrics(u2d[la0:la1, lo0:lo1], lat_c, ila, ilo),
        "region": _crit(rmargin >= 0, margin=rmargin),
    }
