# Launches vector_watchdog.py hidden in the background. Run this from
# wherever you cloned this repo - no need to edit paths, $PSScriptRoot
# resolves relative to this script's own location automatically.
# Repo was split into core/ life/ tools/ folders 2026-09-20 - this script
# itself now lives in scripts/, one level below the repo root.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot

Start-Process -FilePath "$Root\venv\Scripts\python.exe" `
  -ArgumentList "$Root\core\vector_watchdog.py" `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput "$Root\watchdog.out.log" `
  -RedirectStandardError "$Root\watchdog.err.log"
Write-Output "watchdog launched"
