"""
End-to-end checks for find_moments.py, and for feeding its shortlist into
make_shorts.py (including several Shorts cut from one clip).

Builds its own sample clips with loud bursts at known times, so it needs
ffmpeg/ffprobe on PATH and nothing else.

  python tests/test_find_moments.py
"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
FIND = os.path.join(ROOT, "find_moments.py")
MK = os.path.join(ROOT, "make_shorts.py")

fails = 0


def check(name, ok, info=""):
    global fails
    if not ok:
        fails += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {info}")


def clip(path, seconds, audio_expr=None):
    """Test-pattern video, optionally with an aevalsrc sound track."""
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
           f"testsrc2=size=1280x720:rate=30:duration={seconds}"]
    if audio_expr:
        cmd += ["-f", "lavfi", "-i", f"aevalsrc='{audio_expr}':s=48000:d={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio_expr:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run(cmd + [path, "-y"], check=True)


def dur(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path]).strip())


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def main():
    work = tempfile.mkdtemp(prefix="scout_test_")
    try:
        inp = os.path.join(work, "clips_in")
        out = os.path.join(work, "scout")
        os.makedirs(inp)
        # Quiet play with two loud "goals" at 10s and 30s.
        clip(os.path.join(inp, "game1.mp4"), 40,
             "0.03*sin(2*PI*220*t)+if(between(t,10,10.8)+between(t,30,30.8),0.9*sin(2*PI*440*t),0)")
        # Loud background music with one moment on top of it at 20s.
        clip(os.path.join(inp, "music.mp4"), 30,
             "0.25*sin(2*PI*220*t)+if(between(t,20,20.8),0.7*sin(2*PI*440*t),0)")
        # Nothing happens.
        clip(os.path.join(inp, "quiet.mp4"), 15, "0.03*sin(2*PI*220*t)")
        # No sound track at all.
        clip(os.path.join(inp, "silent.mp4"), 8)

        print("scanning")
        r = run(FIND, "--input", inp, "--output", out)
        check("runs cleanly", r.returncode == 0, r.stderr.strip())

        with open(os.path.join(out, "clips.csv"), newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        found = sorted((row["filename"], float(row["start"]), float(row["end"])) for row in rows)
        names = [f[0] for f in found]
        check("finds exactly 3 moments", len(found) == 3, str(found))
        check("both goals in game1 found", names.count("game1.mp4") == 2)
        check("finds the moment over loud music", names.count("music.mp4") == 1)
        check("quiet and silent clips give nothing",
              "quiet.mp4" not in names and "silent.mp4" not in names)

        # Each window is 8s before the peak to 4s after it.
        g = [f for f in found if f[0] == "game1.mp4"]
        check("game1 first window ~2-14s", abs(g[0][1] - 2.25) < 0.6 and abs(g[0][2] - 14.25) < 0.6, str(g[0]))
        check("game1 second window ~22-34s", abs(g[1][1] - 22.25) < 0.6 and abs(g[1][2] - 34.25) < 0.6, str(g[1]))
        mus = [f for f in found if f[0] == "music.mp4"][0]
        check("music window ~12-24s", abs(mus[1] - 12.25) < 0.6 and abs(mus[2] - 24.25) < 0.6, str(mus))
        check("loudest jumps ranked first",
              rows[0]["filename"] == "game1.mp4" and rows[2]["filename"] == "music.mp4",
              str([r["filename"] for r in rows]))

        previews = sorted(f for f in os.listdir(out) if f.startswith("preview_"))
        check("one preview per moment", previews == ["preview_01.mp4", "preview_02.mp4", "preview_03.mp4"],
              str(previews))
        check("previews are the right length", abs(dur(os.path.join(out, "preview_01.mp4")) - 12) < 0.3)
        check("overview image made", os.path.isfile(os.path.join(out, "overview_1.jpg")))
        page = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        check("page lists all 3 and the quiet/silent clips",
              page.count("<video") == 3 and "quiet.mp4" in page and "silent.mp4" in page)

        print("re-running cleans up old previews")
        r = run(FIND, "--input", inp, "--output", out, "--top", "1")
        previews = sorted(f for f in os.listdir(out) if f.startswith("preview_"))
        check("only the new preview remains", previews == ["preview_01.mp4"], str(previews))
        run(FIND, "--input", inp, "--output", out)  # back to the full list

        print("feeding the shortlist to make_shorts")
        shorts = os.path.join(work, "shorts_out")
        r = run(MK, "--input", inp, "--output", shorts, "--no-thumbs",
                "--manifest", os.path.join(out, "clips.csv"), "--end-card", "worth a sub?")
        check("make_shorts runs cleanly", r.returncode == 0, r.stderr.strip()[-300:])
        made = sorted(os.listdir(shorts))
        check("two Shorts from game1, one from music",
              made == ["game1_short_1.mp4", "game1_short_2.mp4", "music_short.mp4"], str(made))
        check("each Short is the moment's length",
              all(abs(dur(os.path.join(shorts, f)) - 12) < 0.3 for f in made))

        print("bad options")
        r = run(FIND, "--input", inp, "--output", out, "--top", "0")
        check("rejects --top 0", r.returncode == 1 and "must be more than 0" in r.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\nALL PASSED" if not fails else f"\n{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
