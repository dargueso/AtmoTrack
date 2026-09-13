<?php
/**
 * db.php — SQLite access for the PHP server.
 *
 * The web host runs SQLite 3.7.17 on an NFS disk, so:
 *   - no UPSERT (ON CONFLICT ... DO UPDATE): update first, insert if nothing changed
 *   - rollback journal (default), never WAL (WAL is unsafe on network file systems)
 *   - a generous busy timeout so concurrent answers wait instead of failing
 */

declare(strict_types=1);

const SCHEMA_VERSION = 3;  // keep equal to PRAGMA user_version at the end of schema.sql

function ensure_data_dir(): void
{
    if (!is_dir(DANA_DATA)) {
        mkdir(DANA_DATA, 0700, true);
    }
}

function db(): PDO
{
    static $pdo = null;
    if ($pdo !== null) {
        return $pdo;
    }
    ensure_data_dir();
    $pdo = new PDO('sqlite:' . DANA_DATA . '/responses.sqlite', null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_TIMEOUT => 30,
    ]);
    $pdo->exec('PRAGMA foreign_keys = ON');
    $pdo->exec('PRAGMA busy_timeout = 30000');
    // schema.sql is idempotent (CREATE ... IF NOT EXISTS) and ends with PRAGMA user_version = N
    if ((int) $pdo->query('PRAGMA user_version')->fetchColumn() < SCHEMA_VERSION) {
        $pdo->exec((string) file_get_contents(DANA_SCHEMA));
    }
    return $pdo;
}

/** Fetch one row or null. */
function q1(PDO $pdo, string $sql, array $params = []): ?array
{
    $st = $pdo->prepare($sql);
    $st->execute($params);
    $row = $st->fetch();
    return $row === false ? null : $row;
}

function qall(PDO $pdo, string $sql, array $params = []): array
{
    $st = $pdo->prepare($sql);
    $st->execute($params);
    return $st->fetchAll();
}

function qexec(PDO $pdo, string $sql, array $params = []): int
{
    $st = $pdo->prepare($sql);
    $st->execute($params);
    return $st->rowCount();
}

/**
 * Insert new cases from the manifest into the cases table.
 * Case ids are append-only and never change, so only missing ids are read.
 * A marker file avoids re-checking on every request.
 */
function sync_cases_if_needed(PDO $pdo, array $manifest, string $manifestPath, bool $force = false): int
{
    $marker = DANA_DATA . '/cases_synced';
    $stamp = filemtime($manifestPath) . ':' . filesize($manifestPath);
    if (!$force && is_file($marker) && trim((string) file_get_contents($marker)) === $stamp) {
        return 0;
    }
    $have = array_flip(array_column(qall($pdo, 'SELECT case_id FROM cases'), 'case_id'));
    $added = 0;
    $pdo->beginTransaction();
    try {
        $st = $pdo->prepare('INSERT INTO cases (case_id, time, category, tags, algo_n_cols) VALUES (?,?,?,?,?)');
        $up = $pdo->prepare('UPDATE cases SET time = ?, category = ?, tags = ?, algo_n_cols = ? WHERE case_id = ?');
        foreach (array_keys($manifest['cases']) as $cid) {
            $cid = (string) $cid;
            if (isset($have[$cid])) {
                if ($force) {  // full refresh (admin init): category/tags/detections may have changed
                    $p = DANA_CASES . "/eval/$cid.json";
                    if (is_file($p)) {
                        $e = json_decode((string) file_get_contents($p), true);
                        $up->execute([$e['time'], $e['category'], jenc($e['tags']), $e['algo_n_cols'], $cid]);
                    }
                }
                continue;
            }
            $p = DANA_CASES . "/eval/$cid.json";
            if (!is_file($p)) {
                continue;
            }
            $e = json_decode((string) file_get_contents($p), true, 512, JSON_THROW_ON_ERROR);
            $st->execute([$cid, $e['time'], $e['category'], jenc($e['tags']), $e['algo_n_cols']]);
            $added++;
        }
        $pdo->commit();
    } catch (Throwable $ex) {
        $pdo->rollBack();
        throw $ex;
    }
    file_put_contents($marker, $stamp, LOCK_EX);
    return $added;
}
