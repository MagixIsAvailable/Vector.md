# systemd unit templates

Real unit files this project's own Pi uses, with the personal paths
swapped for `<you>`/`<version>` placeholders. All three are **user-level**
services (`systemctl --user`, not `sudo systemctl`) so they don't need
root and stay tied to one user's session — paired with
`loginctl enable-linger $USER` so they still start on boot even without
an active SSH login.

## Install one

```
mkdir -p ~/.config/systemd/user
cp docs/systemd/<name>.service ~/.config/systemd/user/
# edit the copied file: replace <you> with your actual username,
# <version> with your installed node version (n8n.service only)
systemctl --user daemon-reload
systemctl --user enable --now <name>.service
loginctl enable-linger $USER
```

## Check it's actually working

```
systemctl --user status <name>.service
journalctl --user -u <name>.service -f     # live logs, Ctrl+C to stop
```

If a service shows `activating (auto-restart)` in a tight loop, it's
crashing on every start — check `journalctl` or just run the script
directly in the foreground once (`venv/bin/python3 <script>.py`) to see
the real traceback; systemd's journal can rotate crash logs out fast
under a rapid restart loop.

## What each one is for

| Unit | Runs | Depends on |
|---|---|---|
| `brain-proxy.service` | `brain_proxy.py` — conversation brain + `/run/<name>` HTTP bridge for n8n | wire-pod, Ollama |
| `vector-watchdog.service` | `vector_watchdog.py` — pickup/cliff/motion/sound reflexes | direct robot connection only |
| `n8n.service` | n8n itself — the scheduler that triggers proactive/autonomous behavior | none of the above; they depend on *it* |

See the main [README's Architecture section](../../README.md#architecture)
for how these three relate to each other and to the optional MCP server.
