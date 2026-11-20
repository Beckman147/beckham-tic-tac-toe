# YouTube pipeline: PS5 clips → Shorts → scheduled uploads

Two scripts that turn raw PS5 captures into a week of scheduled YouTube Shorts.

| Script | What it does |
|---|---|
| `make_shorts.py` | Batch-converts landscape PS5 clips into 1080×1920 vertical Shorts: trims to your start/end times, reframes, normalizes loudness to YouTube's −14 LUFS, burns in a hook line that wraps to fit the screen, adds an optional end card like "worth a sub?" over the last second or two, and saves a thumbnail frame. |
| `upload_youtube.py` | Uploads from a spreadsheet with titles, tags and scheduled publish times. Safe to re-run: it never uploads the same file twice, stops cleanly at the daily quota, and stops after one video if YouTube forces it private. |

**Start with [SETUP.md](SETUP.md).**

## Framing modes

PS5 footage is 16:9 and Shorts are 9:16, so something has to give. Try all three on one
real clip before you commit:

- `zoom` *(default)*: centre square over a blurred fill. Action ~1.8× bigger than `fit`,
  keeps ~56% of the frame width. The best compromise for shooters.
- `crop`: full-screen centre crop. Biggest action, but keeps only ~32% of the width, so
  the minimap, killfeed and ammo counter are gone.
- `fit`: the whole frame over a blurred fill. Full HUD, small action on a phone.

## End card

```
py make_shorts.py --input C:\yt\clips_in --output C:\yt\shorts_out --manifest C:\yt\clips.csv --end-card "worth a sub?"
```

The card fades in over the last 1.5 seconds of every Short (`--end-card-secs` changes
that). It sits a little above centre, clear of the title, subscribe button and side
buttons YouTube draws on top of a Short. To change it for one clip, put different text in
that clip's `end_card` column in `clips.csv`, or `none` to leave it off.

## Testing

```
python tests/test_make_shorts.py
python tests/test_upload.py
```

`test_make_shorts.py` builds its own sample clips with ffmpeg and checks trim lengths and
that the end card appears exactly when it should, including on trimmed clips.
`test_upload.py` runs the uploader against a fake YouTube service (no account needed) and
covers the happy path, a quota stop mid-batch with resume, forced-private detection, a
dropped schedule, retries on server errors, a single bad row, and `--limit`.

## Note

This lives in the tic-tac-toe repo for now. It's self-contained, so you can move the
whole folder into its own repo whenever you like.
