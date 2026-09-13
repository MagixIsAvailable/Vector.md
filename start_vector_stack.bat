@echo off
rem Vector always-on stack launcher - run when the stack is down (double-click).
rem Starts: brain proxy (:8590), watchdog (danger monitor), proactive speech.
rem NOTE: do NOT run while instances are already up (creates duplicates).
cd /d %~dp0
start "vector-brain" /min venv\Scripts\python.exe brain_proxy.py
start "vector-watchdog" /min venv\Scripts\python.exe vector_watchdog.py
start "vector-proactive" /min venv\Scripts\python.exe proactive.py --cooldown 25
