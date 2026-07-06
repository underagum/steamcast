param(
    [string]$Target = "ingest-rtmp.broadcast.steamcontent.com",
    [int]$Port = 1935,
    [int]$PingCount = 50
)

# Ensure UTF-8 for proper emoji/symbol rendering in Windows Terminal
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Host.UI.RawUI.WindowTitle = "SteamCast - Livestream Readiness Test"
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " SteamCast - Full Livestream Readiness Test" -ForegroundColor Cyan
Write-Host " Target: $Target`:$Port" -ForegroundColor Cyan
Write-Host " Pings:  $PingCount" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

<#
  .SYNOPSIS
    Phase 1: Packet Loss, Latency, Jitter
    Tries ICMP first; falls back to TCP connect timing if ICMP is blocked (Starlink).
#>
Write-Host "=== Phase 1: Latency + Packet Loss + Jitter ===" -ForegroundColor Cyan
Write-Host "Pinging $Target ($PingCount samples)..."
Write-Host ""

$ping = Test-Connection -ComputerName $Target -Count $PingCount -ErrorAction SilentlyContinue
$icmpBlocked = $false

if (-not $ping) {
    Write-Host "  [WARN] ICMP ping blocked or host unreachable." -ForegroundColor Yellow
    Write-Host "         (Starlink sometimes blocks ICMP. Trying with ping.exe...)" -ForegroundColor Yellow
    $raw = ping -n $PingCount -w 3000 $Target 2>&1
    if ($LASTEXITCODE -eq 0) {
        $times = @()
        $recv = 0
        $sent = $PingCount
        foreach ($line in $raw) {
            if ($line -match 'time[=<](\d+)ms') {
                $times += [int]$Matches[1]
                $recv++
            }
        }
    } else {
        Write-Host "  ping.exe also failed - ICMP fully blocked." -ForegroundColor Red
        $icmpBlocked = $true
    }
} else {
    $times = $ping | Where-Object { $_.StatusCode -eq 0 } | ForEach-Object { $_.ResponseTime }
    $recv = $times.Count
    $sent = $ping.Count
}

# ── TCP fallback for ICMP-blocked networks ──
if ($icmpBlocked) {
    Write-Host "  [FALLBACK] Measuring TCP connect latency (20 rapid connects)..." -ForegroundColor Yellow

    $tcpRtts = @()
    $tcpFails = 0
    $tcpSamples = 20

    for ($i = 0; $i -lt $tcpSamples; $i++) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $tc = New-Object System.Net.Sockets.TcpClient
            $tc.Connect($Target, $Port)
            $sw.Stop()
            $tcpRtts += $sw.ElapsedMilliseconds
            $tc.Close()
        } catch {
            $tcpFails++
        }
    }

    $times = $tcpRtts
    $recv = $tcpRtts.Count
    $sent = $tcpSamples

    if ($tcpRtts.Count -ge 2) {
        Write-Host "  Measured TCP SYN-SYN/ACK RTT. Lower bound = network latency+Starlink processing." -ForegroundColor Gray
        $lossPct = [math]::Round($tcpFails / $tcpSamples * 100, 1)
    } else {
        $lossPct = 100
    }
} else {
    $lossPct = if ($sent -gt 0) { [math]::Round(($sent - $recv) / $sent * 100, 1) } else { 100 }
}

$lossColor = if ($lossPct -gt 5) { "Red" } elseif ($lossPct -gt 0) { "Yellow" } else { "Green" }

Write-Host ""
Write-Host "  Connectivity: $recv/$sent successful  ($lossPct% failure)" -ForegroundColor $lossColor

if ($times.Count -ge 2) {
    $min = [math]::Round(($times | Measure-Object -Minimum).Minimum, 0)
    $max = [math]::Round(($times | Measure-Object -Maximum).Maximum, 0)
    $avg = [math]::Round(($times | Measure-Object -Average).Average, 0)

    # Jitter = mean absolute deviation of consecutive RTT differences (RFC 1889)
    $diffs = @()
    for ($i = 1; $i -lt $times.Count; $i++) {
        $diffs += [math]::Abs($times[$i] - $times[$i-1])
    }
    $jitter = if ($diffs.Count -gt 0) { [math]::Round(($diffs | Measure-Object -Average).Average, 1) } else { 0 }
    $sumSq = ($times | ForEach-Object { ($_ - $avg) * ($_ - $avg) } | Measure-Object -Sum).Sum
    $stdev = [math]::Round([math]::Sqrt($sumSq / ($times.Count - 1)), 1)

    $jitterColor = if ($jitter -gt 30) { "Yellow" } else { "Green" }
    $latencyColor = if ($avg -gt 150) { "Yellow" } else { "Green" }

    Write-Host "  Latency:  min=${min}ms  avg=${avg}ms  max=${max}ms" -ForegroundColor $latencyColor
    Write-Host "  Jitter:   ${jitter}ms  (stdev ${stdev}ms)" -ForegroundColor $jitterColor

    # Composite readiness score (0-100)
    $score = 100
    if ($lossPct -gt 5) { $score -= 30 } elseif ($lossPct -gt 1) { $score -= 15 }
    if ($avg -gt 300) { $score -= 20 } elseif ($avg -gt 150) { $score -= 10 }
    if ($jitter -gt 50) { $score -= 25 } elseif ($jitter -gt 20) { $score -= 15 } elseif ($jitter -gt 10) { $score -= 5 }
    if ($max -gt 1000) { $score -= 10 }
    $score = [math]::Max(0, $score)

    $scoreColor = if ($score -ge 80) { "Green" } elseif ($score -ge 50) { "Yellow" } else { "Red" }
    $scoreLabel = if ($score -ge 80) { "GOOD - Starlink stable enough for streaming" } `
             elseif ($score -ge 50) { "FAIR - Reduce bitrate, monitor for drops" } `
             else { "POOR - Expect buffering, consider backup connection" }

    Write-Host ""
    Write-Host "  Live Readiness: $score/100" -ForegroundColor $scoreColor
    Write-Host "  Rating: $scoreLabel" -ForegroundColor $scoreColor
} else {
    Write-Host "  Latency:  N/A (no successful connections)" -ForegroundColor Red
    Write-Host "  Live Readiness: 0/100 - cannot measure" -ForegroundColor Red
    $jitter = 0
}

