#!/usr/bin/env python3
"""
upload_youtube.py - Batch-upload videos to YouTube from a CSV, with scheduling.

Runs on YOUR machine under YOUR Google account. Nothing is sent anywhere except
YouTube's own upload API.

Usage:
  python upload_youtube.py --csv uploads.csv --dry-run     # validate only
  python upload_youtube.py --csv uploads.csv --limit 1     # canary: one video
  python upload_youtube.py --csv uploads.csv               # the rest

CSV (header required). 'file' and 'title' are mandatory:
  file,title,description,tags,privacy,publish_at,thumbnail
  shorts_out/clip01_short.mp4,4 kills 1 mag #shorts,,cod|warzone|clutch,private,2026-12-01T17:00:00Z,

  privacy     private | unlisted | public   (default: private)
  publish_at  ISO 8601 time to go public, e.g. 2026-12-01T17:00:00Z or
              2026-12-01T12:00:00-05:00. Forces privacy=private until then.
  tags        separated by |
  thumbnail   optional .jpg/.png. Needs a phone-verified channel, and the
              Shorts feed ignores it - only matters for long-form.
  category    optional; defaults to 20 (Gaming)

Safety:
  * A state file (default uploaded.json) records every finished upload. Re-runs
    skip anything already in it, so a crash or quota stop never double-uploads.
  * After each upload the video is read back. If YouTube changed the privacy
    you asked for (unaudited API projects can be forced to private), the batch
    stops immediately so you don't upload a pile of locked videos.
  * Hitting the daily upload quota stops the batch cleanly. Run it again later.

One-time setup: see SETUP.md next to this script.

Requires: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
"""

import argparse
import csv
import datetime as dt
import json
import os
import random
import sys
import time

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # read-only, used to verify each upload landed with the privacy you asked for
    "https://www.googleapis.com/auth/youtube.readonly",
]

VALID_PRIVACY = {"private", "unlisted", "public"}
GAMING_CATEGORY = "20"
RETRYABLE_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8
CHUNK = 8 * 1024 * 1024


class StopBatch(Exception):
    """Raised to halt the whole run (quota, locked privacy, auth)."""


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- CSV

