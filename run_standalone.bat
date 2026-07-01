@echo off
REM Noctyra Model Manager - standalone launcher (no ComfyUI required)
REM
REM IMPORTANT: keep this file ASCII-only. Windows CMD parses .bat files in the
REM system code page (GBK / cp936 on Chinese Windows) BEFORE chcp takes effect,
REM so any non-ASCII characters here will produce "not recognized as command"
REM errors like: 'xxxx' is not recognized as an internal or external command.
REM
REM Defaults:
REM   - Port: reads manager_config.json "server_port" (fallback 8199)
REM   - Host: 127.0.0.1 (use --host 0.0.0.0 for LAN access)
REM
REM Usage:
REM   run_standalone.bat                      (default port, localhost only)
REM   run_standalone.bat --port 9000          (custom port)
REM   run_standalone.bat --host 0.0.0.0       (LAN access)
REM   run_standalone.bat --no-auto-shutdown   (disable ComfyUI auto-exit)

setlocal
cd /d "%~dp0"

REM Switch console to UTF-8 so Python log output shows Chinese correctly
REM (this is fine here because chcp affects display, not .bat parsing above)
chcp 65001 >nul 2>&1

REM Resolve python_embeded\python.exe from current layout:
REM   this file: <portable>\ComfyUI\custom_nodes\ComfyUI-Noctyra-Manager\run_standalone.bat
REM   python:    <portable>\python_embeded\python.exe
set "PYTHON_EMBEDED=%~dp0..\..\..\python_embeded\python.exe"

if not exist "%PYTHON_EMBEDED%" (
    echo [Noctyra] python_embeded\python.exe not found at:
    echo [Noctyra]   %PYTHON_EMBEDED%
    echo [Noctyra] Please verify you're running this inside ComfyUI_windows_portable.
    echo [Noctyra] Or edit PYTHON_EMBEDED in this file to point to your python.
    pause
    exit /b 1
)

echo [Noctyra] Starting standalone mode (no ComfyUI)
echo [Noctyra] Python: %PYTHON_EMBEDED%
echo.

REM Run via file path, not -m manager: python_embeded's sys.path doesn't include CWD
"%PYTHON_EMBEDED%" "%~dp0manager\__main__.py" %*

echo.
echo [Noctyra] Exited. Press any key to close...
pause >nul
endlocal
