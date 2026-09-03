#!/usr/bin/env python3
"""repair_audio.py — fill AAC holes + pad audio to video duration.

Usage: repair_audio.py <file.mp4> [out.mp4]   (default out: <file>.fixed.mp4)

Strategy: scan audio packet PTS, rebuild the audio track as
[real regions] + [generated silence for each hole] + [silence to video end],
video stream copied untouched, audio normalized to AAC 128k 44.1k stereo.
"""
import json, subprocess, sys

HOLE_S = 0.5          # gaps > this are holes
FRAME_S = 0.03        # tolerance for trailing packet coverage

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}... failed:\n{r.stderr[-2000:]}")
    return r.stdout

def audio_pts(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
               "-show_entries", "packet=pts_time", "-of", "json", path])
    pts = [float(p["pts_time"]) for p in json.loads(out).get("packets", [])
           if p.get("pts_time") is not None]
    return sorted(pts)

def video_dur(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration", "-of", "json", path])
    return float(json.loads(out)["format"]["duration"])

def audio_start_end(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
               "-show_entries", "stream=start_time,duration",
               "-of", "json", path])
    s = json.loads(out)["streams"][0]
    return float(s.get("start_time", 0) or 0), float(s.get("duration", 0) or 0)

def find_holes(pts, vdur):
    """Regions (audio present) and holes (silence needed) over [0, vdur]."""
    if not pts:
        return [], [(0.0, vdur)]
    regions, holes = [], []
    cur_start = pts[0]
    prev = pts[0]
    # leading hole before first packet
    if pts[0] > HOLE_S:
        holes.append((0.0, pts[0]))
    for p in pts[1:]:
        if p - prev > HOLE_S:
            regions.append((cur_start, prev + FRAME_S))
            holes.append((prev + FRAME_S, p))
            cur_start = p
        prev = p
    aend = min(pts[-1] + FRAME_S, vdur)
    regions.append((cur_start, aend))
    if vdur - aend > HOLE_S:
        holes.append((aend, vdur))
    return regions, holes

def build_filter(regions, holes):
    parts, labels, n = [], [], 0
    for s, e in regions:
        if e - s <= 0.01:
            continue
        lab = f"r{n}"; n += 1
        parts.append(
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=44100:channel_layouts=stereo[{lab}]")
        labels.append(lab)
    for s, e in holes:
        if e - s <= 0.01:
            continue
        lab = f"s{n}"; n += 1
        parts.append(
            f"anullsrc=r=44100:cl=stereo,atrim=duration={e - s:.3f},"
            f"asetpts=PTS-STARTPTS[{lab}]")
        labels.append(lab)
    parts.append("".join(f"[{l}]" for l in labels) +
                 f"concat=n={len(labels)}:v=0:a=1[outa]")
    return ";".join(parts), labels

def scan_report(path, label):
    vdur = video_dur(path)
    pts = audio_pts(path)
    regions, holes = find_holes(pts, vdur)
    h = [(round(a, 1), round(b, 1), round(b - a, 1)) for a, b in holes]
    print(f"{label}: video={vdur:.1f}s audio_pkts={len(pts)} "
          f"holes={h if h else 'NONE'}")
    return holes

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    src, dst = argv[0], argv[1] if len(argv) > 1 else argv[0].rsplit(".", 1)[0] + ".fixed.mp4"
    vdur = video_dur(src)
    pts = audio_pts(src)
    regions, holes = find_holes(pts, vdur)
    print(f"src  : {src}")
    scan_report(src, "before")
    if not holes:
        print("no holes — nothing to do")
        return 0
    fc, labels = build_filter(regions, holes)
    if not labels:
        print("no audio to rebuild")
        return 1
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src,
           "-filter_complex", fc,
           "-map", "0:v:0", "-c:v", "copy",
           "-map", "[outa]", "-c:a", "aac", "-b:a", "128k",
           "-ar", "44100", "-ac", "2",
           "-movflags", "+faststart", dst]
    run(cmd)
    scan_report(dst, "after ")
    # verify: no holes remain, audio reaches video end
    pts2 = audio_pts(dst)
    _, holes2 = find_holes(pts2, video_dur(dst))
    if holes2:
        print(f"FAIL: {len(holes2)} hole(s) remain"); return 1
    print(f"OK   : {dst}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
