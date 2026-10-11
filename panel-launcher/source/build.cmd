@echo off
setlocal
cd /d "%~dp0"
set "COMPILER=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%COMPILER%" set "COMPILER=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%COMPILER%" (
  echo .NET Framework compiler not found.
  pause
  exit /b 1
)
"%COMPILER%" /nologo /target:winexe /out:"..\OpenFlux.exe" /win32manifest:app.manifest /win32icon:icons\main.ico /resource:icons\main.ico,OpenFlux.main.ico /resource:icons\offline.ico,OpenFlux.offline.ico /resource:icons\connecting.ico,OpenFlux.connecting.ico /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Runtime.Serialization.dll OpenFlux.cs
if errorlevel 1 (
  pause
  exit /b 1
)
echo Built OpenFlux.exe.
pause
