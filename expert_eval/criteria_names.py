"""criteria_names.py — COL criteria names shared by numpy and stdlib-only modules."""

# Order in which tracking/col.py applies the checks
CRITERIA_ORDER = [
    "cy_duration",
    "latitude",
    "pole",
    "object_bounds",
    "isolation",
    "eastward_flow",
    "z500_threshold",
    "region",
    "border",
    "col_duration",
]

# Quantile levels (fraction of ring points BELOW) stored for threshold sweeps:
# isolation passes iff quantile(dz, 1 - col_percent_isolation) > col_thres_isolation
DZ_QUANTILE_LEVELS = [round(0.05 * i, 2) for i in range(11)]
