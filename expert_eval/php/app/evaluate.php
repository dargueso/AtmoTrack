<?php
/**
 * evaluate.php — PHP port of evaluate.py (keep both in sync; test_php_backend.py checks parity).
 *
 * The expert is the reference. Outcomes:
 *   agree_hit, partial_match, location_mismatch, algo_miss, algo_false_alarm, agree_null
 */

declare(strict_types=1);

const NEAR_CYCLONE_KM = 300.0;

function decode_mask(string $b64): string
{
    return (string) gzuncompress((string) base64_decode($b64));
}

function mask_bit(string $bits, int $k): int
{
    return (ord($bits[$k >> 3]) >> (7 - ($k & 7))) & 1;
}

function mask_indices(string $bits, int $n): array
{
    $out = [];
    $len = strlen($bits);
    for ($i = 0; $i < $len; $i++) {
        $byte = ord($bits[$i]);
        if ($byte === 0) {
            continue;
        }
        for ($b = 0; $b < 8; $b++) {
            if ($byte & (0x80 >> $b)) {
                $k = $i * 8 + $b;
                if ($k < $n) {
                    $out[] = $k;
                }
            }
        }
    }
    return $out;
}

/** Python round() for the grid index (round half to even). */
function grid_index(array $grid, float $lat, float $lon): ?array
{
    $i = (int) round(($lat - $grid['lat0']) / $grid['dlat'], 0, PHP_ROUND_HALF_EVEN);
    $j = (int) round(($lon - $grid['lon0']) / $grid['dlon'], 0, PHP_ROUND_HALF_EVEN);
    if ($i >= 0 && $i < $grid['nlat'] && $j >= 0 && $j < $grid['nlon']) {
        return [$i, $j];
    }
    return null;
}

function cell_latlon(array $grid, int $k): array
{
    $i = intdiv($k, $grid['nlon']);
    $j = $k % $grid['nlon'];
    return [$grid['lat0'] + $i * $grid['dlat'], $grid['lon0'] + $j * $grid['dlon']];
}

function haversine_km(float $lat1, float $lon1, float $lat2, float $lon2): float
{
    $p1 = deg2rad($lat1);
    $p2 = deg2rad($lat2);
    $dp = $p2 - $p1;
    $dl = deg2rad($lon2 - $lon1);
    $a = sin($dp / 2) ** 2 + cos($p1) * cos($p2) * sin($dl / 2) ** 2;
    return 2 * 6371.0 * asin(min(1.0, sqrt($a)));
}

function bearing_deg(float $lat1, float $lon1, float $lat2, float $lon2): float
{
    $p1 = deg2rad($lat1);
    $p2 = deg2rad($lat2);
    $dl = deg2rad($lon2 - $lon1);
    $x = sin($dl) * cos($p2);
    $y = cos($p1) * sin($p2) - sin($p1) * cos($p2) * cos($dl);
    return fmod(rad2deg(atan2($x, $y)) + 360.0, 360.0);
}

function in_box(array $box, float $lat, float $lon): bool
{
    return $box[0] <= $lon && $lon <= $box[1] && $box[2] <= $lat && $lat <= $box[3];
}

function rnd(float $x, int $nd): ?float
{
    return is_finite($x) ? round($x, $nd) : null;
}

/** Decoded private evaluation data of one case (cases/eval/<case_id>.json). */
final class CaseData
{
    public array $raw;
    public array $grid;
    public array $box;
    public int $n;
    public array $cols;
    public array $cyclones;
    private array $bitsCache = [];
    private array $cellsCache = [];

    public function __construct(array $d)
    {
        $this->raw = $d;
        $this->grid = $d['grid'];
        $this->box = $d['box'];
        $this->n = $this->grid['nlat'] * $this->grid['nlon'];
        $this->cols = $d['cols'];
        $this->cyclones = $d['cyclones'];
    }

