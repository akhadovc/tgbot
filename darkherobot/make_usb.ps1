# Diddy'ni USB fleshkaga ko'chirish (portable versiya).
# Python, kutubxonalar, kod, sozlamalar va bot xotirasi fleshkaga yoziladi.
# Ishxonada hech narsa o'rnatish shart emas: fleshkadagi START_DIDDY.bat ni bosish kifoya.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$src = $PSScriptRoot
$log = Join-Path $src 'make_usb_log.txt'
Set-Content -LiteralPath $log -Value "" -Encoding UTF8
function Log($m) {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}
function Fail($m) { Log "XATO: $m"; Read-Host "Enter bosing"; exit 1 }

# 1) Kompyuterdagi Python (venv qaysi Python'dan yaratilgan bo'lsa, o'sha)
$cfgPath = Join-Path $src 'venv\pyvenv.cfg'
if (-not (Test-Path -LiteralPath $cfgPath)) { Fail "venv topilmadi. Avval setup.bat ni ishga tushiring." }
$homeLine = Get-Content -LiteralPath $cfgPath | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
$pyHome = ($homeLine -replace '^\s*home\s*=\s*', '').Trim()
if (-not (Test-Path -LiteralPath (Join-Path $pyHome 'python.exe'))) { Fail "Python topilmadi: $pyHome" }
Log "Python: $pyHome"

# 2) Fleshkani topish
$drives = @(Get-Volume | Where-Object { $_.DriveType -eq 'Removable' -and $_.DriveLetter })
if ($drives.Count -eq 1) {
    $letter = [string]$drives[0].DriveLetter
} else {
    if ($drives.Count -eq 0) { Write-Host "Fleshka topilmadi (ba'zi USB disklar 'Removable' emas)." -ForegroundColor Yellow }
    else { Write-Host ("Bir nechta fleshka bor: " + (($drives | ForEach-Object { "$($_.DriveLetter): $($_.FileSystemLabel)" }) -join ', ')) }
    $letter = (Read-Host "Fleshka harfini yozing (masalan E)").Trim().TrimEnd(':').ToUpper()
}
if ($letter -notmatch '^[D-Z]$') { Fail "Noto'g'ri disk harfi: '$letter'" }
$vol = Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue
if (-not $vol) { Fail "$($letter): disk topilmadi" }
if ($vol.SizeRemaining -lt 400MB) { Fail ("Fleshkada joy kam: " + [math]::Round($vol.SizeRemaining / 1MB) + " MB bor, kamida 400 MB kerak") }
$dst = "$($letter):\DiddyBot"
Log "Fleshka: $($letter): ($($vol.FileSystemLabel)), papka: $dst"

# 3) Uydagi botni to'xtatish (xotira fayli to'g'ri nusxalanishi va ikki bot bir vaqtda ishlamasligi uchun)
$all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")
$launchers = @($all | Where-Object { $_.ExecutablePath -like "$src\*" -and $_.CommandLine -like '*bot.py*' })
$ids = @($launchers | ForEach-Object { $_.ProcessId })
$children = @($all | Where-Object { $ids -contains $_.ParentProcessId })
$running = @($children) + @($launchers)
foreach ($p in $running) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
if ($running.Count -gt 0) { Log "Kompyuterdagi Diddy to'xtatildi"; Start-Sleep -Seconds 2 }

function Copy-Tree($from, $to, [string[]]$extra) {
    $rcArgs = @($from, $to) + $extra + @('/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    & robocopy @rcArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "nusxalashda xato ($from -> $to), kod $LASTEXITCODE" }
}

# 4) Python (testlar, IDLE, tkinter kerak emas) + kutubxonalar
Log "Python nusxalanyapti (1-2 daqiqa)..."
Copy-Tree $pyHome "$dst\python" @('/E', '/XD', '__pycache__', "$pyHome\Lib\test", "$pyHome\Lib\idlelib",
    "$pyHome\Lib\tkinter", "$pyHome\Lib\turtledemo", "$pyHome\tcl", "$pyHome\Doc", "$pyHome\Lib\site-packages",
    '/XF', '*.pyc')
Log "Kutubxonalar nusxalanyapti..."
Copy-Tree (Join-Path $src 'venv\Lib\site-packages') "$dst\python\Lib\site-packages" @('/E', '/XD', '__pycache__', '/XF', '*.pyc')

# 5) Bot kodi, sozlamalar, kalitlar va xotira
Log "Bot fayllari nusxalanyapti..."
Copy-Tree $src $dst @('*.py', 'requirements.txt', 'README.md', '.env', 'sozlamalar.env', 'diddy.db', 'diddy.db-wal', 'diddy.db-shm',
    '/XF', 'bot_v1_eski.py')

# 6) Fleshkadagi ishga tushirish fayli
$start = @'
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Diddy (USB)
set DIDDY_PORTABLE=1
set PYTHONDONTWRITEBYTECODE=1
set PYTHONNOUSERSITE=1
set PYTHONPATH=
set PYTHONHOME=
echo Diddy fleshkadan ishga tushyapti... Fleshkani chiqarsangiz bot o'chadi.
"%~dp0python\python.exe" bot.py
pause
'@
[IO.File]::WriteAllText("$dst\START_DIDDY.bat", ($start -replace "`r?`n", "`r`n"), (New-Object Text.UTF8Encoding($false)))

# 7) Tekshiruv: fleshkadagi Python kutubxonalarni ko'ryaptimi
$check = & "$dst\python\python.exe" -s -c "import telegram, openai, dotenv, imageio_ffmpeg; print('OK', telegram.__version__, openai.__version__)" 2>&1
Log "Tekshiruv: $check"
if (-not ("$check" -like 'OK*')) { Fail "fleshkadagi Python ishlamadi" }
$size = [math]::Round(((Get-ChildItem -LiteralPath $dst -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
Log "Tayyor! Fleshkada $size MB. Ishga tushirish: $dst\START_DIDDY.bat"
Write-Host ""
Write-Host "Eslatma: kompyuterdagi Diddy to'xtatildi. Bir vaqtda faqat bitta joyda ishlating." -ForegroundColor Yellow
Read-Host "Enter bosing"
