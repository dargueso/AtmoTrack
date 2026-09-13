<?php
/**
 * admin.php — command-line admin on the web host (run over SSH; see ../../host.sh).
 *
 *   php admin.php init                          create the database and register the cases
 *   php admin.php add-codes --n 5 --label AEMET create invite codes
 *   php admin.php list-codes
 *   php admin.php requests                      codes requested from the sign-in page (no emails are stored)
 *   php admin.php test-email <address>          send a test email with the configured transport
 *   php admin.php flow-key                      secret the Power Automate flow checks in request emails
 *   php admin.php stats                         quick counts
 *   php admin.php snapshot <file>               consistent copy of the database (for analysis)
 *   php admin.php rescore [--force]             re-score all answers against the deployed algorithm version
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

require_once __DIR__ . '/bootstrap.php';

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
            $code = new_invite_code($pdo);
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

    case 'requests':
        $rows = qall($pdo, 'SELECT q.created_at, q.last_sent_at, q.n_sent, q.code, q.name, q.affiliation, q.experience,
                                   i.expert_id,
                                   (SELECT COUNT(*) FROM responses r WHERE r.expert_id = i.expert_id) AS n
                            FROM code_requests q JOIN invite_codes i ON i.code = q.code ORDER BY q.created_at');
        printf("%-16s %-10s %-24s %-28s %-24s %5s %4s %s\n", 'requested (UTC)', 'code', 'name', 'institution', 'experience', 'sent', 'used', 'answers');
        foreach ($rows as $r) {
            printf("%-16s %-10s %-24s %-28s %-24s %5d %4s %d\n", substr($r['created_at'], 0, 16), $r['code'],
                mb_strimwidth($r['name'], 0, 24), mb_strimwidth((string) $r['affiliation'], 0, 28),
                $r['experience'], $r['n_sent'], $r['expert_id'] ? 'yes' : 'no', $r['n']);
        }
        break;

    case 'flow-key':
        echo flow_key(), "\n";
        break;

    case 'test-email':
        $to = $argv[2] ?? '';
        if (!filter_var($to, FILTER_VALIDATE_EMAIL)) {
            fwrite(STDERR, "usage: php admin.php test-email <address>\n");
            exit(2);
        }
        $cfg = settings()['email'];
        try {
            send_mail($to, 'DANA Expert Check: test email',
                "This is a test from the DANA Expert Check site.\n\nTransport: " . ($cfg['transport'] ?? 'mail')
                . "\nSender: " . sender_address($cfg) . "\nSent: " . now_iso() . "\n");
        } catch (Throwable $e) {
            fwrite(STDERR, 'FAILED: ' . $e->getMessage() . "\n");
            exit(1);
        }
        echo "Test email handed over via " . ($cfg['transport'] ?? 'mail') . " from " . sender_address($cfg) . "\n";
        break;

    case 'stats':
        foreach (['code_requests', 'experts', 'sessions', 'responses', 'reviews'] as $t) {
            printf("%-10s %d\n", $t, $pdo->query("SELECT COUNT(*) FROM $t")->fetchColumn());
        }
        foreach (qall($pdo, 'SELECT outcome, COUNT(*) AS n FROM responses GROUP BY outcome ORDER BY n DESC') as $r) {
            printf("  %-18s %d\n", $r['outcome'], $r['n']);
        }
        break;

    case 'rescore':
        // Keeps every score in response_scores (the original one under the version it was made with)
        // and puts the score for the deployed version in responses (used by the site and sampler).
        $m = json_decode((string) file_get_contents(DANA_CASES . '/manifest.json'), true, 512, JSON_THROW_ON_ERROR);
        $ver = $m['algo_version'] ?? 'unversioned';
        $force = in_array('--force', $argv, true);
        $have = [];
        foreach (qall($pdo, 'SELECT response_id, algo_version FROM response_scores') as $x) {
            $have[(int) $x['response_id']][$x['algo_version']] = true;
        }
        $rows = qall($pdo, 'SELECT r.*, s.algo_version AS session_version FROM responses r
                            JOIN sessions s ON s.id = r.session_id ORDER BY r.case_id, r.id');
        $ins = $pdo->prepare('INSERT OR REPLACE INTO response_scores (response_id, algo_version, outcome, n_systems,
                                  n_matched, n_algo_missed, n_algo_extra, click_details, algo_details, scored_at)
                              VALUES (?,?,?,?,?,?,?,?,?,?)');
        $upd = $pdo->prepare('UPDATE responses SET outcome = ?, n_systems = ?, n_matched = ?, n_algo_missed = ?,
                                  n_algo_extra = ?, click_details = ?, algo_details = ? WHERE id = ?');
        $done = 0;
        $changes = [];
        $case = null;
        $pdo->beginTransaction();
        foreach ($rows as $r) {
            $rid = (int) $r['id'];
            if (empty($have[$rid])) {
                $orig = json_decode((string) $r['session_version'], true)['version'] ?? 'initial';
                $ins->execute([$rid, $orig, $r['outcome'], $r['n_systems'], $r['n_matched'], $r['n_algo_missed'],
                    $r['n_algo_extra'], $r['click_details'], $r['algo_details'], $r['created_at']]);
                $have[$rid][$orig] = true;
            }
            if (isset($have[$rid][$ver]) && !$force) {
                continue;
            }
            if ($case === null || $case->raw['case_id'] !== (string) $r['case_id']) {
                $case = CaseData::load((string) $r['case_id']);
            }
            $res = evaluate_answer($case, (bool) $r['has_dana'], json_decode($r['clicks'], true) ?: []);
            $cd = jenc($res['click_details']);
            $ad = jenc($res['algo_details']);
            $ins->execute([$rid, $ver, $res['outcome'], $res['n_systems'], $res['n_matched'], $res['n_algo_missed'],
                $res['n_algo_extra'], $cd, $ad, now_iso()]);
            $upd->execute([$res['outcome'], $res['n_systems'], $res['n_matched'], $res['n_algo_missed'],
                $res['n_algo_extra'], $cd, $ad, $rid]);
            if ($res['outcome'] !== $r['outcome']) {
                $k = $r['outcome'] . ' -> ' . $res['outcome'];
                $changes[$k] = ($changes[$k] ?? 0) + 1;
            }
            $done++;
        }
        $pdo->commit();
        echo "Re-scored $done of " . count($rows) . " answers for algorithm version $ver\n";
        ksort($changes);
        foreach ($changes as $k => $v) {
            printf("  %-40s %d\n", $k, $v);
        }
        if (!$changes) {
            echo "  no outcome changed\n";
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
        fwrite(STDERR, "usage: php admin.php init|add-codes [--n N] [--label L]|list-codes|requests|test-email <address>|flow-key|stats|snapshot <file>|rescore [--force]\n");
        exit(2);
}
