"""
Exercise upload_youtube.py end to end against a fake YouTube service.
No network, no credentials, no real video files.

  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
  python tests/test_upload.py
"""
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile

import httplib2
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import upload_youtube as up

up.time.sleep = lambda s: None  # don't actually back off in tests

# --- fixtures: a throwaway folder with dummy "videos" and a manifest -------------
# The uploader only needs the files to exist; the fake service never reads them.
WORK = tempfile.mkdtemp(prefix="yt_upload_test_")
os.chdir(WORK)
for name in ("fake_ps5_clip_short.mp4", "apex 4k clip_short.mp4",
             "silent_clip_short.mp4", "thumb.jpg"):
    with open(name, "wb") as fh:
        fh.write(b"\0" * 1024)
# Schedule 30 days out so the test never goes stale.
SCHED = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).replace(
    hour=17, minute=0, second=0, microsecond=0)
SCHED_STR = SCHED.strftime("%Y-%m-%dT%H:%M:%SZ")
with open("good.csv", "w", encoding="utf-8") as fh:
    fh.write("file,title,description,tags,privacy,publish_at,thumbnail\n")
    fh.write("fake_ps5_clip_short.mp4,4 kills 1 mag #shorts,Clean clip.,cod|warzone|clutch,public,,thumb.jpg\n")
    fh.write(f"apex 4k clip_short.mp4,Nobody saw this coming #shorts,,apex|apex legends,unlisted,{SCHED_STR},\n")
    fh.write("silent_clip_short.mp4,Silent one #shorts,,,private,,\n")


def http_error(status, reason):
    resp = httplib2.Response({"status": status})
    resp.reason = reason
    body = json.dumps({"error": {"errors": [{"reason": reason}], "code": status}})
    return HttpError(resp, body.encode())


class FakeStatus:
    def progress(self):
        return 0.5


class FakeRequest:
    def __init__(self, script):
        self.script = list(script)  # items: Exception to raise, or dict response

    def next_chunk(self):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if step == "progress":
            return FakeStatus(), None
        return None, step


class FakeExec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeYouTube:
    """
    plan: list of per-upload scripts for next_chunk.
    readback: function(video_id, requested_body) -> status dict.
    """

    def __init__(self, plan, readback=None):
        self.plan = list(plan)
        self.readback = readback or (lambda vid, body: body["status"])
        self.bodies = {}
        self.inserted = []
        self.thumbs = []
        self.n = 0

    def videos(self):
        return self

    def thumbnails(self):
        yt = self

        class T:
            def set(self, videoId, media_body):
                yt.thumbs.append(videoId)
                return FakeExec({})
        return T()

    def insert(self, part, body, media_body):
        self.n += 1
        vid = f"VID{self.n:03d}"
        script = self.plan.pop(0)
        # Final dict response gets the id + echo of requested status.
        script = [dict(s, id=vid, status=body["status"]) if isinstance(s, dict) else s
                  for s in script]
        self.bodies[vid] = body
        self.inserted.append((vid, body["snippet"]["title"]))
        return FakeRequest(script)

    def list(self, part, id):
        return FakeExec({"items": [{"status": self.readback(id, self.bodies[id])}]})


def run(fake, extra_args, state="uploaded.json"):
    up.get_service = lambda cs, tok: fake
    sys.argv = ["upload_youtube.py", "--csv", "good.csv", "--state", state] + extra_args
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            up.main()
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


def state_ids(path="uploaded.json"):
    if not os.path.exists(path):
        return []
    return sorted(v["video_id"] for v in json.load(open(path)).values())


def reset(path="uploaded.json"):
    if os.path.exists(path):
        os.remove(path)


OK = ["progress", {}]
failures = 0


def check(name, cond, detail=""):
    global failures
    mark = "PASS" if cond else "FAIL"
    if not cond:
        failures += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- 1. happy path
print("1. happy path, then re-run is a no-op")
reset()
fake = FakeYouTube([OK, OK, OK])
code, out, err = run(fake, [])
check("exit 0", code == 0, f"code={code} err={err}")
check("3 uploads recorded", state_ids() == ["VID001", "VID002", "VID003"], state_ids())
check("thumbnail set on row 1 only", fake.thumbs == ["VID001"], fake.thumbs)
sched = fake.bodies["VID002"]["status"]
check("scheduled row sent as private + publishAt",
      sched.get("privacyStatus") == "private" and sched.get("publishAt") == SCHED_STR, sched)
