# InspectSummary 啟動腳本
# 使用方式：在 PowerShell 中執行 .\start.ps1
# 停止方式：Ctrl+C（在同一視窗）

Set-Location $PSScriptRoot

# 自動找可用的 port（8000 → 8001 → 8002）
$port = 8000
foreach ($p in @(8000, 8001, 8002)) {
    $inUse = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue
    if (-not $inUse) { $port = $p; break }
}

Write-Host "啟動 InspectSummary 服務 → http://localhost:$port/"
Write-Host "Swagger 文件 → http://localhost:$port/docs"
Write-Host "按 Ctrl+C 停止"
Write-Host ""

$env:PORT = $port
python server.py
