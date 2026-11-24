#!/usr/bin/env python3
r"""
find_moments.py - Go through a folder of gameplay clips and find the highlights.

In Rocket League the big moments are loud: a goal explosion and the crowd
reaction jump well above normal play. This script listens to every clip, finds
those jumps, and for each one it:

  * cuts a small 360p preview you can watch in seconds,
  * adds a row to a ready-made clips.csv with start and end already filled in,
  * adds a strip of frames to overview images you can skim at a glance.

Then:
  1. Open index.html in the output folder and watch the previews.
  2. In the output folder's clips.csv, delete the rows you don't want and add
     hooks to the ones you keep. Row 1 is preview #1, row 2 is #2, and so on.
  3. Run make_shorts.py with --manifest pointing at that clips.csv.

Usage:
  python find_moments.py --input C:\yt\clips_in --output C:\yt\scout
  python find_moments.py --input C:\yt\clips_in --output C:\yt\scout --top 40 --after 12

Loud isn't the same as good: a demolition or a loud menu can score too, and a
great save is quiet. Treat the list as a shortlist to watch, not a verdict.

Requires ffmpeg + ffprobe on PATH and make_shorts.py in the same folder.
"""

import argparse
import csv
import glob
import html
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_shorts import (VIDEO_EXTS, die, ff_escape_path, find_font,  # noqa: E402
                         need_tool, probe)

STEP = 0.5            # seconds per loudness reading
SILENCE_DB = -120.0   # what "-inf" (digital silence) counts as


# ------------------------------------------------------------------ listening

def loudness_series(path):
    """Return [(time_seconds, rms_db)] at STEP-second resolution."""
    samples = int(8000 * STEP)
    chain = (f"aformat=channel_layouts=mono,aresample=8000,"
             f"asetnsamples=n={samples}:p=0,"
             f"astats=metadata=1:reset=1,"
             f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-af", chain,
         "-f", "null", "-"],
        capture_output=True, text=True, errors="replace")
    series, t = [], None
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("frame:") and "pts_time:" in line:
            try:
                t = float(line.rsplit("pts_time:", 1)[1].split()[0])
            except ValueError:
                t = None
        elif line.startswith("lavfi.astats.Overall.RMS_level=") and t is not None:
            raw = line.split("=", 1)[1]
            try:
                db = float(raw)
            except ValueError:
                db = SILENCE_DB
            if db != db or db < SILENCE_DB:   # nan or -inf
                db = SILENCE_DB
            series.append((t, db))
            t = None
    return series


def find_peaks(series, threshold, min_gap):
    """
    Moments that jump well above their surroundings.

    Each reading is compared with the median of the ~20 seconds around it, so a
    clip with loud music or a noisy lobby still shows its real spikes. Returns
    [(time, jump_db)], strongest first, at least min_gap seconds apart.
    """
    if len(series) < 5:
        return []
    values = [db for _, db in series]
    half = int(10 / STEP)
    scored = []
    for i, (t, db) in enumerate(series):
        if db <= SILENCE_DB:
            continue
        window = [v for v in values[max(0, i - half): i + half + 1] if v > SILENCE_DB]
        if len(window) < 3:
            continue
        jump = db - statistics.median(window)
        if jump >= threshold:
            scored.append((t + STEP / 2, jump))
    scored.sort(key=lambda x: -x[1])
    picked = []
    for t, jump in scored:
        if all(abs(t - pt) >= min_gap for pt, _ in picked):
            picked.append((t, jump))
    return picked


# ------------------------------------------------------------------- outputs

def clean_old(out_dir):
    """Remove files a previous run made, so stale previews never linger."""
    for pattern in ("preview_*.mp4", "poster_*.jpg", "overview_*.jpg", "index.html"):
        for f in glob.glob(os.path.join(out_dir, pattern)):
            os.remove(f)


def make_preview(src, start, length, dst):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", src,
         "-t", f"{length:.3f}", "-vf", "scale=-2:360", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", dst, "-y"],
        check=False)


def make_poster(src, t, dst):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", src,
         "-frames:v", "1", "-vf", "scale=-2:360", "-q:v", "4", dst, "-y"],
        check=False)


def make_strip(src, start, length, dst):
    """Five frames spread across the moment, side by side."""
    rate = 5.0 / max(length, 0.5)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", src, "-vf", f"fps={rate:.5f},scale=192:108,tile=5x1",
         "-frames:v", "1", "-q:v", "4", dst, "-y"],
        check=False)
    return os.path.isfile(dst)


