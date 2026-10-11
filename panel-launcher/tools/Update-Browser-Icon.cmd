@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; try { $config=Join-Path $env:LOCALAPPDATA 'OpenFluxPanel\settings.json'; if (-not (Test-Path -LiteralPath $config)) { throw 'Open OpenFlux.exe and save connection settings first.' }; $s=Get-Content -Raw -LiteralPath $config | ConvertFrom-Json; if ($s.Host -notmatch '^[A-Za-z0-9:][A-Za-z0-9:._-]*$' -or $s.User -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]*$' -or [int]$s.Port -lt 1 -or [int]$s.Port -gt 65535 -or -not (Test-Path -LiteralPath $s.KeyPath -PathType Leaf)) { throw 'Invalid connection settings. Check OpenFlux.exe.' }; Write-Host ('Updating browser icon on '+$s.Host+'...'); $OutputEncoding=New-Object System.Text.UTF8Encoding; Get-Content -Raw -LiteralPath '.\update-browser-icon.py' | & ssh.exe -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -p $s.Port -i $s.KeyPath -l $s.User $s.Host 'python3 -'; exit $LASTEXITCODE } catch { Write-Host $_; exit 1 }"
if errorlevel 1 (
  echo Update failed. Check the message above.
) else (
  echo Done. Refresh the panel with Ctrl+F5.
)
pause