    public static function load(string $caseId): self
    {
        $p = DANA_CASES . "/eval/$caseId.json";
        return new self(json_decode((string) file_get_contents($p), true, 512, JSON_THROW_ON_ERROR));
    }

    public function bits(string $kind, array $obj): string
    {
        $key = $kind . ':' . $obj['id'];
        return $this->bitsCache[$key] ??= decode_mask($obj['mask']);
    }

    public function cells(string $kind, array $obj): array
    {
        $key = $kind . ':' . $obj['id'];
        return $this->cellsCache[$key] ??= mask_indices($this->bits($kind, $obj), $this->n);
    }

    public function contains(string $kind, array $obj, int $k): bool
    {
        return (bool) mask_bit($this->bits($kind, $obj), $k);
    }

    public function nearestCellKm(string $kind, array $obj, float $lat, float $lon): float
    {
        $best = INF;
        foreach ($this->cells($kind, $obj) as $k) {
            [$clat, $clon] = cell_latlon($this->grid, $k);
            if (abs($clat - $lat) * 111.0 > $best) {
                continue;
            }
            $best = min($best, haversine_km($lat, $lon, $clat, $clon));
        }
        return $best;
    }
}

function slim_record(?array $rec): array
{
    if (!$rec) {
        return [];
    }
    $margins = [];
    foreach ($rec['criteria'] ?? [] as $k => $v) {
        if (is_array($v) && array_key_exists('margin', $v)) {
            $margins[$k] = $v['margin'];
        }
    }
    return [
        'first_failed_criterion' => $rec['first_failed_criterion'] ?? null,
        'failed_criteria' => $rec['failed_criteria'] ?? null,
        'margins' => $margins ?: new stdClass(),
    ];
}

/**
 * Evaluate one answer. $clicks: list of ['lat' => float, 'lon' => float].
 * Returns outcome, counts, click_details, algo_details (same structure as evaluate.py).
 */