def make_overviews(moments, strips, out_dir, font, tmpdir, per_page=10):
    """Stack labelled strips into overview_1.jpg, overview_2.jpg, ..."""
    pages = []
    items = [(m, s) for m, s in zip(moments, strips) if s]
    for page_no, first in enumerate(range(0, len(items), per_page), start=1):
        chunk = items[first:first + per_page]
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        for _, strip in chunk:
            cmd += ["-i", strip]
        parts = []
        for i, (m, _) in enumerate(chunk):
            label = (f"#{m['rank']}  {m['filename']}  "
                     f"{m['start']:.1f}-{m['end']:.1f}s  +{m['jump']:.0f} dB")
            chain = f"[{i}:v]pad=iw:ih+30:0:30:color=0x15181d"
            if font:
                fd, lf = tempfile.mkstemp(suffix=".txt", dir=tmpdir)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(label)
                chain += (f",drawtext=fontfile='{ff_escape_path(font)}'"
                          f":textfile='{ff_escape_path(lf)}'"
                          f":fontcolor=white:fontsize=17:x=8:y=7")
            parts.append(chain + f"[s{i}]")
        if len(chunk) > 1:
            parts.append("".join(f"[s{i}]" for i in range(len(chunk)))
                         + f"vstack=inputs={len(chunk)}[out]")
            last = "[out]"
        else:
            last = "[s0]"
        dst = os.path.join(out_dir, f"overview_{page_no}.jpg")
        cmd += ["-filter_complex", ";".join(parts), "-map", last,
                "-frames:v", "1", "-q:v", "3", dst, "-y"]
        subprocess.run(cmd, check=False)
        if os.path.isfile(dst):
            pages.append(os.path.basename(dst))
    return pages


def write_manifest(moments, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["filename", "start", "end", "hook", "end_card"])
        for m in moments:
            w.writerow([m["filename"], f"{m['start']:.1f}", f"{m['end']:.1f}", "", ""])


def write_html(moments, pages, quiet, silent, out_dir, manifest_name):
    e = html.escape
    cards = []
    for m in moments:
        cards.append(f"""
      <article>
        <video src="{e(m['preview'])}" poster="{e(m['poster'])}" controls preload="none"></video>
        <div class="meta">
          <b>#{m['rank']}</b>
          <span class="file">{e(m['filename'])}</span>
          <span>{m['start']:.1f}s to {m['end']:.1f}s &middot; {m['end'] - m['start']:.0f}s long &middot; +{m['jump']:.0f} dB</span>
        </div>
      </article>""")
    notes = []
    if quiet:
        notes.append("<p><b>Nothing stood out in:</b> " + ", ".join(e(q) for q in quiet)
                     + ". Check these by hand if you remember something good in them.</p>")
    if silent:
        notes.append("<p><b>No sound, so nothing could be found in:</b> "
                     + ", ".join(e(s) for s in silent) + ".</p>")
    overview_links = " ".join(f'<a href="{e(p)}">{e(p)}</a>' for p in pages)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip Scout</title>
