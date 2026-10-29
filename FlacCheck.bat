@echo off
rem Startet FlacCheck mit der Conda-Umgebung "flaccheck" (ohne Konsolenfenster)
cd /d "%~dp0"
start "" "%USERPROFILE%\anaconda3\envs\flaccheck\pythonw.exe" main.py
