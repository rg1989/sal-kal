@echo off
rem Sal-Kal setup for Windows: double-click. It fetches the current installer from sal-kal.com and runs it
rem (no admin rights needed; everything goes in %LOCALAPPDATA%\Sal-Kal).
title Sal-Kal setup
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://sal-kal.com/install.ps1 | iex"
echo.
pause
