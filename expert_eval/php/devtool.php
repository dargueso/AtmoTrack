<?php
/**
 * devtool.php — test helper (not deployed). Reads a JSON job on stdin, writes JSON to stdout.
 *
 *   {"cmd": "evaluate", "jobs": [{"case_id": "...", "has_dana": true, "clicks": [...]}, ...]}
 *   {"cmd": "draw", "rows": [...], "n_cases": 10, "exclude": [...], "repeat": 200}
 *
 * Needs DANA_CASES_DIR (evaluate) and DANA_SETTINGS (draw) in the environment.
 */

declare(strict_types=1);

// repo layout: php/devtool.php + php/app/; host layout: dana_app/dev/devtool.php + dana_app/
define('DANA_APP', getenv('DANA_APP_DIR') ?: (is_dir(__DIR__ . '/app') ? __DIR__ . '/app' : dirname(__DIR__)));
define('DANA_CASES', rtrim(getenv('DANA_CASES_DIR') ?: '', '/'));
require_once DANA_APP . '/evaluate.php';
require_once DANA_APP . '/sampler.php';

$job = json_decode((string) stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
ini_set('serialize_precision', '-1');

if ($job['cmd'] === 'evaluate') {
    $cache = [];
    $out = [];
    foreach ($job['jobs'] as $j) {
        $case = $cache[$j['case_id']] ??= CaseData::load((string) $j['case_id']);
        $out[] = evaluate_answer($case, (bool) $j['has_dana'], $j['clicks']);
    }
    echo json_encode($out, JSON_UNESCAPED_SLASHES);
} elseif ($job['cmd'] === 'draw') {
    $settings = json_decode((string) file_get_contents(getenv('DANA_SETTINGS')), true);
    $exclude = array_fill_keys(array_map('strval', $job['exclude'] ?? []), true);
    $out = [];
    for ($i = 0; $i < ($job['repeat'] ?? 1); $i++) {
        $out[] = draw_session($job['rows'], (int) $job['n_cases'], $exclude, $settings);
    }
    echo json_encode($out);
} elseif ($job['cmd'] === 'sql') {
    $pdo = new PDO('sqlite:' . $job['db'], null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $pdo->exec('PRAGMA busy_timeout = 30000');
    $st = $pdo->prepare($job['sql']);
    $st->execute($job['params'] ?? []);
    echo json_encode($st->columnCount() ? $st->fetchAll(PDO::FETCH_ASSOC) : []);
} elseif ($job['cmd'] === 'info') {
    echo json_encode([
        'php' => PHP_VERSION,
        'sqlite' => (new PDO('sqlite::memory:'))->query('select sqlite_version()')->fetchColumn(),
        'zlib' => function_exists('gzuncompress'),
    ]);
}
