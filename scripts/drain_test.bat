@echo off
rem Battery runtime test - log drain until Vector docks / goes low
rem Repo was split into core/ life/ tools/ folders 2026-09-20 - this script
rem itself now lives in scripts/, one level below the repo root.
cd /d %~dp0\..
start "vector-drain-log" /min venv\Scripts\python.exe life\charge_monitor.py --file drain_runtime_test.csv --minutes 190 --every 3
