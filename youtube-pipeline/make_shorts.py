#!/usr/bin/env python3
"""
make_shorts.py - Batch-convert PS5 gameplay captures into YouTube Shorts.

Takes a folder of landscape PS5 clips and produces 1080x1920 vertical clips with
loudness-normalized audio, an optional burned-in hook line, and a thumbnail frame.

Three framing modes (try all three on one real clip before committing):
  crop  full-bleed centre crop. Biggest action, keeps only ~32% of the width, so
        minimap / killfeed / ammo are gone.
  zoom  centre square over a blurred fill. Action ~1.8x bigger than fit, keeps
        ~56% of the width. Usually the best compromise for shooters.
  fit   whole frame over a blurred fill. Full HUD, but the action is small.

Usage:
  python make_shorts.py --input clips_in --output shorts_out
  python make_shorts.py --input clips_in --output shorts_out --manifest clips.csv
  python make_shorts.py --input clips_in --output shorts_out --mode fit --max-dur 30

Manifest (CSV, header required). Only 'filename' is mandatory:
  filename,start,end,hook
  cod_clip_01.mp4,00:00:12,00:00:33,4 kills with the wrong gun
  apex_clip_07.webm,,,Nobody saw this coming

  start/end  ffmpeg timestamps (HH:MM:SS or seconds). Blank = clip start/end.
  hook       text burned across the top. Blank = no text.

Requires ffmpeg + ffprobe on PATH. Python 3.8+. No third-party packages.
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}

# Vertical canvas. 1080x1920 is the Shorts native frame.
OUT_W, OUT_H = 1080, 1920

FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\impact.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def need_tool(name):
    path = shutil.which(name)
    if not path:
        die(
            f"{name} not found on PATH.\n"
            f"  Windows: winget install Gyan.FFmpeg   (then reopen your terminal)\n"
            f"  macOS:   brew install ffmpeg"
        )
    return path


def find_font(explicit=None):
    """Locate a bold TTF for drawtext."""
    if explicit:
        if not os.path.isfile(explicit):
            die(f"--font not found: {explicit}")
        return explicit
    for cand in FONT_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def ff_escape_path(path):
    """
    Escape a filesystem path for use *inside* an ffmpeg filter graph.

    Filter graphs treat ':' as an option separator and '\' as an escape, so a
    Windows path like C:\\Windows\\Fonts\\arialbd.ttf has to become
    C\\:/Windows/Fonts/arialbd.ttf before it can be embedded.
    """
    p = path.replace("\\", "/")
    p = p.replace(":", r"\:")
    return p


def probe(path):
    """Return (duration_seconds, width, height, has_audio)."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            path,
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    try:
        meta = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None

    dur = 0.0
    try:
        dur = float(meta.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        pass

    width = height = 0
    has_audio = False
    for st in meta.get("streams", []):
        if st.get("codec_type") == "video" and not width:
            width = int(st.get("width") or 0)
            height = int(st.get("height") or 0)
            if not dur:
                try:
                    dur = float(st.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
        elif st.get("codec_type") == "audio":
            has_audio = True
    return dur, width, height, has_audio


def parse_ts(value):
    """
    Parse a timestamp into seconds. Accepts SS, SS.ss, MM:SS, HH:MM:SS(.ss).
    Returns None for blank input. Raises ValueError on garbage.
    """
    value = (value or "").strip()
    if not value:
        return None
    parts = value.split(":")
    if len(parts) > 3:
        raise ValueError(value)
    secs = 0.0
    try:
        for part in parts:
            secs = secs * 60 + float(part)
    except ValueError:
        raise ValueError(value) from None
    if secs < 0:
        raise ValueError(value)
    return secs


def wrap_hook(text, max_lines=3):
    """
    Wrap hook text to fit the 1080px canvas. drawtext never wraps on its own, so
    an unwrapped hook overflows and loses its first and last words off-screen.

    Picks the largest font size whose wrapped result fits in max_lines.
    Returns (wrapped_text, fontsize).
    """
    import textwrap

    usable = OUT_W - 2 * 70          # side margins
    # Bold sans glyphs average ~0.58 x fontsize wide; 0.62 leaves headroom for
    # wide characters (W, M, digits) so lines don't clip at the edges.
    char_w = 0.62
    for size in (84, 76, 68, 60, 52):
        per_line = max(8, int(usable / (char_w * size)))
        lines = textwrap.wrap(text, width=per_line, break_long_words=True)
        if len(lines) <= max_lines:
            return "\n".join(lines), size
    # Still too long at the smallest size: keep the first max_lines and mark it.
    size = 52
    per_line = max(8, int(usable / (char_w * size)))
    lines = textwrap.wrap(text, width=per_line, break_long_words=True)[:max_lines]
    lines[-1] = lines[-1].rstrip(" .,;:") + "..."
    return "\n".join(lines), size


def build_video_filter(mode, hook, font, hook_file, fontsize=72):
    """
    Build the video filter chain.

    Returns (filter_string, uses_complex). 'fit' and 'zoom' need filter_complex
    because they composite the gameplay over a blurred copy of itself.
    """
    draw = ""
    if hook and font:
        # textfile= avoids escaping the hook text itself, which commonly carries
        # apostrophes, colons and commas that would otherwise break the graph.
        draw = (
            f",drawtext=fontfile='{ff_escape_path(font)}'"
            f":textfile='{ff_escape_path(hook_file)}'"
            f":fontcolor=white:fontsize={fontsize}:line_spacing=14"
            f":borderw=8:bordercolor=black@0.95"
            f":x=(w-text_w)/2:y=170"
            f":text_align=center"
        )

    if mode == "crop":
        # Centre-crop to 9:16 off the source height, then scale to canvas.
        chain = (
            f"crop=ih*9/16:ih,"
            f"scale={OUT_W}:{OUT_H}:flags=lanczos,"
            f"setsar=1"
            f"{draw}"
        )
        return chain, False

    # 'fit' shows the whole frame; 'zoom' centre-crops to a square first, so the
    # action is ~1.8x larger than fit while keeping ~56% of the width (vs ~32%
    # for crop). For shooters that usually keeps the crosshair area readable on
    # a phone without throwing away everything around it.
    fg_src = "[0:v]crop=ih:ih," if mode == "zoom" else "[0:v]"
    chain = (
        f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},boxblur=24:2,setsar=1[bg];"
        f"{fg_src}scale={OUT_W}:-2:flags=lanczos,setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2{draw}"
    )
    return chain, True


def convert(src, dst, mode, hook, font, max_dur, lufs, start, end,
            crf, thumbs_dir, tmpdir, dry_run):
    info = probe(src)
    if info is None:
        print(f"  skip (unreadable): {os.path.basename(src)}")
        return False
    dur, width, height, has_audio = info
    if not width or not height:
        print(f"  skip (no video stream): {os.path.basename(src)}")
        return False

    label = os.path.basename(src)
    try:
        start_s = parse_ts(start)
        end_s = parse_ts(end)
    except ValueError as exc:
        print(f"  skip (bad timestamp '{exc}'): {label}")
        return False
    if start_s is not None and end_s is not None and end_s <= start_s:
        print(f"  skip (end {end} is not after start {start}): {label}")
        return False
    if start_s is not None and dur and start_s >= dur:
        print(f"  skip (start {start} is past clip end {dur:.1f}s): {label}")
        return False

    # Manifest start/end are positions in the SOURCE clip. An input-side -ss
    # resets timestamps to zero, so a raw '-to end' would be read as a duration
    # and overshoot by 'start'. Convert to an explicit length instead.
    length = None
    if end_s is not None:
        length = end_s - (start_s or 0.0)
    elif max_dur:
        length = max_dur

    # Hook text goes to a sidecar file so drawtext never has to parse it.
    hook_file = None
    fontsize = 72
    if hook and font:
        wrapped, fontsize = wrap_hook(hook)
        fd, hook_file = tempfile.mkstemp(suffix=".txt", dir=tmpdir)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(wrapped)

    vf, complex_mode = build_video_filter(mode, hook, font, hook_file, fontsize)

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats"]

    # -ss before -i seeks fast; -t after -i is the output length.
    if start_s:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", src]
    if length:
        cmd += ["-t", f"{length:.3f}"]

    if complex_mode:
        cmd += ["-filter_complex", vf]
    else:
        cmd += ["-vf", vf]

    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.1",
        "-r", "30",
        "-movflags", "+faststart",
    ]

    if has_audio:
        # EBU R128 to YouTube's playback target, so clips don't jump in volume.
        cmd += ["-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        cmd += ["-an"]

    cmd += [dst, "-y"]

    if dry_run:
        print(f"  [dry-run] {label} -> {os.path.basename(dst)}")
        print(f"            {' '.join(cmd)}")
        return True

    print(f"  {label} ({width}x{height}, {dur:.1f}s) -> {os.path.basename(dst)}")
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"  FAILED: {label}", file=sys.stderr)
        return False

    if thumbs_dir:
        stem = os.path.splitext(os.path.basename(dst))[0]
        thumb = os.path.join(thumbs_dir, f"{stem}.jpg")
        # Grab a frame ~1s in; frame 0 is often a fade-in or black.
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-ss", "1", "-i", dst, "-frames:v", "1",
             "-q:v", "2", thumb, "-y"],
            check=False,
        )
    return True


