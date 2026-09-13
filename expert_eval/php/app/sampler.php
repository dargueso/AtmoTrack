<?php
/**
 * sampler.php — PHP port of sampler.py (stratified, disagreement-weighted session drawing).
 *
 *   w = (base_weight + d) * novelty
 *   d = (n_disagree + unsure_weight * n_unsure + alpha) / (n + alpha + beta)
 */

declare(strict_types=1);

function case_weight(int $n, int $nDisagree, int $nUnsure, array $sp): float
{
    $d = ($nDisagree + $sp['unsure_weight'] * $nUnsure + $sp['prior_alpha'])
        / ($n + $sp['prior_alpha'] + $sp['prior_beta']);
    $novelty = $n < $sp['novelty_min_answers'] ? $sp['novelty_bonus'] : 1.0;
    return ($sp['base_weight'] + $d) * $novelty;
}

function rand_unit(): float
{
    return mt_rand() / (mt_getrandmax() + 1);
}

function weighted_draw(array $pool, int $k, bool $diversify = false): array
{
    $pool = array_values($pool);
    $weights = array_column($pool, 'weight');
    $out = [];
    while ($pool && count($out) < $k) {
        $total = array_sum($weights);
        $r = rand_unit() * $total;
        $i = 0;
        $acc = $weights[0];
        while ($acc <= $r && $i < count($weights) - 1) {
            $i++;
            $acc += $weights[$i];
        }
        $c = $pool[$i];
        array_splice($pool, $i, 1);
        array_splice($weights, $i, 1);
        $out[] = $c;
        if ($diversify) {
            $tags = array_diff($c['tags'], ['cyclone_not_col']);
            foreach ($pool as $j => $o) {
                if (array_intersect($tags, $o['tags'])) {
                    $weights[$j] *= 0.5;
                }
            }
        }
    }
    return $out;
}

/**
 * @param array $rows    case_id, category, tags (JSON or list), algo_n_cols, n, n_disagree, n_unsure
 * @param array $exclude case_id => true for cases this expert already answered or has pending
 * @return array [list of case_ids, n_pos]
 */
function draw_session(array $rows, int $nCases, array $exclude, array $settings): array
{
    $ss = $settings['session'];
    $sp = $settings['sampling'];
    $strata = ['col_clear' => [], 'col_borderline' => [], 'nocol_clear' => [], 'nocol_borderline' => []];
    foreach ($rows as $r) {
        $cid = (string) $r['case_id'];
        if (isset($exclude[$cid]) || !array_key_exists($r['category'], $strata)) {
            continue;
        }
        $tags = is_array($r['tags']) ? $r['tags'] : (json_decode((string) $r['tags'], true) ?: []);
        $strata[$r['category']][] = [
            'case_id' => $cid,
            'tags' => $tags,
            'weight' => case_weight((int) ($r['n'] ?? 0), (int) ($r['n_disagree'] ?? 0), (int) ($r['n_unsure'] ?? 0), $sp),
        ];
    }

    $lo = (int) ceil($ss['min_pos_frac'] * $nCases - 1e-9);
    $hi = (int) floor($ss['max_pos_frac'] * $nCases + 1e-9);
    $nPos = mt_rand($lo, max($lo, $hi));

    $drawClass = function (string $prefix, int $k, float $borderFrac) use ($strata): array {
        $kBorder = (int) round($k * $borderFrac, 0, PHP_ROUND_HALF_EVEN);
        $border = weighted_draw($strata["{$prefix}_borderline"], $kBorder, true);
        $clear = weighted_draw($strata["{$prefix}_clear"], $k - count($border));
        if (count($border) + count($clear) < $k) {  // not enough clear cases: top up with borderline
            $taken = array_flip(array_column($border, 'case_id'));
            $rest = array_filter($strata["{$prefix}_borderline"], fn($c) => !isset($taken[$c['case_id']]));
            $border = array_merge($border, weighted_draw($rest, $k - count($border) - count($clear), true));
        }
        return array_merge($border, $clear);
    };

    $pos = $drawClass('col', $nPos, (float) $ss['pos_borderline_frac']);
    $neg = $drawClass('nocol', $nCases - $nPos, (float) $ss['neg_borderline_frac']);
    $short = $nCases - count($pos) - count($neg);
    if ($short > 0) {  // one class ran out: fill from the other
        $taken = array_flip(array_column(array_merge($pos, $neg), 'case_id'));
        $other = count($pos) < $nPos ? 'nocol' : 'col';
        $rest = array_filter(
            array_merge($strata["{$other}_borderline"], $strata["{$other}_clear"]),
            fn($c) => !isset($taken[$c['case_id']])
        );
        $extra = weighted_draw($rest, $short);
        if ($other === 'nocol') {
            $neg = array_merge($neg, $extra);
        } else {
            $pos = array_merge($pos, $extra);
        }
    }
    $chosen = array_merge($pos, $neg);
    shuffle($chosen);
    return [array_column($chosen, 'case_id'), count($pos)];
}
