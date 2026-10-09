"""
constants.py — Physical constants used throughout AtmoTrack.

Access via:
    from constants import const
    const.g          # 9.81 m s⁻²
    const.earth_radius  # 6 371 000 m
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class _Constants:
    Rd: float = 287.04  # gas constant for dry air [J kg⁻¹ K⁻¹]
    Rv: float = 461.5  # gas constant for water vapour [J kg⁻¹ K⁻¹]
    cp: float = 7.0 * 287.04 / 2.0  # specific heat at constant pressure [J kg⁻¹ K⁻¹]
    cv: float = 718.0  # specific heat at constant volume [J kg⁻¹ K⁻¹]
    epsilon_gamma: float = 0.62197
    es_base_bolton: float = 611.2  # saturation vapour pressure base [Pa]
    es_Abolton: float = 17.67
    es_Bbolton: float = 243.5  # [°C]
    es_base_tetens: float = 6.1078
    es_Atetens_vapor: float = 7.5
    es_Btetens_vapor: float = 237.3
    es_Atetens_ice: float = 9.5
    es_Btetens_ice: float = 265.5
    g: float = 9.81  # gravitational acceleration [m s⁻²]
    p1000mb: float = 100000.0  # reference pressure [Pa]
    pconst: float = 10000.0
    gamma: float = 0.0065  # environmental lapse rate [K m⁻¹]
    rcp: float = 287.04 / (7.0 * 287.04 / 2.0)  # Rd / cp
    tkelvin: float = 273.15  # 0 °C in Kelvin
    missingval: float = 1.0e20
    kappa: float = (7.0 * 287.04 / 2.0 - 718.0) / (7.0 * 287.04 / 2.0)
    L: float = 2.501e6  # latent heat of vaporisation [J kg⁻¹]
    a: float = 2.0 / 7.0
    b: float = 0.62197 * 2.501e6**2 / (287.04 * 7.0 * 287.04 / 2.0)
    c: float = (2.0 / 7.0) * 2.501e6 / 287.04
    secinday: int = 86400  # seconds per day
    earth_radius: int = 6371000  # Earth radius [m]
    SB_sigma: float = 5.6704e-8  # Stefan–Boltzmann constant [W m⁻² K⁻⁴]


#: Singleton instance — use ``const.g``, ``const.earth_radius``, etc.
const = _Constants()
