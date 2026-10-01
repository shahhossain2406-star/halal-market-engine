# Quarterly halal screening, run by Windows Task Scheduler. Logs to logs\screen.log
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path logs | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== $stamp ===" | Out-File -Append -Encoding utf8 logs\screen.log
$env:PYTHONIOENCODING = "utf-8"
& "C:\Python314\python.exe" main.py screen *>&1 | Out-File -Append -Encoding utf8 logs\screen.log
