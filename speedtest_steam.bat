@echo off
echo ============================================
echo  Steam CDN Throughput Test (1 minute)
echo  Target: ingest-rtmp.broadcast.steamcontent.com:1935
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"Write-Host 'Connecting...'; ^
$c = New-Object System.Net.Sockets.TcpClient; ^
$c.SendTimeout = 5000; ^
try { ^
  $c.Connect('ingest-rtmp.broadcast.steamcontent.com', 1935); ^
  Write-Host 'Connected. Pushing data for 60 seconds...'; ^
  $s = $c.GetStream(); ^
  $d = New-Object byte[] 32768; ^
  (New-Object Random).NextBytes($d); ^
  $t = New-Object System.Diagnostics.Stopwatch; ^
  $t.Start(); ^
  $b = 0; ^
  $last = 0; ^
  while ($t.ElapsedMilliseconds -lt 60000) { ^
    $s.Write($d, 0, $d.Length); ^
    $b += $d.Length; ^
    $elapsed = [math]::Round($t.Elapsed.TotalSeconds, 0); ^
    if ($elapsed -ne $last -and $elapsed % 10 -eq 0) { ^
      $current = [math]::Round(($b * 8) / $t.Elapsed.TotalSeconds / 1048576, 1); ^
      Write-Host ('  {0}s  -  {1} Mbps  ({2} MB sent)' -f $elapsed, $current, [math]::Round($b / 1048576, 1)); ^
      $last = $elapsed; ^
    } ^
  }; ^
  $t.Stop(); ^
  $mbps = [math]::Round(($b * 8) / $t.Elapsed.TotalSeconds / 1048576, 1); ^
  $mb = [math]::Round($b / 1048576, 1); ^
  Write-Host ''; ^
  Write-Host ('=== RESULT ==='); ^
  Write-Host ('  Duration:  {0}s' -f [math]::Round($t.Elapsed.TotalSeconds, 1)); ^
  Write-Host ('  Data sent: {0} MB' -f $mb); ^
  Write-Host ('  Throughput: {0} Mbps' -f $mbps); ^
  Write-Host ''; ^
  Write-Host ('  Max concurrent 5Mbps streams: ~{0}' -f [math]::Floor($mbps / 5.0)); ^
  Write-Host ('  Max concurrent 4Mbps streams: ~{0}' -f [math]::Floor($mbps / 4.0)); ^
  Write-Host ('  Max concurrent 3.5Mbps streams: ~{0}' -f [math]::Floor($mbps / 3.5)); ^
  $s.Close(); ^
} catch { ^
  Write-Host ('FAILED: ' + $_.Exception.Message); ^
} finally { ^
  $c.Close(); ^
}"

pause
