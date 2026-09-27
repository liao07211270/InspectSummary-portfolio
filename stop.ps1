# 停止 InspectSummary 服務（自動偵測 port）
foreach ($port in @(8000, 8001, 8002)) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $serverPid = $conn.OwningProcess | Select-Object -First 1
        if (Get-Process -Id $serverPid -ErrorAction SilentlyContinue) {
            Stop-Process -Id $serverPid -Force
            Write-Host "已停止 port $port（PID $serverPid）"
        } else {
            Write-Host "Port $port 有 zombie socket（PID $serverPid 已不存在），請重開機清除"
        }
    }
}
