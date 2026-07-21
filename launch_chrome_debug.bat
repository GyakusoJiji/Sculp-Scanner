@echo off
rem Launch Chrome with a remote debugging port so Sculp-Scanner can attach.
rem Usage: launch_chrome_debug.bat [URL] [PORT]
rem (Japanese notes are in README.md - this file stays ASCII so cmd.exe parses it.)
cd /d "%~dp0"
python -m sculpscanner.chrome %*
if errorlevel 1 pause