def parse_publish_at(value):
    """Return an RFC 3339 UTC string, or None. Raises ValueError."""
    value = (value or "").strip()
    if not value:
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    when = dt.datetime.fromisoformat(raw)
    if when.tzinfo is None:
        raise ValueError(f"'{value}' has no timezone - add Z or an offset like -05:00")
    when = when.astimezone(dt.timezone.utc)
    if when <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise ValueError(f"'{value}' is in the past (or under 5 minutes away)")
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_rows(csv_path):
    """Parse and validate every row up front. Any error aborts before uploading."""
    base = os.path.dirname(os.path.abspath(csv_path))
    rows, errors = [], []

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = [(f or "").strip().lower() for f in (reader.fieldnames or [])]
        for col in ("file", "title"):
            if col not in fields:
                die(f"{csv_path}: missing required column '{col}'. Found: {reader.fieldnames}")

        for lineno, raw in enumerate(reader, start=2):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if not any(r.values()):
                continue
            where = f"line {lineno}"

            path = r.get("file", "")
            if not path:
                errors.append(f"{where}: empty 'file'")
                continue
            if not os.path.isabs(path):
                path = os.path.join(base, path)
            path = os.path.normpath(path)
            if not os.path.isfile(path):
                errors.append(f"{where}: file not found: {path}")

            title = r.get("title", "")
            if not title:
                errors.append(f"{where}: empty 'title'")
            elif len(title) > 100:
                errors.append(f"{where}: title is {len(title)} chars, YouTube max is 100")
            desc = r.get("description", "")
            for label, text in (("title", title), ("description", desc)):
                if "<" in text or ">" in text:
                    errors.append(f"{where}: {label} contains < or >, which YouTube rejects")
            if len(desc.encode("utf-8")) > 5000:
                errors.append(f"{where}: description exceeds 5000 bytes")

            tags = [t.strip() for t in r.get("tags", "").split("|") if t.strip()]
            # YouTube counts tags containing spaces as if wrapped in quotes.
            tag_len = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)
            if tag_len > 500:
                errors.append(f"{where}: tags total {tag_len} chars, YouTube max is 500")

            privacy = (r.get("privacy") or "private").lower()
            if privacy not in VALID_PRIVACY:
                errors.append(f"{where}: privacy '{privacy}' must be one of {sorted(VALID_PRIVACY)}")

            publish_at = None
            try:
                publish_at = parse_publish_at(r.get("publish_at"))
            except ValueError as exc:
                errors.append(f"{where}: publish_at {exc}")
            if publish_at and privacy != "private":
                print(f"note: {where}: publish_at set, so privacy forced to private "
                      f"until it goes live (was '{privacy}')")
                privacy = "private"

            thumb = r.get("thumbnail", "")
            if thumb:
                if not os.path.isabs(thumb):
                    thumb = os.path.join(base, thumb)
                thumb = os.path.normpath(thumb)
                if not os.path.isfile(thumb):
                    errors.append(f"{where}: thumbnail not found: {thumb}")
                elif os.path.getsize(thumb) > 2 * 1024 * 1024:
                    errors.append(f"{where}: thumbnail over YouTube's 2MB limit")

            rows.append({
                "line": lineno, "file": path, "title": title,
                "description": desc, "tags": tags, "privacy": privacy,
                "publish_at": publish_at, "thumbnail": thumb or None,
                "category": r.get("category") or GAMING_CATEGORY,
            })

    if errors:
        print(f"\n{len(errors)} problem(s) in {csv_path} - nothing was uploaded:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    return rows


# ------------------------------------------------------------------------- state

def load_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        die(f"could not read state file {path}: {exc}\n"
            f"  Fix or move it aside. Deleting it means already-uploaded videos "
            f"would be uploaded again.")


def save_state(path, state):
    """Atomic write so a crash mid-save can't corrupt the record."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def state_key(path):
    return os.path.normcase(os.path.abspath(path))


# -------------------------------------------------------------------------- auth

def get_service(client_secret, token_path):
    """Build an authorised YouTube client, running the browser OAuth flow if needed."""
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        die("Google libraries missing. Run:\n"
            "  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # OAuth apps left in "Testing" mode issue refresh tokens that die after
            # 7 days. Just sign in again.
            print("saved login expired - opening browser to sign in again")
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(client_secret):
            die(f"{client_secret} not found. Download it from Google Cloud Console "
                f"(see SETUP.md, step 4).")
        flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


# ------------------------------------------------------------------------ upload

def error_reason(exc):
    """Pull YouTube's machine-readable reason out of an HttpError."""
    try:
        body = json.loads(exc.content.decode("utf-8"))
        errs = body.get("error", {}).get("errors", [])
        if errs:
            return errs[0].get("reason", "")
        return body.get("error", {}).get("status", "")
    except Exception:
        return ""


def classify(exc):
    """Map an HttpError to 'retry', 'stop' or 'skip'."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    reason = error_reason(exc)
    if status in RETRYABLE_STATUS:
        return "retry", status, reason
    if status == 403 and reason in {"quotaExceeded", "uploadLimitExceeded",
                                     "rateLimitExceeded", "dailyLimitExceeded"}:
        return "stop", status, reason
    if status == 401:
        return "stop", status, reason
    return "skip", status, reason


def resumable_upload(request, label):
    """Drive a resumable upload to completion with backoff on transient errors."""
    from googleapiclient.errors import HttpError

    response, attempt = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"\r    {label}: {int(status.progress() * 100):3d}%", end="", flush=True)
        except HttpError as exc:
            kind, code, reason = classify(exc)
            if kind != "retry":
                raise
            attempt += 1
            if attempt > MAX_RETRIES:
                raise
            wait = min(64, 2 ** attempt) + random.random()
            print(f"\n    HTTP {code} {reason}, retry {attempt}/{MAX_RETRIES} in {wait:.0f}s")
            time.sleep(wait)
        except (ConnectionError, TimeoutError, OSError) as exc:
            attempt += 1
            if attempt > MAX_RETRIES:
                raise
            wait = min(64, 2 ** attempt) + random.random()
            print(f"\n    network error ({exc}), retry {attempt}/{MAX_RETRIES} in {wait:.0f}s")
            time.sleep(wait)
    print(f"\r    {label}: 100%")
    return response


def verify(youtube, video_id, row, inserted):
    """
    Confirm YouTube kept the privacy and schedule we asked for.

    Unaudited Google Cloud projects have historically had uploads forced to
    private. Detect that on the first video and stop, instead of discovering
    it after uploading the whole batch.
    """
    got = (inserted or {}).get("status", {})
    try:
        res = youtube.videos().list(part="status", id=video_id).execute()
        items = res.get("items", [])
        if items:
            got = items[0].get("status", got)
    except Exception as exc:  # read-back is a safety net, not a hard requirement
        print(f"    (could not read back status: {exc})")

    want_priv, got_priv = row["privacy"], got.get("privacyStatus")
    problems = []
    if got_priv and got_priv != want_priv:
        problems.append(f"privacy is '{got_priv}', you asked for '{want_priv}'")
    if row["publish_at"] and not got.get("publishAt"):
        problems.append(f"scheduled time {row['publish_at']} was dropped")
    return problems


def upload_one(youtube, row):
    from googleapiclient.http import MediaFileUpload

    status = {"privacyStatus": row["privacy"], "selfDeclaredMadeForKids": False}
    if row["publish_at"]:
        status["publishAt"] = row["publish_at"]

    body = {
        "snippet": {
            "title": row["title"],
            "description": row["description"],
            "tags": row["tags"],
            "categoryId": row["category"],
        },
        "status": status,
    }
    media = MediaFileUpload(row["file"], chunksize=CHUNK, resumable=True,
                            mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    return resumable_upload(request, os.path.basename(row["file"]))


def set_thumbnail(youtube, video_id, thumb):
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb)).execute()
        print("    thumbnail set")
    except HttpError as exc:
        reason = error_reason(exc)
        hint = " (channel needs phone verification for custom thumbnails)" \
            if exc.resp.status == 403 else ""
        print(f"    thumbnail failed: HTTP {exc.resp.status} {reason}{hint} - video is still up")


# -------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Batch-upload videos to YouTube from a CSV.")
    ap.add_argument("--csv", required=True, help="upload manifest")
    ap.add_argument("--client-secret", default="client_secret.json",
                    help="OAuth client file from Google Cloud (default: client_secret.json)")
    ap.add_argument("--token", default="token.json",
                    help="where to cache your login (default: token.json)")
    ap.add_argument("--state", default="uploaded.json",
                    help="record of finished uploads (default: uploaded.json)")
    ap.add_argument("--limit", type=int, default=0,
                    help="upload at most N videos this run (use 1 for a first test)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate the CSV and show the plan without uploading")
    args = ap.parse_args()

    if not os.path.isfile(args.csv):
        die(f"CSV not found: {args.csv}")

    rows = load_rows(args.csv)
    state = load_state(args.state)

    todo = [r for r in rows if state_key(r["file"]) not in state]
    done_already = len(rows) - len(todo)
    if args.limit > 0:
        todo = todo[:args.limit]

    print(f"{len(rows)} row(s): {done_already} already uploaded, {len(todo)} to do this run\n")
    for r in todo:
        when = f"goes public {r['publish_at']}" if r["publish_at"] else r["privacy"]
        print(f"  line {r['line']:>3}  {os.path.basename(r['file'])}  [{when}]  {r['title']}")

    if args.dry_run or not todo:
        print("\n(dry run - nothing uploaded)" if args.dry_run else "\nnothing to do")
        return

    from googleapiclient.errors import HttpError

    youtube = get_service(args.client_secret, args.token)
    print()
    uploaded = 0
    failed = []
    try:
        for r in todo:
            print(f"[{uploaded + 1}/{len(todo)}] {r['title']}")
            try:
                resp = upload_one(youtube, r)
            except HttpError as exc:
                kind, code, reason = classify(exc)
                if kind == "stop":
                    if code == 401:
                        raise StopBatch(f"HTTP 401 {reason}: login rejected. Delete "
                                        f"{args.token} and run again to sign in.")
                    raise StopBatch(f"HTTP {code} {reason}: daily upload quota reached. "
                                    f"Progress is saved - run the same command again "
                                    f"tomorrow (quota resets at midnight Pacific).")
                print(f"    FAILED, skipping: HTTP {code} {reason or exc}")
                failed.append(os.path.basename(r["file"]))
                continue

            vid = resp["id"]
            state[state_key(r["file"])] = {
                "video_id": vid,
                "title": r["title"],
                "uploaded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            save_state(args.state, state)  # record before anything else can fail
            uploaded += 1
            print(f"    https://youtu.be/{vid}")

            problems = verify(youtube, vid, r, resp)
            if problems:
                raise StopBatch(
                    "YouTube did not keep your settings on that upload:\n      - "
                    + "\n      - ".join(problems)
                    + "\n    Your Google Cloud project is probably unaudited, which forces "
                      "API uploads to private. Stopped so the rest aren't locked too.\n"
                      "    Options: publish through YouTube Studio instead, or request an "
                      "API audit (SETUP.md, 'If uploads get locked to private')."
                )

            if r["thumbnail"]:
                set_thumbnail(youtube, vid, r["thumbnail"])
    except StopBatch as stop:
        print(f"\nSTOPPED: {stop}", file=sys.stderr)
        print(f"{uploaded} uploaded this run, recorded in {args.state}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print(f"\ninterrupted - {uploaded} uploaded this run, recorded in {args.state}")
        sys.exit(130)

    print(f"\ndone: {uploaded} uploaded, recorded in {args.state}")
    if failed:
        print(f"{len(failed)} FAILED (not recorded, so the next run retries them): "
              f"{', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
