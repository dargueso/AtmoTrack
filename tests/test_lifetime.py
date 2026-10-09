"""Minimum-lifetime filtering counts time steps once (no second division by DT)."""

import numpy as np

from atmotrack.tracking.shared import BreakupObjects, clean_up_objects


def _objects():
    """Two objects: label 1 lives 1 step, label 2 lives 2 steps."""
    data = np.zeros((6, 10, 10), dtype=int)
    data[0, 2:4, 2:4] = 1
    data[2:4, 6:8, 6:8] = 2
    return data


def test_clean_up_objects_counts_steps():
    kept, _ = clean_up_objects(_objects(), dT=6, min_tsteps=2)
    labels = np.unique(kept)[1:]
    assert len(labels) == 1, "only the 2-step object must survive a 2-step minimum"
    assert np.all(kept[2:4, 6:8, 6:8] > 0)
    assert np.all(kept[0] == 0)


def test_clean_up_objects_keeps_all_with_one_step():
    kept, _ = clean_up_objects(_objects(), dT=6, min_tsteps=1)
    assert len(np.unique(kept)[1:]) == 2


def test_breakup_objects_counts_steps():
    kept = BreakupObjects(_objects(), min_tsteps=2, dT=6)
    assert len(np.unique(kept)[1:]) == 1
    assert np.all(kept[0] == 0)


def test_min_lifetime_from_config(use_config):
    """MinTimeCY=12 h at DT=6 h keeps a 2-step cyclone and drops a 1-step one."""
    from tests.conftest import LAT2D, LON2D, TIMES

    use_config(DT=6, MinTimeCY=12)
    from atmotrack.tracking import CY_ACY_z500_tracking

    z = np.full((len(TIMES),) + LON2D.shape, 53_000.0)
    # one deep low present for exactly 2 steps, another for 1 step, far apart
    for t in (10, 11):
        z[t] -= 2_500.0 * np.exp(-((LON2D + 10) ** 2 + (LAT2D - 40) ** 2) / 20.0)
    z[20] -= 2_500.0 * np.exp(-((LON2D - 10) ** 2 + (LAT2D - 60) ** 2) / 20.0)
    cy, _ = CY_ACY_z500_tracking(z, TIMES, LON2D, LAT2D, nc_file=None)
    assert cy[20].max() == 0, "the 1-step low must be removed"
    assert cy[10:12].max() > 0, "the 2-step low must survive"
