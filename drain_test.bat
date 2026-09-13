@echo off
rem Battery runtime test - log drain until Vector docks / goes low
cd /d %~dp0
start "vector-drain-log" /min venv\Scripts\python.exe charge_monitor.py --file drain_runtime_test.csv --minutes 190 --every 3
