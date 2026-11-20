"""
End-to-end checks for make_shorts.py: trim lengths and end-card timing.

Generates its own sample clips with ffmpeg, so it needs ffmpeg/ffprobe on PATH
and nothing else.

  python tests/test_make_shorts.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MK = os.path.join(HERE, "..", "make_shorts.py")

fails = 0


def check(name, ok, info=""):
    global fails
    if not ok:
        fails += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {info}")


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", *args, "-y"], check=True)


def mk(inp, out, *args):
    r = subprocess.run([sys.executable, MK, "--input", inp, "--output", out,
                        "--no-thumbs", *args], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout, r.stderr)
        sys.exit("make_shorts failed")


def dur(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path]).strip())


def frame(path, t):
    """Tiny grayscale frame at time t, as raw bytes."""
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
         "-vf", "scale=108:192,format=gray", "-f", "rawvideo", "-"],
        capture_output=True).stdout


def diff(a, b, t):
    """Mean per-pixel difference between two renders at time t."""
    fa, fb = frame(a, t), frame(b, t)
    assert fa and fb, f"no frame at {t}s"
    return sum(abs(x - y) for x, y in zip(fa, fb)) / len(fa)


def main():
    work = tempfile.mkdtemp(prefix="mkshorts_test_")
    try:
        inp = os.path.join(work, "in")
        os.makedirs(inp)
        # A 20s 1080p clip with audio, like a PS5 capture, and a 5s silent one.
        ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=20",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", os.path.join(inp, "long.mp4"))
        ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=5",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           os.path.join(inp, "silent.mp4"))

        card_csv = os.path.join(work, "card.csv")
        base_csv = os.path.join(work, "base.csv")
        with open(card_csv, "w") as fh:
            fh.write("filename,start,end,hook,end_card\n"
                     "long.mp4,3,15,Plat 1 air dribble,\n"
                     "silent.mp4,,,,none\n")
        with open(base_csv, "w") as fh:
            fh.write("filename,start,end,hook\n"
                     "long.mp4,3,15,Plat 1 air dribble\n"
                     "silent.mp4,,,\n")

        print("trimmed clip (start 3s, end 15s) with the default end card")
        c, b = os.path.join(work, "card"), os.path.join(work, "base")
        mk(inp, c, "--manifest", card_csv, "--end-card", "worth a sub?")
        mk(inp, b, "--manifest", base_csv)
        cp, bp = os.path.join(c, "long_short.mp4"), os.path.join(b, "long_short.mp4")
        length = dur(cp)
        check("length is end - start (12s)", abs(length - 12) < 0.15, f"({length:.2f}s)")
        t0 = length - 1.5
        check("no card 0.4s before it's due", diff(cp, bp, t0 - 0.4) < 0.5)
        check("card shows 0.6s after it's due", diff(cp, bp, t0 + 0.6) > 3)
        check("no card early in the Short", diff(cp, bp, 1.0) < 0.5)

        print("'none' in the end_card column")
        sp, sb = os.path.join(c, "silent_short.mp4"), os.path.join(b, "silent_short.mp4")
        worst = max(diff(sp, sb, t) for t in (0.5, dur(sp) - 0.8, dur(sp) - 0.2))
        check("never shows a card", worst < 0.5)

        print("length capped by --max-dur 8")
        c2, b2 = os.path.join(work, "cap_card"), os.path.join(work, "cap_base")
        mk(inp, c2, "--max-dur", "8", "--end-card", "worth a sub?")
        mk(inp, b2, "--max-dur", "8")
        cp, bp = os.path.join(c2, "long_short.mp4"), os.path.join(b2, "long_short.mp4")
        check("length is 8s", abs(dur(cp) - 8) < 0.15)
        check("no card at 6.1s", diff(cp, bp, 6.1) < 0.5)
        check("card at 7.1s", diff(cp, bp, 7.1) > 3)

        print("bad --end-card-secs")
        r = subprocess.run([sys.executable, MK, "--input", inp, "--output",
                            os.path.join(work, "x"), "--end-card-secs", "0"],
                           capture_output=True, text=True)
        check("rejected with a clear message",
              r.returncode == 1 and "must be more than 0" in r.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\nALL PASSED" if not fails else f"\n{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
