<?php
/**
 * admin.php — command-line admin on the web host (run over SSH; see ../../host.sh).
 *
 *   php admin.php init                          create the database and register the cases
 *   php admin.php add-codes --n 5 --label AEMET create invite codes
 *   php admin.php list-codes
 *   php admin.php stats                         quick counts
 *   php admin.php snapshot <file>               consistent copy of the database (for analysis)
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

require_once __DIR__ . '/bootstrap.php';

const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

function opt(array $argv, string $name, $default = null)
{
    $i = array_search($name, $argv, true);
    return $i !== false && isset($argv[$i + 1]) ? $argv[$i + 1] : $default;
}

$cmd = $argv[1] ?? '';
$pdo = db();

switch ($cmd) {
    case 'init':
        secret_key();
        $mp = DANA_CASES . '/manifest.json';
        $m = json_decode((string) file_get_contents($mp), true, 512, JSON_THROW_ON_ERROR);
        $added = sync_cases_if_needed($pdo, $m, $mp, true);
        $n = (int) $pdo->query('SELECT COUNT(*) FROM cases')->fetchColumn();
        echo "Database ready: " . DANA_DATA . "/responses.sqlite ($n cases, $added new)\n";
        break;

    case 'add-codes':
        $n = max(1, (int) opt($argv, '--n', 1));
        $label = opt($argv, '--label');
        $pdo->beginTransaction();
        $out = [];
        for ($i = 1; $i <= $n; $i++) {
            $code = '';
            for ($c = 0; $c < 8; $c++) {
                $code .= ($c === 4 ? '-' : '') . ALPHABET[random_int(0, strlen(ALPHABET) - 1)];
            }
            $lab = ($label !== null && $n > 1) ? "$label-$i" : $label;
            qexec($pdo, 'INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)', [$code, $lab, now_iso()]);
            $out[] = "$code\t" . ($lab ?? '');
        }
        $pdo->commit();
        echo implode("\n", $out), "\n";
        break;

    case 'list-codes':
        $rows = qall($pdo, 'SELECT i.code, i.label, i.expert_id,
                                   (SELECT COUNT(*) FROM responses r WHERE r.expert_id = i.expert_id) AS n
                            FROM invite_codes i ORDER BY i.created_at, i.code');
        printf("%-10s %-20s %-5s %s\n", 'code', 'label', 'used', 'answers');
        foreach ($rows as $r) {
            printf("%-10s %-20s %-5s %d\n", $r['code'], $r['label'] ?? '', $r['expert_id'] ? 'yes' : 'no', $r['n']);
        }
        break;

    case 'stats':
        foreach (['experts', 'sessions', 'responses', 'reviews'] as $t) {
            printf("%-10s %d\n", $t, $pdo->query("SELECT COUNT(*) FROM $t")->fetchColumn());
        }
        foreach (qall($pdo, 'SELECT outcome, COUNT(*) AS n FROM responses GROUP BY outcome ORDER BY n DESC') as $r) {
            printf("  %-18s %d\n", $r['outcome'], $r['n']);
        }
        break;

    case 'snapshot':
        $dest = $argv[2] ?? '';
        if ($dest === '') {
            fwrite(STDERR, "usage: php admin.php snapshot <file>\n");
            exit(2);
        }
        // BEGIN IMMEDIATE blocks other writers (rollback journal), so the copied file is consistent
        $pdo->exec('BEGIN IMMEDIATE');
        $ok = copy(DANA_DATA . '/responses.sqlite', $dest);
        $pdo->exec('COMMIT');
        if (!$ok) {
            fwrite(STDERR, "copy failed\n");
            exit(1);
        }
        chmod($dest, 0600);
        echo "Snapshot written to $dest\n";
        break;

    default:
        fwrite(STDERR, "usage: php admin.php init|add-codes [--n N] [--label L]|list-codes|stats|snapshot <file>\n");
        exit(2);
}