def load_manifest(path):
    """Return {lowercased filename: {start, end, hook}}."""
    rows = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "filename" not in [
            (f or "").strip().lower() for f in reader.fieldnames
        ]:
            die(f"{path}: CSV needs a 'filename' column. Found: {reader.fieldnames}")
        for raw in reader:
            row = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in raw.items()}
            name = row.get("filename", "")
            if not name:
                continue
            rows[name.lower()] = {
                "start": row.get("start", ""),
                "end": row.get("end", ""),
                "hook": row.get("hook", ""),
            }
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="Batch-convert PS5 captures into YouTube Shorts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True, help="folder of PS5 clips")
    ap.add_argument("--output", required=True, help="folder for finished Shorts")
    ap.add_argument("--manifest", help="CSV with per-clip start/end/hook")
    ap.add_argument("--mode", choices=["crop", "zoom", "fit"], default="zoom",
                    help="framing mode (default: zoom)")
    ap.add_argument("--max-dur", type=float, default=45.0,
                    help="hard cap in seconds when no end time given (default: 45; 0 disables)")
    ap.add_argument("--lufs", type=float, default=-14.0,
                    help="integrated loudness target (default: -14)")
    ap.add_argument("--crf", type=int, default=19,
                    help="x264 quality, lower is better (default: 19)")
    ap.add_argument("--font", help="path to a bold .ttf for hook text")
    ap.add_argument("--no-thumbs", action="store_true", help="skip thumbnail frames")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the ffmpeg commands without encoding")
    args = ap.parse_args()

    need_tool("ffmpeg")
    need_tool("ffprobe")

    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.output)
    if not os.path.isdir(in_dir):
        die(f"--input is not a folder: {in_dir}")
    if os.path.abspath(in_dir) == os.path.abspath(out_dir):
        die("--input and --output must be different folders")
    os.makedirs(out_dir, exist_ok=True)

    thumbs_dir = None
    if not args.no_thumbs:
        thumbs_dir = os.path.join(out_dir, "thumbs")
        os.makedirs(thumbs_dir, exist_ok=True)

    manifest = load_manifest(args.manifest) if args.manifest else {}

    font = find_font(args.font)
    wants_text = any(r["hook"] for r in manifest.values())
    if wants_text and not font:
        print("warning: no bold font found, hook text will be skipped. "
              "Pass --font C:\\Windows\\Fonts\\arialbd.ttf", file=sys.stderr)

    clips = sorted(
        f for f in os.listdir(in_dir)
        if os.path.splitext(f)[1].lower() in VIDEO_EXTS
        and os.path.isfile(os.path.join(in_dir, f))
    )
    if not clips:
        die(f"no video files in {in_dir} (looked for {', '.join(sorted(VIDEO_EXTS))})")

    # A manifest acts as a whitelist: only listed clips get built.
    if manifest:
        clips = [c for c in clips if c.lower() in manifest]
        missing = set(manifest) - {c.lower() for c in clips}
        for m in sorted(missing):
            print(f"warning: manifest lists '{m}' but it is not in {in_dir}",
                  file=sys.stderr)
        if not clips:
            die("manifest matched none of the files in --input")

    print(f"{len(clips)} clip(s), mode={args.mode}, target {OUT_W}x{OUT_H}\n")

    tmpdir = tempfile.mkdtemp(prefix="mkshorts_")
    ok = 0
    try:
        for name in clips:
            src = os.path.join(in_dir, name)
            stem = os.path.splitext(name)[0]
            dst = os.path.join(out_dir, f"{stem}_short.mp4")
            row = manifest.get(name.lower(), {})
            if convert(
                src=src, dst=dst, mode=args.mode,
                hook=row.get("hook", ""), font=font,
                max_dur=(args.max_dur if args.max_dur > 0 else None),
                lufs=args.lufs,
                start=row.get("start", ""), end=row.get("end", ""),
                crf=args.crf, thumbs_dir=thumbs_dir,
                tmpdir=tmpdir, dry_run=args.dry_run,
            ):
                ok += 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\ndone: {ok}/{len(clips)} -> {out_dir}")
    if ok < len(clips):
        sys.exit(1)


if __name__ == "__main__":
    main()
