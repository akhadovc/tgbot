# Diddy'ni qayta ishga tushirish: shu papkadagi ishlab turgan botni to'xtatib, yangisini ochadi.
Set-Location -LiteralPath $PSScriptRoot
$folder = $PSScriptRoot
$all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")
$launchers = @($all | Where-Object { $_.ExecutablePath -like "$folder\*" -and $_.CommandLine -like '*bot.py*' })
$ids = @($launchers | ForEach-Object { $_.ProcessId })
$children = @($all | Where-Object { $ids -contains $_.ParentProcessId })
$running = @($children) + @($launchers)
foreach ($p in $running) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] stopped=$($running.Count)" | Out-File -FilePath (Join-Path $folder 'restart_log.txt') -Encoding utf8
Start-Sleep -Seconds 2
Start-Process -FilePath (Join-Path $folder "start_diddy.bat") -WorkingDirectory $folder