<style>
  :root {{ --bg:#f5f6f8; --card:#fff; --ink:#14171c; --muted:#5d6470; --line:#e2e5ea; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0f1114; --card:#181b20; --ink:#eef0f3; --muted:#9aa1ad; --line:#2a2e35; }}
  }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.5 "Segoe UI", system-ui, sans-serif; }}
  main {{ max-width:1200px; margin:0 auto; padding:24px 16px 48px; }}
  h1 {{ margin:0 0 4px; font-size:1.6rem; }}
  .how {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:12px 16px; margin:16px 0 24px; }}
  .how ol {{ margin:6px 0 0; padding-left:20px; }}
  code {{ background:var(--line); padding:1px 5px; border-radius:4px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:16px; }}
  article {{ background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  video {{ display:block; width:100%; aspect-ratio:16/9; background:#000; }}
  .meta {{ display:grid; gap:2px; padding:10px 12px; font-size:.88rem; }}
  .meta b {{ font-size:1rem; }}
  .file {{ color:var(--muted); overflow-wrap:anywhere; }}
  .notes {{ color:var(--muted); font-size:.9rem; margin-top:24px; }}
</style></head>
<body><main>
  <h1>Clip Scout</h1>
  <p style="margin:0;color:var(--muted)">{len(moments)} moments found, loudest jumps first.</p>
  <div class="how">
    <b>What to do</b>
    <ol>
      <li>Watch the previews below.</li>
      <li>Open <code>{e(manifest_name)}</code> in this folder. Row 1 is #1, row 2 is #2, and so on.
          Delete the rows you don't want, and type a hook into the ones you keep.</li>
      <li>Run <code>make_shorts.py</code> with <code>--manifest</code> pointing at that file.</li>
    </ol>
    <p style="margin:8px 0 0">Frame strips of every moment: {overview_links or "none"}</p>
  </div>
  <section class="grid">{''.join(cards)}
  </section>
  <div class="notes">{''.join(notes)}</div>
</main></body></html>
"""
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Find the highlight moments in a folder of gameplay clips.")
    ap.add_argument("--input", required=True, help="folder of clips to scan")
    ap.add_argument("--output", required=True, help="folder for previews and the shortlist")
    ap.add_argument("--top", type=int, default=25,
                    help="how many moments to keep, loudest first (default: 25)")
    ap.add_argument("--before", type=float, default=8.0,
                    help="seconds to keep before each moment (default: 8)")
    ap.add_argument("--after", type=float, default=4.0,
                    help="seconds to keep after it (default: 4; about 12 includes "
                         "Rocket League's goal replay)")
    ap.add_argument("--threshold", type=float, default=6.0,
                    help="how many dB above its surroundings a moment must jump (default: 6)")
    ap.add_argument("--min-gap", type=float, default=12.0,
                    help="minimum seconds between two moments in one clip (default: 12)")
    args = ap.parse_args()

    for name in ("top", "before", "after", "threshold", "min_gap"):
        if getattr(args, name) <= 0:
            die(f"--{name.replace('_', '-')} must be more than 0")

    need_tool("ffmpeg")
    need_tool("ffprobe")

    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.output)
    if not os.path.isdir(in_dir):
        die(f"--input is not a folder: {in_dir}")
    if in_dir == out_dir:
        die("--input and --output must be different folders")
    os.makedirs(out_dir, exist_ok=True)

    clips = sorted(
        f for f in os.listdir(in_dir)
        if os.path.splitext(f)[1].lower() in VIDEO_EXTS
        and os.path.isfile(os.path.join(in_dir, f))
    )
    if not clips:
        die(f"no video files in {in_dir} (looked for {', '.join(sorted(VIDEO_EXTS))})")

    print(f"listening to {len(clips)} clip(s)...\n")
    found, quiet, silent = [], [], []
    for n, name in enumerate(clips, start=1):
        src = os.path.join(in_dir, name)
        info = probe(src)
        if info is None:
            print(f"  [{n}/{len(clips)}] {name}: unreadable, skipped")
            continue
        dur, _, _, has_audio, _ = info
        if not has_audio:
            silent.append(name)
            print(f"  [{n}/{len(clips)}] {name}: no sound")
            continue
        peaks = find_peaks(loudness_series(src), args.threshold, args.min_gap)
        if not peaks:
            quiet.append(name)
        for t, jump in peaks:
            start = max(0.0, t - args.before)
            end = min(dur, t + args.after) if dur else t + args.after
            if end - start >= 2.0:
                found.append({"filename": name, "src": src, "peak": t,
                              "start": start, "end": end, "jump": jump})
        mins, secs = divmod(int(dur or 0), 60)
        print(f"  [{n}/{len(clips)}] {name} ({mins}:{secs:02d}): {len(peaks)} moment(s)")

    found.sort(key=lambda m: -m["jump"])
    moments = found[:args.top]
    print(f"\n{len(found)} moment(s) found, keeping the top {len(moments)}")
    if not moments:
        print("Nothing stood out. Try a lower --threshold, like 4.")
        sys.exit(0)

    clean_old(out_dir)
    font = find_font()
    tmpdir = tempfile.mkdtemp(prefix="scout_")
    strips = []
    try:
        for rank, m in enumerate(moments, start=1):
            m["rank"] = rank
            m["preview"] = f"preview_{rank:02d}.mp4"
            m["poster"] = f"poster_{rank:02d}.jpg"
            length = m["end"] - m["start"]
            print(f"  preview #{rank}: {m['filename']} {m['start']:.1f}-{m['end']:.1f}s")
            make_preview(m["src"], m["start"], length, os.path.join(out_dir, m["preview"]))
            make_poster(m["src"], m["peak"] - 1.0, os.path.join(out_dir, m["poster"]))
            strip = os.path.join(tmpdir, f"strip_{rank:02d}.jpg")
            strips.append(strip if make_strip(m["src"], m["start"], length, strip) else None)
        pages = make_overviews(moments, strips, out_dir, font, tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    manifest = os.path.join(out_dir, "clips.csv")
    write_manifest(moments, manifest)
    write_html(moments, pages, quiet, silent, out_dir, "clips.csv")

    print(f"\ndone. Open {os.path.join(out_dir, 'index.html')} to watch the previews.")
    print(f"Shortlist ready to edit: {manifest}")


if __name__ == "__main__":
    main()
