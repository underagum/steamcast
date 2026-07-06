#!/usr/bin/env python3
"""Steam CDN livestream readiness test — Linux equivalent of speedtest_steam.bat.

Measures: throughput, packet loss, latency, jitter, route health.
Target: ingest-rtmp.broadcast.steamcontent.com:1935
"""
import socket, time, random, math, sys, subprocess, re, statistics

ENDPOINT = "ingest-rtmp.broadcast.steamcontent.com"
PORT = 1935
PING_COUNT = 50
DURATION = 60
BATCH = 32768

def c(text, code=0):
    """Simple ANSI color wrapper."""
    colors = {0: "", 1: "\033[91m", 2: "\033[93m", 3: "\033[36m", 4: "\033[32m", 5: "\033[90m"}
    reset = "\033[0m"
    return f"{colors.get(code, '')}{text}{reset}"

def color_score(s):
    if s >= 80: return 4
    if s >= 50: return 2
    return 1

def pitch_metric(val, thresholds):
    """Return color code based on thresholds [(red_above, yellow_above, green_label), ...]"""
    for red, yellow in thresholds:
        if val > red: return 1
        if val > yellow: return 2
    return 4

# ── Phase 1: Latency + Packet Loss + Jitter ──────────────────────────
print("=" * 55)
print(c(" Phase 1: Latency + Packet Loss + Jitter", 3))
print(f" Pinging {ENDPOINT} ({PING_COUNT} samples)...")
print("=" * 55)
print()

ping_times = []
sent = PING_COUNT
recv = 0

try:
    result = subprocess.run(
        ["ping", "-c", str(PING_COUNT), "-W", "2", "-i", "0.2", ENDPOINT],
        capture_output=True, text=True, timeout=PING_COUNT * 0.5 + 10
    )
    # Parse ping output
    for line in result.stdout.splitlines():
        m = re.search(r'time[=<](\d+\.?\d*)\s*ms', line)
        if m:
            ping_times.append(float(m.group(1)))
    # Count received
    m_recv = re.search(r'(\d+)\s+received', result.stdout)
    if m_recv:
        recv = int(m_recv.group(1))
    # Also check "0 received"
    if "0 received" in result.stdout:
        recv = 0
    sent_match = re.search(r'(\d+)\s+packets?\s+transmitted', result.stdout)
    if sent_match:
        sent = int(sent_match.group(1))
except (subprocess.TimeoutExpired, FileNotFoundError) as e:
    print(c(f"  ping failed: {e}", 1))
    ping_times = []
    recv = 0

ping_min = ping_avg = ping_max = 0.0
jitter = ping_stdev = 0.0

loss_pct = round((sent - recv) / sent * 100, 1) if sent > 0 else 100
loss_color = 1 if loss_pct > 5 else 4
print(f"  Packets: {recv}/{sent} received  ({c(f'{loss_pct}% loss', loss_color)})")

if len(ping_times) >= 2:
    ping_min = round(min(ping_times), 0)
    ping_max = round(max(ping_times), 0)
    ping_avg = round(statistics.mean(ping_times), 0)
    ping_stdev = round(statistics.stdev(ping_times), 1)

    # RFC 1889 jitter = mean absolute difference between consecutive RTTs
    diffs = [abs(ping_times[i] - ping_times[i-1]) for i in range(1, len(ping_times))]
    jitter = round(statistics.mean(diffs), 1) if diffs else 0

    jitter_color = pitch_metric(jitter, [(50, 20), (20, 10)])
    latency_color = pitch_metric(ping_avg, [(300, 150), (150, 80)])

    print(f"  Latency:  min={ping_min:.0f}ms  avg={c(f'{ping_avg:.0f}ms', latency_color)}  max={ping_max:.0f}ms")
    print(f"  Jitter:   {c(f'{jitter}ms', jitter_color)}  (stdev {ping_stdev}ms)")

    # Composite readiness score (0-100)
    score = 100
    if loss_pct > 5: score -= 30
    elif loss_pct > 1: score -= 15
    if ping_avg > 300: score -= 20
    elif ping_avg > 150: score -= 10
    if jitter > 50: score -= 25
    elif jitter > 20: score -= 15
    elif jitter > 10: score -= 5
    if ping_max > 1000: score -= 10
    score = max(0, int(score))

    score_color = color_score(score)
    print(f"\n  Live Readiness: {c(f'{score}/100', score_color)}", end="")
    if score >= 80:
        print(c(" — GOOD", 4))
    elif score >= 50:
        print(c(" — FAIR", 2))
    else:
        print(c(" — POOR", 1))
