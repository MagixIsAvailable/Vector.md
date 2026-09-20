@echo off
rem Vector always-on stack launcher - run when the stack is down (double-click).
rem Starts: brain proxy (:8590), watchdog (danger monitor), proactive speech.
rem NOTE: do NOT run while instances are already up (creates duplicates).
rem Repo was split into core/ life/ tools/ folders 2026-09-20 - this script
rem itself now lives in scripts/, one level below the repo root.
cd /d %~dp0\..
start "vector-brain" /min venv\Scripts\python.exe core\brain_proxy.py
start "vector-watchdog" /min venv\Scripts\python.exe core\vector_watchdog.py
start "vector-proactive" /min venv\Scripts\python.exe life\proactive.py --cooldown 25
