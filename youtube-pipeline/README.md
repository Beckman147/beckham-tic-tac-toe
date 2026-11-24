# YouTube pipeline: PS5 clips → Shorts → scheduled uploads

Three scripts that turn raw PS5 captures into a week of scheduled YouTube Shorts.

| Script | What it does |
|---|---|
| `find_moments.py` | Goes through a whole folder of clips and finds the highlights. Goals are the loudest moments in Rocket League, so it listens for big jumps in volume, then makes a page of short previews and a ready-made `clips.csv` with start and end times filled in. |
| `make_shorts.py` | Batch-converts landscape PS5 clips into 1080×1920 vertical Shorts at up to 60fps: trims to your start/end times, reframes, normalizes loudness to YouTube's −14 LUFS, burns in a hook line that wraps to fit the screen, adds an optional end card like "worth a sub?" over the last second or two, and saves a thumbnail frame. |
| `upload_youtube.py` | Uploads from a spreadsheet with titles, tags and scheduled publish times. Safe to re-run: it never uploads the same file twice, stops cleanly at the daily quota, and stops after one video if YouTube forces it private. |

**Start with [SETUP.md](SETUP.md).**

## Finding your best moments

Got a pile of clips and no idea what's in them?

```
py find_moments.py --input C:\yt\clips_in --output C:\yt\scout
```

Then open `C:\yt\scout\index.html` and watch the previews, loudest moments first. Open
`C:\yt\scout\clips.csv` (row 1 is preview #1, and so on), delete the rows you don't
want, type a hook into the ones you keep, and build them:

```
py make_shorts.py --input C:\yt\clips_in --output C:\yt\shorts_out --manifest C:\yt\scout\clips.csv --end-card "worth a sub?"
```

Each moment runs from 8 seconds before the loud spike to 4 seconds after it. `--after 12`
also catches Rocket League's goal replay, and `--top 40` keeps more moments. Loud isn't
always good (a demo can score high, and a great save is quiet), so treat the list as a
shortlist to watch. The `overview_*.jpg` images show five frames of every moment, so you
can skim them at a glance or send them to Claude to help choose.

A clip can appear on several rows of `clips.csv`. Each row becomes its own Short
(`name_short_1.mp4`, `name_short_2.mp4`, ...).

## Framing modes

PS5 footage is 16:9 and Shorts are 9:16, so something has to give. Try all three on one
real clip before you commit:

- `zoom` *(default)*: centre square over a blurred fill. Action ~1.8× bigger than `fit`,
  keeps ~56% of the frame width. The best compromise for shooters.
- `crop`: full-screen centre crop. Biggest action, but keeps only ~32% of the width, so
  the minimap, killfeed and ammo counter are gone.
- `fit`: the whole frame over a blurred fill. Full HUD, small action on a phone.

## Frame rate

Shorts keep the clip's own frame rate, up to 60fps. PS5 captures are 60fps, and fast
games like Rocket League look much smoother at 60 than 30. Use `--fps 30` for smaller
files.

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
python tests/test_find_moments.py
python tests/test_make_shorts.py
python tests/test_upload.py
```

`test_find_moments.py` builds clips with loud bursts at known times and checks they're
found, timed and ranked correctly, that quiet and silent clips give nothing, and that
the shortlist feeds straight into `make_shorts.py`.

`test_make_shorts.py` builds its own sample clips with ffmpeg and checks trim lengths,
that frame rates come through intact (60fps stays 60, 30fps isn't padded), and that the
end card appears exactly when it should, including on trimmed clips.
`test_upload.py` runs the uploader against a fake YouTube service (no account needed) and
covers the happy path, a quota stop mid-batch with resume, forced-private detection, a
dropped schedule, retries on server errors, a single bad row, and `--limit`.

## Note

This lives in the tic-tac-toe repo for now. It's self-contained, so you can move the
whole folder into its own repo whenever you like.
