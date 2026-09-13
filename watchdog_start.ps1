# Launches vector_watchdog.py hidden in the background. Run this from
# wherever you cloned this repo - no need to edit paths, $PSScriptRoot
# resolves relative to this script's own location automatically.
$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot

Start-Process -FilePath "$Root\venv\Scripts\python.exe" `
  -ArgumentList "$Root\vector_watchdog.py" `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput "$Root\watchdog.out.log" `
  -RedirectStandardError "$Root\watchdog.err.log"
Write-Output "watchdog launched"
