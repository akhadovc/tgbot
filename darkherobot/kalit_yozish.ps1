# Diddy 2.0 - yangi kalitlarni .env ga yozish. Kalitlar faqat shu kompyuterda qoladi.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Ask($prompt, $pattern, $example) {
    while ($true) {
        Write-Host ""
        Write-Host $prompt -ForegroundColor Cyan
        Write-Host "  (namuna: $example)  - nusxa oling va shu oynaga o'ng tugma bilan qo'ying, keyin Enter" -ForegroundColor DarkGray
        $value = (Read-Host ">").Trim().Trim('"').Trim("'")
        if ($value -match $pattern) { return $value }
        Write-Host "Bu to'g'ri kalitga o'xshamayapti, qaytadan qo'ying." -ForegroundColor Red
    }
}

Write-Host "=== Diddy 2.0: yangi kalitlar ===" -ForegroundColor Yellow

# 1) Telegram token
while ($true) {
    $token = Ask "1) @BotFather bergan YANGI Telegram bot tokenini qo'ying:" '^\d{6,12}:[A-Za-z0-9_-]{30,}$' "1234567890:AAH...."
    try {
        $me = Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/getMe" -TimeoutSec 20
        Write-Host ("   OK: bot @" + $me.result.username + " topildi") -ForegroundColor Green
        break
    } catch {
        Write-Host "   Telegram bu tokenni qabul qilmadi. Tokenni to'liq nusxalaganingizni tekshiring." -ForegroundColor Red
    }
}

# 2) OpenAI key
while ($true) {
    $key = Ask "2) YANGI OpenAI API kalitini qo'ying:" '^sk-[A-Za-z0-9_-]{20,}$' "sk-proj-...."
    try {
        $null = Invoke-RestMethod -Uri "https://api.openai.com/v1/models/gpt-6.1-sol" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 20
        Write-Host "   OK: kalit ishlayapti, gpt-6.1-sol ochiq" -ForegroundColor Green
        break
    } catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code -eq 401) { Write-Host "   OpenAI kalitni rad etdi (noto'g'ri yoki o'chirilgan)." -ForegroundColor Red }
        elseif ($code -eq 404 -or $code -eq 403) {
            Write-Host "   Kalit to'g'ri, lekin bu akkauntda gpt-6.1-sol ochiq emas. Billing/limitni tekshiring." -ForegroundColor Red
            $ans = Read-Host "   Baribir shu kalit bilan davom etaymi? (ha/yo'q)"
            if ($ans -match '^h') { break }
        }
        else { Write-Host "   OpenAI'ga ulanib bo'lmadi (internet?). Qayta urinib ko'ring." -ForegroundColor Red }
    }
}

# 3) .env ni yangilash: faqat shu ikki qator almashadi, boshqa sozlamalar saqlanadi
$envPath = Join-Path $PSScriptRoot ".env"
$lines = @()
if (Test-Path -LiteralPath $envPath) {
    $lines = Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*(TELEGRAM_BOT_TOKEN|OPENAI_API_KEY)\s*=' }
}
$lines = @("TELEGRAM_BOT_TOKEN=$token", "OPENAI_API_KEY=$key") + $lines
[IO.File]::WriteAllLines($envPath, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "3) .env yangilandi." -ForegroundColor Green

# 4) shu papkadagi ishlab turgan Diddy'ni to'xtatish
$folder = $PSScriptRoot
$all = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")
# venv\Scripts\python.exe is a small launcher that starts the real Python as a child process,
# so stop the launcher from this folder and the child it started.
$launchers = @($all | Where-Object { $_.ExecutablePath -like "$folder\*" -and $_.CommandLine -like '*bot.py*' })
$ids = @($launchers | ForEach-Object { $_.ProcessId })
$children = @($all | Where-Object { $ids -contains $_.ParentProcessId })
$running = @($children) + @($launchers)
foreach ($p in $running) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
if ($running.Count -gt 0) { Write-Host "4) Eski ishlab turgan Diddy to'xtatildi." -ForegroundColor Green; Start-Sleep -Seconds 2 }

# 5) yangi kalitlar bilan ishga tushirish
Start-Process -FilePath (Join-Path $folder "start_diddy.bat") -WorkingDirectory $folder
Write-Host "5) Diddy yangi kalitlar bilan ishga tushdi (alohida oynada)." -ForegroundColor Green
Write-Host ""
Write-Host "Tayyor! Bu oynani yopsangiz bo'ladi." -ForegroundColor Yellow
Read-Host "Enter bosing"
