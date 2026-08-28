"""Steam storefront broadcast liveness probe.

Anonymous storefront-visibility check, verified 2026-08-28.

Pipeline:
    1. rtmp_key → steamid
       key format: "steam_<accountid>_<hash>"
       steamid = 76561197960265728 + accountid
    2. GET /broadcast/getbroadcastinfo/?steamid=<sid>
       → {"is_online": bool, "appid": str, "app_title": str, ...}
    3. GET /broadcast/getbroadcastmpd/?steamid=<sid>
       → {"success": "ready", "hls_url": "https://.../master.m3u8?...", ...}
    4. GET hls_url (master.m3u8)
       → parse EXT-X-STREAM-INF: BANDWIDTH / RESOLUTION / CODECS

LIVE  = is_online true AND hls manifest parses with stream specs
PUSHED = ffmpeg is transmitting but storefront not (yet) confirmed

Why not the old thumbnail method: the community broadcasts hub page is
session-dependent (broadcaster sees cards, anonymous gets stripped HTML),
so anonymous thumbnail probes produce false negatives. These endpoints
answer anonymously and reflect the real storefront state.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Optional

BASE_STEAMID64 = 76561197960265728
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 15


def steamid_from_rtmp_key(rtmp_key: str) -> Optional[int]:
    """Derive the broadcaster steamid64 from a Steam RTMP key.

    Key format: steam_<accountid>_<hash>  (e.g. steam_132444871_cb1325e...)
    Returns None when the key is not in Steam's format.
    """
    if not isinstance(rtmp_key, str):
        return None
    m = re.match(r"^steam_(\d+)_", rtmp_key.strip())
    if not m:
        return None
    return BASE_STEAMID64 + int(m.group(1))


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "ignore")


def probe_stream(rtmp_key: str) -> dict:
    """Probe one stream's storefront state. Returns a result dict:

        {
          "online": bool,          # is_online from getbroadcastinfo
          "steamid": int|None,
          "appid": str|None,
          "title": str|None,
          "hls_url": str|None,     # when online
          "stream_inf": str|None,  # raw EXT-X-STREAM-INF line when manifest parses
          "bandwidth_kbps": int|None,
          "resolution": str|None,
          "error": str|None,
        }
    """
    result = {
        "online": False,
        "steamid": None,
        "appid": None,
        "title": None,
        "hls_url": None,
        "stream_inf": None,
        "bandwidth_kbps": None,
        "resolution": None,
        "error": None,
    }

    sid = steamid_from_rtmp_key(rtmp_key)
    result["steamid"] = sid
    if sid is None:
        result["error"] = "invalid rtmp_key format"
        return result

    try:
        info = _get_json(f"https://steamcommunity.com/broadcast/getbroadcastinfo/?steamid={sid}")
    except Exception as e:
        result["error"] = f"getbroadcastinfo: {e}"
        return result

    result["appid"] = info.get("appid")
    result["title"] = info.get("app_title")
    result["online"] = bool(info.get("is_online", False))
    if not result["online"]:
        return result

    try:
        mpd = _get_json(f"https://steamcommunity.com/broadcast/getbroadcastmpd/?steamid={sid}")
    except Exception as e:
        result["error"] = f"getbroadcastmpd: {e}"
        return result

    hls = mpd.get("hls_url")
    result["hls_url"] = hls
    if not hls:
        result["error"] = "no hls_url in getbroadcastmpd"
        return result

    try:
        master = _get_text(hls)
    except Exception as e:
        result["error"] = f"manifest fetch: {e}"
        return result

    m = re.search(r"#EXT-X-STREAM-INF:([^\n]+)", master)
    if m:
        inf = m.group(1)
        result["stream_inf"] = inf
        bm = re.search(r"BANDWIDTH=(\d+)", inf)
        if bm:
            result["bandwidth_kbps"] = int(bm.group(1)) // 1000
        rm = re.search(r"RESOLUTION=(\d+x\d+)", inf)
        if rm:
            result["resolution"] = rm.group(1)

    return result


def probe_with_retries(rtmp_key: str, attempts: int = 3, delay: float = 10.0) -> dict:
    """Probe with benefit-of-the-doubt retries (storefront lags ingest).

    Steam's storefront can take several seconds after RTMP connect before
    is_online flips true. Wait `delay` seconds between attempts so fresh
    streams aren't falsely flagged offline.
    """
    last = probe_stream(rtmp_key)
    for _ in range(max(0, attempts - 1)):
        if last.get("online"):
            return last
        time.sleep(delay)
        last = probe_stream(rtmp_key)
    return last
