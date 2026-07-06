@echo off
setlocal enabledelayedexpansion

set "TARGET=ingest-rtmp.broadcast.steamcontent.com"
set "PORT=1935"
set "PING_COUNT=50"

echo ============================================
echo  SteamCast — Full Livestream Readiness Test
echo  Target: %TARGET%:%PORT%
echo  Pings:  %PING_COUNT%
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$host.UI.RawUI.WindowTitle = 'SteamCast — Livestream Readiness';" ^
"$t = %TARGET%;" ^
"$port = %PORT%;" ^
"$pc = %PING_COUNT%;" ^
"" ^
"Write-Host '=== Phase 1: Latency + Packet Loss + Jitter ===' -ForegroundColor Cyan;" ^
"Write-Host ('Pinging {0} ({1} samples)...' -f $t, $pc);" ^
"Write-Host '';" ^
"" ^
"$ping = Test-Connection -ComputerName $t -Count $pc -ErrorAction SilentlyContinue;" ^
"" ^
"if (-not $ping) {" ^
"  Write-Host '  [WARN] ICMP ping blocked or host unreachable.' -ForegroundColor Yellow;" ^
"  Write-Host '         (Starlink sometimes blocks ICMP. Trying with ping.exe...)' -ForegroundColor Yellow;" ^
"  $raw = ping -n $pc -w 3000 $t;" ^
"  if ($LASTEXITCODE -eq 0) {" ^
"    $times = @();" ^
"    $recv = 0; $sent = $pc;" ^
"    foreach ($line in $raw) {" ^
"      if ($line -match 'time[=<](\d+)ms') {" ^
"        $times += [int]$Matches[1];" ^
"        $recv++" ^
"      }" ^
"    }" ^
"  } else {" ^
"    Write-Host '  ping.exe also failed — ICMP fully blocked.' -ForegroundColor Red;" ^
"    $times = @(); $recv = 0; $sent = $pc" ^
"  }" ^
"} else {" ^
"  $times = $ping | Where-Object { $_.StatusCode -eq 0 } | ForEach-Object { $_.ResponseTime };" ^
"  $recv = $times.Count;" ^
"  $sent = $ping.Count;" ^
"}" ^
"" ^
"$lossPct = if ($sent -gt 0) { [math]::Round(($sent - $recv) / $sent * 100, 1) } else { 100 };" ^
"" ^
"Write-Host '';" ^
"Write-Host ('  Packets: {0}/{1} received  ({2}%% loss)' -f $recv, $sent, $lossPct) -ForegroundColor $(if ($lossPct -gt 5) {@('Red')} else {@('Green')});" ^
"" ^
"if ($times.Count -ge 2) {" ^
"  $min = [math]::Round(($times | Measure-Object -Minimum).Minimum, 0);" ^
"  $max = [math]::Round(($times | Measure-Object -Maximum).Maximum, 0);" ^
"  $avg = [math]::Round(($times | Measure-Object -Average).Average, 0);" ^
"  " ^
"  # Jitter = mean absolute deviation of consecutive RTT differences (RFC 1889 style)" ^
"  $diffs = @();" ^
"  for ($i = 1; $i -lt $times.Count; $i++) {" ^
"    $diffs += [math]::Abs($times[$i] - $times[$i-1])" ^
"  }" ^
"  $jitter = if ($diffs.Count -gt 0) { [math]::Round(($diffs | Measure-Object -Average).Average, 1) } else { 0 };" ^
"  $stdev = if ($times.Count -gt 1) {" ^
"    $sumSq = ($times | ForEach-Object { ($_ - $avg) * ($_ - $avg) } | Measure-Object -Sum).Sum;" ^
"    [math]::Round([math]::Sqrt($sumSq / ($times.Count - 1)), 1)" ^
"  } else { 0 };" ^
"  " ^
"  Write-Host ('  Latency:  min={0}ms  avg={1}ms  max={2}ms' -f $min, $avg, $max);" ^
"  Write-Host ('  Jitter:   {0}ms  (stdev {1}ms)' -f $jitter, $stdev) -ForegroundColor $(if ($jitter -gt 30) {@('Yellow')} else {@('Green')});" ^
"  " ^
"  # Score each metric for livestreaming" ^
"  $score = 100;" ^
"  if ($lossPct -gt 5) { $score -= 30 } elseif ($lossPct -gt 1) { $score -= 15 };" ^
"  if ($avg -gt 300) { $score -= 20 } elseif ($avg -gt 150) { $score -= 10 };" ^
"  if ($jitter -gt 50) { $score -= 25 } elseif ($jitter -gt 20) { $score -= 15 } elseif ($jitter -gt 10) { $score -= 5 };" ^
"  if ($max -gt 1000) { $score -= 10 };" ^
"  $score = [math]::Max(0, $score);" ^
"  Write-Host '';" ^
"  Write-Host ('  Live Readiness: {0}/100' -f $score) -ForegroundColor $(if ($score -ge 80) {@('Green')} elseif ($score -ge 50) {@('Yellow')} else {@('Red')});" ^
"  " ^
"  if ($score -ge 80) { Write-Host '  Rating: GOOD — Starlink stable enough for streaming' -ForegroundColor Green }" ^
"  elseif ($score -ge 50) { Write-Host '  Rating: FAIR — Reduce bitrate, monitor for drops' -ForegroundColor Yellow }" ^
"  else { Write-Host '  Rating: POOR — Expect buffering, consider backup connection' -ForegroundColor Red }" ^
"" ^
"} else {" ^
"  Write-Host '  Latency:  N/A (no successful pings)' -ForegroundColor Red;" ^
"  Write-Host '  Live Readiness: 0/100 — cannot measure' -ForegroundColor Red;" ^
"}" ^
"" ^
"Write-Host '';" ^
"Write-Host '=== Phase 2: Route Diagnostics (pathping) ===' -ForegroundColor Cyan;" ^
"Write-Host 'This takes ~90 seconds...' -ForegroundColor Gray;" ^
"Write-Host '';" ^
"$ppOut = pathping -n -q 10 -w 1000 $t 2>&1;" ^
"$ppLines = $ppOut | Select-String -Pattern '^\s+\d+' | Select-Object -First 15;" ^
"Write-Host '  Hop-by-hop (first 15 hops):' -ForegroundColor Gray;" ^
"if ($ppLines) { $ppLines | ForEach-Object { Write-Host ('  ' + $_) -ForegroundColor Gray } }" ^
"else { Write-Host '  (pathping returned no hop data)' -ForegroundColor Gray }" ^
"" ^
"Write-Host '';" ^
"Write-Host '=== Phase 3: Throughput Test (60s) ===' -ForegroundColor Cyan;" ^
"Write-Host 'Connecting...';" ^
"$c = New-Object System.Net.Sockets.TcpClient;" ^
"$c.SendTimeout = 5000;" ^
"try {" ^
"  $c.Connect($t, $port);" ^
"  Write-Host 'Connected. Pushing data for 60 seconds...';" ^
"  $s = $c.GetStream();" ^
"  $d = New-Object byte[] 32768;" ^
"  (New-Object Random).NextBytes($d);" ^
"  $st = [DateTime]::UtcNow;" ^
"  $b = 0;" ^
"  $last = 0;" ^
"  $errors = 0;" ^
"  while (1) {" ^
"    $elapsed = [math]::Round(([DateTime]::UtcNow - $st).TotalMilliseconds, 0);" ^
"    if ($elapsed -ge 60000) { break };" ^
"    try {" ^
"      $s.Write($d, 0, $d.Length);" ^
"      $b += $d.Length;" ^
"    } catch {" ^
"      $errors++;" ^
"      if ($errors -gt 5) { throw };  # too many write failures = connection dead" ^
"      Start-Sleep -Milliseconds 100" ^
"    }" ^
"    $sec = [math]::Floor($elapsed / 1000);" ^
"    if ($sec -ne $last -and $sec %% 10 -eq 0) {" ^
"      $current = [math]::Round(($b * 8) / [math]::Max($elapsed / 1000, 0.001) / 1048576, 1);" ^
"      Write-Host ('  {0}s  -  {1} Mbps  ({2} MB sent)' -f $sec, $current, [math]::Round($b / 1048576, 1));" ^
"      $last = $sec;" ^
"    }" ^
"  };" ^
"  $elapsedS = [math]::Max(([DateTime]::UtcNow - $st).TotalSeconds, 0.001);" ^
"  $mbps = [math]::Round(($b * 8) / $elapsedS / 1048576, 1);" ^
"  $mb = [math]::Round($b / 1048576, 1);" ^
"  Write-Host '';" ^
"  Write-Host ('=== RESULTS ===') -ForegroundColor Cyan;" ^
"  Write-Host '';" ^
"  Write-Host ('  Phase 1 — Network Quality')" ^
"  Write-Host ('    Packet Loss:  {0}%%' -f $lossPct);" ^
"  if ($times.Count -ge 2) {" ^
"  Write-Host ('    Latency:      min={0}ms  avg={1}ms  max={2}ms' -f $min, $avg, $max);" ^
"  Write-Host ('    Jitter:       {0}ms  (stdev {1}ms)' -f $jitter, $stdev);" ^
"  }" ^
"  Write-Host '';" ^
"  Write-Host ('  Phase 3 — Throughput')" ^
"  Write-Host ('    Duration:     {0}s' -f [math]::Round($elapsedS, 1));" ^
"  Write-Host ('    Data sent:    {0} MB' -f $mb);" ^
"  Write-Host ('    Throughput:   {0} Mbps' -f $mbps);" ^
"  if ($errors -gt 0) { Write-Host ('    Write errors: {0}' -f $errors) -ForegroundColor Yellow };" ^
"  Write-Host '';" ^
"  Write-Host ('  Stream Capacity');" ^
"  Write-Host ('    Max 5Mbps streams:    ~{0}' -f [math]::Floor($mbps / 5.0));" ^
"  Write-Host ('    Max 4Mbps streams:    ~{0}' -f [math]::Floor($mbps / 4.0));" ^
"  Write-Host ('    Max 3.5Mbps streams:  ~{0}' -f [math]::Floor($mbps / 3.5));" ^
"  if ($lossPct -gt 2 -or $jitter -gt 30) {" ^
"    Write-Host '';" ^
"    Write-Host ('  ⚠ RECOMMENDATION: Reduce bitrate or enable FEC/retransmit') -ForegroundColor Yellow;" ^
"    Write-Host ('    Packet loss >2%% or jitter >30ms degrades livestream quality') -ForegroundColor Yellow;" ^
"    Write-Host ('    Try: --prep 3500k instead of 5000k to ride out Starlink handoffs') -ForegroundColor Yellow;" ^
"  }" ^
"  $s.Close();" ^
"} catch {" ^
"  Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red;" ^
"} finally {" ^
"  $c.Close();" ^
"}" ^
"" ^
"Write-Host '';" ^
"Write-Host 'Press any key to exit...' -NoNewline;" ^
"$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown');"

endlocal
