# Setup (Windows)

One-time setup, about 30 minutes. After this, a week of Shorts takes two commands.

---

## 1. Install Python and ffmpeg

Open **PowerShell** and run:

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
```

**Close and reopen PowerShell** (otherwise it won't find the new programs), then check:

```powershell
py --version
ffmpeg -version
```

Both should print a version. If `ffmpeg` says "not recognized", restart your PC once.

## 2. Install the uploader's libraries

```powershell
py -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

`make_shorts.py` needs nothing extra. Only the uploader uses these.

## 3. Get clips off the PS5

1. Format a USB drive as **exFAT** (on Windows: right-click it in File Explorer → Format → exFAT).
2. On the PS5: **Media Gallery** → tick the checkbox icon to multi-select → pick clips → **Options → Copy to USB Drive**.
3. Plug the drive into your PC. Clips are under `PS5\CREATE\`.
4. Copy the clips you want into a working folder, e.g. `C:\yt\clips_in\`.

> Skip the PlayStation App auto-upload for this. It's limited to 1080p clips under
> 3 minutes and deletes them after 14 days.

**Fix this PS5 setting once:** Settings → Captures and Broadcasts → Captures. Make sure
video is recording at the resolution you want. You can't re-shoot a moment later.

---

## 4. YouTube API access (needed only for the uploader)

You're creating your *own* private "app" that only you use. Google makes this look
scarier than it is.

1. Go to **console.cloud.google.com** and sign in with the Google account that **owns
   your YouTube channel**.
2. Top bar → project picker → **New Project**. Name it anything (`yt-uploader`). Create.
3. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
4. Open **Google Auth Platform** (older UI: *OAuth consent screen*) → **Get started**:
   - App name: anything. Support email: yours.
   - Audience: **External**.
   - Contact email: yours → Create.
5. **Audience → Test users → Add users** → add **your own Gmail address**. Skip this
   and sign-in fails with "access blocked".
6. **Clients → Create client** → Application type **Desktop app** → Create →
   **Download JSON**.
7. Rename that file to `client_secret.json` and put it in this `youtube-pipeline` folder.

**Never share or commit `client_secret.json` or `token.json`.** The `.gitignore` already
blocks them.

### First sign-in

The first time you run the uploader, a browser opens. You'll see **"Google hasn't
verified this app"**. That's expected, because it's your own app. Click **Advanced →
Go to (your app name)** → allow. Your login is saved to `token.json`.

While your app is in "Testing" mode, Google expires that login **every 7 days**. When
that happens the script just opens the browser again. That's normal.

---

## 5. Your weekly routine

```powershell
cd C:\path\to\youtube-pipeline

# 1. Copy templates\clips.csv to C:\yt\clips.csv and list your best moments
#    (filename, start, end, hook text). Then build the Shorts:
py make_shorts.py --input C:\yt\clips_in --output C:\yt\shorts_out --manifest C:\yt\clips.csv

# 2. Watch the results in C:\yt\shorts_out. Not happy with the framing? Try
#    --mode crop or --mode fit (default is zoom).

# 3. Copy templates\uploads.csv, fill in titles and publish times, then:
py upload_youtube.py --csv C:\yt\uploads.csv --dry-run    # check everything first
py upload_youtube.py --csv C:\yt\uploads.csv              # upload + schedule
```

### The very first time you upload: do a one-video test

```powershell
py upload_youtube.py --csv C:\yt\uploads.csv --limit 1
```

Then open **YouTube Studio** and confirm the video shows the schedule you set. See the
next section for why.

---

## If uploads get locked to private

Google has historically **forced videos uploaded through new, unaudited API projects to
private**. In Studio they show as "Private (locked)" and can't be made public. Reports
conflict on whether that rule is still enforced in 2026, and I couldn't confirm it from
Google's docs while building this. So the uploader checks every video after uploading
it, and **stops the whole batch the moment YouTube changes your privacy setting**. You'd
lose one test video, not a week's worth.

If it happens, you have two options:

1. **Upload through YouTube Studio instead.** Studio lets you drag in up to 15 files at
   once and schedule each one. `make_shorts.py` still does all the editing. Only the
   last step goes back to manual. This is the realistic fallback.
2. **Request an API audit** through Google's *YouTube API Services Audit and Quota
   Extension* form. It's slow and aimed at real apps, so it's not worth it for one channel.

## Daily limits

YouTube caps how many API uploads a project can make per day. Reported numbers range from
about 6 to 100 depending on the source and date. If you hit it, the script stops, keeps a
record of what finished, and **the next run picks up exactly where it left off** without
re-uploading anything. The cap resets at midnight Pacific time.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ffmpeg not found` | Reopen PowerShell after installing. Still broken: restart the PC. |
| `access blocked` at sign-in | You skipped step 4.5. Add yourself as a test user. |
| `HTTP 401` | Delete `token.json` and run again to sign in fresh. |
| Thumbnail fails with 403 | Custom thumbnails need a phone-verified channel (youtube.com/verify). The video still uploads. The Shorts feed ignores custom thumbnails anyway. |
| Hook text missing | No bold font found. Add `--font C:\Windows\Fonts\arialbd.ttf`. |
| A video uploaded twice | Don't delete or move `uploaded.json`. It's the record that prevents this. |
