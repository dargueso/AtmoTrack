<?php
/**
 * dev_router.php — local testing with PHP's built-in server (mimics the host's .htaccess).
 *
 *   DANA_APP_DIR=php/app DANA_CASES_DIR=cases DANA_DATA_DIR=/tmp/dana_data \
 *   DANA_SETTINGS=/tmp/settings.json DANA_SCHEMA=schema.sql \
 *   php -S 127.0.0.1:8080 php/dev_router.php
 *
 * Run from expert_eval/. Serves static/ at /, cases/frames at /frames, php/app/api.php at /api/*.
 */

// DANA_STATIC_ROOT: folder with index.html and static/ (default: expert_eval/static layout)
// DANA_FRAMES_DIR:  folder with the map PNGs (default: $DANA_CASES_DIR/frames)
$root = dirname(__DIR__);
$staticRoot = getenv('DANA_STATIC_ROOT') ?: null;
$framesDir = getenv('DANA_FRAMES_DIR') ?: (getenv('DANA_CASES_DIR') ?: $root . '/cases') . '/frames';
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);

if (preg_match('#^/api/(.*)$#', $path, $m)) {
    $_GET['r'] = $m[1];
    require (getenv('DANA_APP_DIR') ?: __DIR__ . '/app') . '/api.php';
    return true;
}

$map = [
    '#^/$#' => $staticRoot ? "$staticRoot/index.html" : "$root/static/index.html",
    '#^/static/(.+)$#' => $staticRoot ? "$staticRoot/static/%s" : "$root/static/%s",
    '#^/frames/(\d{10}\.png)$#' => "$framesDir/%s",
];
foreach ($map as $re => $target) {
    if (preg_match($re, $path, $m)) {
        $file = isset($m[1]) ? sprintf($target, basename($m[1])) : $target;
        if (!is_file($file)) {
            break;
        }
        $types = ['html' => 'text/html', 'js' => 'application/javascript', 'css' => 'text/css', 'png' => 'image/png'];
        header('Content-Type: ' . ($types[pathinfo($file, PATHINFO_EXTENSION)] ?? 'application/octet-stream'));
        readfile($file);
        return true;
    }
}
http_response_code(404);
echo 'Not found';
return true;
