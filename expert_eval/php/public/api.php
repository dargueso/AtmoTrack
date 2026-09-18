<?php
// Public entry point: all logic lives outside the web root (<site>/dana_app/), next to this site's web/ folder.
require (getenv('DANA_APP_DIR') ?: dirname(__DIR__, 2) . '/dana_app') . '/api.php';