check("category is Gaming (20)", fake.bodies["VID001"]["snippet"]["categoryId"] == "20")
check("made-for-kids explicitly false", fake.bodies["VID001"]["status"]["selfDeclaredMadeForKids"] is False)
fake2 = FakeYouTube([])
code, out, err = run(fake2, [])
check("re-run uploads nothing", fake2.n == 0 and "nothing to do" in out, out)

# ------------------------------------------------------- 2. quota mid-batch
print("2. quota exceeded on upload #2, then resume")
reset()
fake = FakeYouTube([OK, [http_error(403, "quotaExceeded")]])
code, out, err = run(fake, [])
check("exit 2 (stopped)", code == 2, f"code={code}")
check("only #1 recorded", state_ids() == ["VID001"], state_ids())
check("message says run again tomorrow", "tomorrow" in err, err)
check("never attempted #3", fake.n == 2, fake.n)
fake = FakeYouTube([OK, OK])
code, out, err = run(fake, [])
titles = [t for _, t in fake.inserted]
check("resume uploads exactly the 2 remaining",
      titles == ["Nobody saw this coming #shorts", "Silent one #shorts"], titles)
check("state now holds 3", len(state_ids()) == 3, state_ids())

# ----------------------------------------- 3. privacy forced to private (canary)
print("3. YouTube forces uploads private -> stop after the first")
reset()
locked = lambda vid, body: {"privacyStatus": "private"}
fake = FakeYouTube([OK, OK, OK], readback=locked)
code, out, err = run(fake, [])
check("exit 2 (stopped)", code == 2, f"code={code}")
check("stopped after exactly 1 upload", fake.n == 1, fake.n)
check("the 1 upload is still recorded (no re-upload later)", state_ids() == ["VID001"], state_ids())
check("explains unaudited project", "unaudited" in err, err)

# ------------------------------------------- 4. dropped schedule is also caught
print("4. scheduled time silently dropped -> stop")
reset()
# Mark row 1 done so the scheduled row is first up.
json.dump({up.state_key("fake_ps5_clip_short.mp4"): {"video_id": "PRE", "title": "x"}},
          open("uploaded.json", "w"))
dropped = lambda vid, body: {"privacyStatus": "private"}  # no publishAt
fake = FakeYouTube([OK, OK], readback=dropped)
code, out, err = run(fake, [])
check("exit 2", code == 2, f"code={code}")
check("reports dropped schedule", "was dropped" in err, err)

# --------------------------------------------------- 5. transient 503 retried
print("5. transient 503 twice, then success")
reset()
fake = FakeYouTube([[http_error(503, "backendError"), http_error(503, "backendError")] + OK, OK, OK])
code, out, err = run(fake, [])
check("exit 0", code == 0, f"code={code} err={err}")
check("all 3 recorded", len(state_ids()) == 3, state_ids())
check("retries were logged", out.count("retry") == 2, out)

# --------------------------------------------- 6. one bad row doesn't sink batch
print("6. HTTP 400 on one row -> skip it, continue")
reset()
fake = FakeYouTube([OK, [http_error(400, "invalidTitle")], OK])
code, out, err = run(fake, [])
check("exit 1 (something failed)", code == 1, f"code={code}")
check("2 of 3 recorded", state_ids() == ["VID001", "VID003"], state_ids())
check("failure reported", "invalidTitle" in out, out)
check("summary line counts the failure", "1 FAILED" in err, err)
fake = FakeYouTube([OK])
code, out, err = run(fake, [])
check("re-run retries only the failed row",
      [t for _, t in fake.inserted] == ["Nobody saw this coming #shorts"], fake.inserted)

# ------------------------------------------------------------------ 7. --limit
print("7. --limit 1 canary")
reset()
fake = FakeYouTube([OK, OK, OK])
code, out, err = run(fake, ["--limit", "1"])
check("exactly 1 uploaded", fake.n == 1 and len(state_ids()) == 1, fake.n)

# --------------------------------------------- 8. retries exhausted -> skip row
print("8. 503 forever -> give up on that row after MAX_RETRIES")
reset()
forever = [http_error(503, "backendError")] * (up.MAX_RETRIES + 1)
fake = FakeYouTube([forever, OK, OK])
code, out, err = run(fake, [])
check("row 1 skipped, others uploaded", state_ids() == ["VID002", "VID003"], state_ids())
check("exit 1", code == 1, f"code={code}")

reset()
import shutil
os.chdir(os.path.expanduser("~"))
shutil.rmtree(WORK, ignore_errors=True)
print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILURE(S)'}")
sys.exit(1 if failures else 0)
