# Pulls Vector's staged vault writes (diary, dashboard, snapshots, logbook,
# reflections, requests) from the Pi's local staging copy into the real
# Obsidian vault. The Pi can't write directly to this Windows filesystem,
# so core/vector_mcp_server.py / life/self_improve.py / life/vector_life.py
# write to ~/vector-mcp/vault_sync/Vector Mind on the Pi instead, and this script
# reconciles that into the real vault on a schedule (via tar piped over ssh,
# so directory contents merge in cleanly instead of nesting).
#
# Fill in your own values below before running - these are intentionally
# left blank, not real config:
#   $KeyFile -> path to the SSH private key you use to reach your Pi
#   $PiHost  -> "<pi-user>@<pi-ip-or-hostname>"
#   $Local   -> path to wherever you want the synced notes to land locally
#               (an Obsidian vault folder, or any plain directory)

$KeyFile = ""   # e.g. "C:\Users\you\.ssh\id_ed25519_vectorpi"
$PiHost  = ""   # e.g. "pi-user@192.168.1.x"
$Local   = ""   # e.g. "C:\Users\you\Documents\Obsidian Vault\Vector Mind"

if (-not $KeyFile -or -not $PiHost -or -not $Local) {
    Write-Error "Set `$KeyFile, `$PiHost, and `$Local at the top of this script before running it."
    exit 1
}

if (-not (Test-Path $Local)) {
    New-Item -ItemType Directory -Path $Local -Force | Out-Null
}

# Routed through cmd.exe: PowerShell's pipeline mangles raw binary (tar)
# data passed directly between two native executables, but cmd's pipes
# are byte-transparent.
$cmd = "ssh -i `"$KeyFile`" $PiHost `"mkdir -p ~/vector-mcp/vault_sync/'Vector Mind' && tar -C ~/vector-mcp/vault_sync/'Vector Mind' -cf - .`" | tar -C `"$Local`" -xf -"
cmd /c $cmd