else:
    print(c("  Latency:  N/A (no successful pings)", 1))
    score = 0

# ── Phase 2: Route Diagnostics ─────────────────────────────────────
print()
print(c(" Phase 2: Route Diagnostics", 3))
print(" This takes ~90 seconds..." if sys.platform != "linux" else " Skipped on Linux (use mtr instead)...")
print()

# ── Phase 3: Throughput Test ───────────────────────────────────────
print(c(" Phase 3: Throughput Test (60s)", 3))
print("Connecting...", flush=True)

try:
    sock = socket.create_connection((ENDPOINT, PORT), timeout=5)
except OSError as e:
    print(c(f"FAILED: {e}", 1))
    sys.exit(1)

print("Connected. Pushing data for 60 seconds...", flush=True)

data = random.randbytes(BATCH)
start = time.monotonic()
bytes_sent = 0
last_report = -1
error = None
write_errors = 0

try:
    while time.monotonic() - start < DURATION:
        try:
            sock.sendall(data)
            bytes_sent += BATCH
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            write_errors += 1
            if write_errors > 5:
                error = e
                break
            time.sleep(0.1)
            continue
        elapsed = int(time.monotonic() - start)
        if elapsed > 0 and elapsed != last_report and elapsed % 10 == 0:
            mbps = (bytes_sent * 8) / (elapsed * 1_048_576)
            mb = bytes_sent / 1_048_576
            print(f"  {elapsed:>3}s  -  {mbps:>5.1f} Mbps  ({mb:.1f} MB sent)")
            last_report = elapsed
except KeyboardInterrupt:
    print("\nInterrupted.")
finally:
    sock.close()
    elapsed = max(time.monotonic() - start, 0.001)

# ── Results ─────────────────────────────────────────────────────────
print()
print(c("═══ RESULTS ═══", 3))
print()

# Phase 1 summary
enough_pings = len(ping_times) >= 2
print(c("  Network Quality", 3))
print(f"    Packet Loss:  {loss_pct}%")
if enough_pings:
    print(f"    Latency:      min={ping_min:.0f}ms  avg={ping_avg:.0f}ms  max={ping_max:.0f}ms")
    print(f"    Jitter:       {jitter}ms  (stdev {ping_stdev}ms)")
print()

# Phase 3 summary
print(c("  Throughput", 3))
if error:
    print(f"    Dropped at {elapsed:.1f}s: {error}")
if bytes_sent == 0:
    print(c("    No data sent — Steam rejected the raw TCP connection.", 2))
    print(c("    (Expected: RTMP ingest requires an RTMP handshake.)", 5))
else:
    mbps = (bytes_sent * 8) / (elapsed * 1_048_576)
    mb = bytes_sent / 1_048_576
    print(f"    Duration:     {elapsed:.1f}s")
    print(f"    Data sent:    {mb:.1f} MB")
    print(f"    Throughput:   {mbps:.1f} Mbps")
    if write_errors > 0:
        print(c(f"    Write errors: {write_errors}", 2))

    print()
    print(c("  Stream Capacity", 3))
    print(f"    Max 5Mbps streams:    ~{math.floor(mbps / 5)}")
    print(f"    Max 4Mbps streams:    ~{math.floor(mbps / 4)}")
    print(f"    Max 3.5Mbps streams:  ~{math.floor(mbps / 3.5)}")

    if loss_pct > 2 or (enough_pings and jitter > 30):
        print()
        print(c("  ⚠ RECOMMENDATION", 2))
        print(c("    Packet loss >2% or jitter >30ms degrades livestream", 2))
        print(c("    Reduce bitrate or lower stream count:", 2))
        print(c("    Try: 3500k prep bitrate instead of 5000k", 2))
        print(c("    to ride out Starlink orbital handoffs.", 2))
