# Roadmap

## Maybe (future plans — not scheduled)

- **`steamcastd` binary rename.** Follow the Unix daemon convention (`sshd`, `crond`, `httpd`) — the background process would be `steamcastd`, the service stays `steamcast.service`, the CLI stays `steamcast`. Cosmetic; touches launcher script, unit file, signal-handler paths, docs, and the `.exe` build.
- **PREP non-interactive flag.** `steamcast prep` currently requires interactive prompts (Confirm.ask / bitrate). Headless runs crash with `EOFError` when stdin is piped. Add `--yes` / `--bitrate N` flags so PREP runs cleanly in scripts and cron without `printf ... | steamcast prep`.
- **In-app Steam ingest speedtest.** Built-in readiness check that measures upload throughput/latency/jitter/packet-loss to Steam's RTMP ingest (replacing the removed standalone `speedtest_steam.*` scripts): `steamcast speedtest [--ingest <url>]`, plus a pre-flight hook before CAST starts. Useful for catching an uplink too thin for the 5 Mbps CBR spec before streams go live.