function evaluate_answer(CaseData $case, bool $hasDana, array $clicks): array
{
    $clicks = $hasDana ? $clicks : [];
    $grid = $case->grid;
    $cyById = [];
    foreach ($case->cyclones as $c) {
        $cyById[$c['id']] = $c;
    }

    $details = [];
    foreach (array_values($clicks) as $idx => $c) {
        $lat = (float) $c['lat'];
        $lon = (float) $c['lon'];
        $ij = grid_index($grid, $lat, $lon);
        $k = $ij ? $ij[0] * $grid['nlon'] + $ij[1] : null;
        $det = [
            'idx' => $idx,
            'lat' => round($lat, 3),
            'lon' => round($lon, 3),
            'outside_box' => !in_box($case->box, $lat, $lon),
            'outside_grid' => $ij === null,
            'inside_col_id' => null,
            'inside_cy_id' => null,
            'matched_col_id' => null,
            'duplicate_of_col' => null,
            'cols' => [],
            'nearest_cyclone' => null,
        ];
        if ($k !== null) {
            foreach ($case->cols as $col) {
                if ($det['inside_col_id'] === null && $case->contains('col', $col, $k)) {
                    $det['inside_col_id'] = $col['id'];
                }
            }
            foreach ($case->cyclones as $cy) {
                if ($det['inside_cy_id'] === null && $case->contains('cy', $cy, $k)) {
                    $det['inside_cy_id'] = $cy['id'];
                }
            }
        }
        foreach ($case->cols as $col) {
            $det['cols'][] = [
                'id' => $col['id'],
                'dist_mask_km' => rnd($case->nearestCellKm('col', $col, $lat, $lon), 1),
                'dist_zmin_km' => round(haversine_km($lat, $lon, $col['zmin_lat'], $col['zmin_lon']), 1),
                'dist_centroid_km' => round(haversine_km($lat, $lon, $col['centroid_lat'], $col['centroid_lon']), 1),
                'bearing_click_to_zmin_deg' => round(bearing_deg($lat, $lon, $col['zmin_lat'], $col['zmin_lon']), 0),
                'same_parent_cyclone' => $det['inside_cy_id'] === $col['id'],
            ];
        }
        $best = null;
        foreach ($case->cyclones as $cy) {
            $dkm = $det['inside_cy_id'] === $cy['id'] ? 0.0 : $case->nearestCellKm('cy', $cy, $lat, $lon);
            if ($dkm <= NEAR_CYCLONE_KM && ($best === null || $dkm < $best[0])) {
                $best = [$dkm, $cy];
            }
        }
        if ($best !== null) {
            [$dkm, $cy] = $best;
            $det['nearest_cyclone'] = [
                'id' => $cy['id'],
                'dist_km' => round($dkm, 1),
                'is_col' => $cy['is_col'],
            ] + slim_record($cy['record'] ?? null);
        } else {
            $det['nearest_cyclone'] = ['no_cyclone_object' => true];
        }
        $details[] = $det;
    }

    // greedy one-to-one matching: each algorithm object at most once
    $matched = [];
    foreach ($details as &$det) {
        $cid = $det['inside_col_id'];
        if ($cid === null) {
            continue;
        }
        if (array_key_exists($cid, $matched)) {
            $det['duplicate_of_col'] = $cid;
        } else {
            $matched[$cid] = $det['idx'];
            $det['matched_col_id'] = $cid;
        }
    }
    unset($det);

    $nSystems = count(array_filter($details, fn($d) => $d['duplicate_of_col'] === null));
    $nMatched = count($matched);
    $nAlgoMissed = $nSystems - $nMatched;
    $nAlgoExtra = count($case->cols) - $nMatched;
    $algoHas = count($case->cols) > 0;

    if (!$hasDana) {
        $outcome = $algoHas ? 'algo_false_alarm' : 'agree_null';
    } elseif (!$algoHas) {
        $outcome = 'algo_miss';
    } elseif ($nSystems === 0) {
        $outcome = 'agree_hit';
    } elseif ($nMatched === 0) {
        $outcome = 'location_mismatch';
    } elseif ($nAlgoMissed === 0 && $nAlgoExtra === 0) {
        $outcome = 'agree_hit';
    } else {
        $outcome = 'partial_match';
    }

    $algoCols = [];
    foreach ($case->cols as $col) {
        $algoCols[] = [
            'id' => $col['id'],
            'matched_click' => $matched[$col['id']] ?? null,
            'zmin_lat' => $col['zmin_lat'],
            'zmin_lon' => $col['zmin_lon'],
            'centroid_lat' => $col['centroid_lat'],
            'centroid_lon' => $col['centroid_lon'],
            'area_km2' => $col['area_km2'] ?? null,
            'life_hours' => $col['life_hours'] ?? null,
            'hours_since_onset' => $col['hours_since_onset'] ?? null,
            'hours_to_decay' => $col['hours_to_decay'] ?? null,
            'record' => $cyById[$col['id']]['record'] ?? null,
        ];
    }
    $nearIds = [];
    foreach ($details as $d) {
        if (isset($d['nearest_cyclone']['id'])) {
            $nearIds[$d['nearest_cyclone']['id']] = true;
        }
    }
    $near = [];
    foreach ($case->cyclones as $cy) {
        if (isset($nearIds[$cy['id']])) {
            $near[] = [
                'id' => $cy['id'],
                'is_col' => $cy['is_col'],
                'zmin_lat' => $cy['zmin_lat'],
                'zmin_lon' => $cy['zmin_lon'],
                'col_life' => $cy['col_life'] ?? null,
                'record' => $cy['record'] ?? null,
            ];
        }
    }
    return [
        'outcome' => $outcome,
        'n_systems' => $nSystems,
        'n_matched' => $nMatched,
        'n_algo_missed' => $nAlgoMissed,
        'n_algo_extra' => $nAlgoExtra,
        'click_details' => $details,
        'algo_details' => [
            'algo_n_cols' => count($case->cols),
            'cols' => $algoCols,
            'cyclones_near_clicks' => $near,
        ],
    ];
}