<#
  .SYNOPSIS
    Phase 2: Route Diagnostics
#>
Write-Host ""
Write-Host "=== Phase 2: Route Diagnostics (pathping) ===" -ForegroundColor Cyan
Write-Host "This takes ~90 seconds..." -ForegroundColor Gray
Write-Host ""

$ppOut = pathping -n -q 10 -w 1000 $Target 2>&1
$ppLines = $ppOut | Select-String -Pattern '^\s+\d+' | Select-Object -First 15
Write-Host "  Hop-by-hop (first 15 hops):" -ForegroundColor Gray
if ($ppLines) {
    $ppLines | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor Gray }
} else {
    Write-Host "  (pathping returned no hop data)" -ForegroundColor Gray
}

<#
  .SYNOPSIS
    Phase 3: Throughput Test (60s)
#>
Write-Host ""
Write-Host "=== Phase 3: Throughput Test (60s) ===" -ForegroundColor Cyan
Write-Host "Connecting..."
$c = New-Object System.Net.Sockets.TcpClient
$c.SendTimeout = 5000

try {
    $c.Connect($Target, $Port)
    Write-Host "Connected. Pushing data for 60 seconds..."
    $s = $c.GetStream()
    $d = New-Object byte[] 32768
    (New-Object Random).NextBytes($d)
    $st = [DateTime]::UtcNow
    $b = 0
    $last = 0
    $errors = 0

    while ($true) {
        $elapsed = [math]::Round(([DateTime]::UtcNow - $st).TotalMilliseconds, 0)
        if ($elapsed -ge 60000) { break }

        try {
            $s.Write($d, 0, $d.Length)
            $b += $d.Length
        } catch {
            $errors++
            if ($errors -gt 5) { throw }  # too many write failures = connection dead
            Start-Sleep -Milliseconds 100
        }

        $sec = [math]::Floor($elapsed / 1000)
        if ($sec -ne $last -and $sec % 10 -eq 0) {
            $current = [math]::Round(($b * 8) / [math]::Max($elapsed / 1000, 0.001) / 1048576, 1)
            Write-Host "  ${sec}s  -  ${current} Mbps  ($([math]::Round($b / 1048576, 1)) MB sent)"
            $last = $sec
        }
    }

    $elapsedS = [math]::Max(([DateTime]::UtcNow - $st).TotalSeconds, 0.001)
    $mbps = [math]::Round(($b * 8) / $elapsedS / 1048576, 1)
    $mb = [math]::Round($b / 1048576, 1)

    Write-Host ""
    Write-Host "=== RESULTS ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Phase 1 - Network Quality"
    Write-Host "    Packet Loss:  $lossPct%"
    if ($times.Count -ge 2) {
        Write-Host "    Latency:      min=${min}ms  avg=${avg}ms  max=${max}ms"
        Write-Host "    Jitter:       ${jitter}ms  (stdev ${stdev}ms)"
    }
    Write-Host ""
    Write-Host "  Phase 3 - Throughput"
    Write-Host "    Duration:     $([math]::Round($elapsedS, 1))s"
    Write-Host "    Data sent:    $mb MB"
    Write-Host "    Throughput:   $mbps Mbps"
    if ($errors -gt 0) {
        Write-Host "    Write errors: $errors" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  Stream Capacity"
    Write-Host "    Max 5Mbps streams:    ~$([math]::Floor($mbps / 5.0))"
    Write-Host "    Max 4Mbps streams:    ~$([math]::Floor($mbps / 4.0))"
    Write-Host "    Max 3.5Mbps streams:  ~$([math]::Floor($mbps / 3.5))"

    if ($lossPct -gt 2 -or ($times.Count -ge 2 -and $jitter -gt 30)) {
        Write-Host ""
        Write-Host "  >> RECOMMENDATION: Reduce bitrate or enable FEC/retransmit" -ForegroundColor Yellow
        Write-Host "     Packet loss >2% or jitter >30ms degrades livestream quality" -ForegroundColor Yellow
        Write-Host "     Try: --prep 3500k instead of 5000k to ride out Starlink handoffs" -ForegroundColor Yellow
    }

    $s.Close()
} catch {
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    $c.Close()
}

Write-Host ""
Write-Host "Press any key to exit..." -NoNewline
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
