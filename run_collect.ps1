# Daily capture, run by Windows Task Scheduler. Logs to logs\collect.log
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path logs | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== $stamp ===" | Out-File -Append -Encoding utf8 logs\collect.log
& "C:\Python314\python.exe" main.py collect *>&1 | Out-File -Append -Encoding utf8 logs\collect.log
