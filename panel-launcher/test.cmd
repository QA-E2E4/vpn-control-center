@echo off
setlocal
cd /d "%~dp0"
set "COMPILER=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%COMPILER%" set "COMPILER=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%COMPILER%" (
  echo .NET Framework compiler not found.
  exit /b 1
)
set "TEST_EXE=%TEMP%\OpenFlux.Validation.%RANDOM%.exe"
"%COMPILER%" /nologo /target:exe /main:Validation /out:"%TEST_EXE%" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Runtime.Serialization.dll source\OpenFlux.cs source\Validation.cs
if errorlevel 1 exit /b 1
"%TEST_EXE%"
set "RESULT=%ERRORLEVEL%"
del /q "%TEST_EXE%" 2>nul
exit /b %RESULT%
