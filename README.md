# portfolio_scraper

Download digitised images of the English & Welsh Exchequer Port Books (TNA E 190
& E 122, 1565–1798) from the [Winchester Portfolio Project][site], plus the
project's crowd-sourced transcriptions of individual entries.

[site]: https://portfolio.winchester.ac.uk/

Two scripts:

- `download_portfolio.py` — all page photos for every digitised book.
- `download_transcripts.py` — the transcribed entries (voyage, vessel, master,
  merchants, cargo) and the "clip" photo links shown next to each entry,
  including the pixel region of the page photo that the entry occupies.

## Permission and use

Images on the site are © The National Archives. The site's terms restrict
their use to "research, private study or education." This script was written
for academic research with the project author's explicit permission. If you
fork this for your own work, **get the same permission first** and respect the
TNA copyright.

## Requirements

```bash
pip install requests
```

Python 3.9+.

## Usage

```bash
# Pull the entire digitised archive (slow — see "Scale" below)
python download_portfolio.py --out ./images

# Narrow to a date range
python download_portfolio.py --out ./images --start-year 1600 --end-year 1620

# Resume an interrupted run (automatic — just rerun the same command)
python download_portfolio.py --out ./images
```

Files land in `images/{reference}/{filename}.JPG`, where `{reference}` is the
TNA shelf reference with `/` replaced by `_` (e.g. `867_14`). A
`manifest.jsonl` in the output directory tracks completed books for resume.

### Flags

| Flag | Default | Notes |
| --- | --- | --- |
| `--out` | `images` | Output directory |
| `--start-year` | `1565` | First year to query |
| `--end-year` | `1798` | Last year to query |
| `--year-chunk` | `5` | Years per `dataquery_json.php` call. Shrink if you see the 1000-result cap warning. |
| `--delay` | `0.5` | Seconds between HTTP requests |
| `--manifest` | `manifest.jsonl` | Append-only log of completed books (path is relative to `--out`) |

## Transcripts

```bash
# Fetch all transcribed entries, link them to photos, write CSVs
python download_transcripts.py --out ./images

# Also cut each entry's region out of its page photo (requires Pillow)
python download_transcripts.py --out ./images --crop
```

Outputs (under `--out`, shared with the image script):

| Path | Contents |
| --- | --- |
| `transcripts/{ref}.json` | Raw `transcriptquery_json.php` response per book. Acts as the resume marker; delete or pass `--refresh` to re-fetch. |
| `transcripts.csv` | One row per *good* (entry × merchant × commodity), with all voyage/cargo fields flattened and the clip image filename(s). |
| `clips.csv` | One row per photo clip: raw viewer geometry plus computed pixel coordinates (`pixel_x/y/width/height`) on the original image. |
| `clips/{ref}/voyage{id}_clip{id}.jpg` | With `--crop`: the entry region cut from the page photo. |

Any page photo referenced by a clip that isn't already on disk is downloaded
into the same `images/{reference}/` layout, so the two scripts share one store.

**Clip geometry**: the site stores each photo link as a saved viewer state —
the image is rendered with CSS `translate(tx,ty) scale(s)` about the image
*centre*, and the clip rectangle is the viewer frame's screen position. The
original-image pixel coordinates are therefore
`x = (clip_x1 − tx)/s − (W/2)·(1−s)/s` (likewise for y), `w = clip_width/s`.
Rotation is ignored (it is almost always 0); treat crops as approximate when
`clip_rotation != 0`.

Note `--year-chunk` defaults to 1 here (not 5): the transcript crawl lists
*all* books, not just digitised ones, so dense periods hit the server's
1000-result cap quickly.

## How it works

The site is a 2014-era PHP/jQuery app. Three quirks worth knowing if you ever
need to debug:

1. **TCs cookie.** All JSON endpoints return
   `{"error": "Terms and Conditions not accepted."}` unless a `TCs=accepted`
   cookie is set. In a browser this cookie is set when you click "Accept" on
   the Terms dialog; if a privacy extension blocks that cookie, image viewers
   spin forever with no visible error.
2. **Broken SSL chain.** The host serves an incomplete cert chain, so the
   script uses `verify=False`. (`curl` needs `-k` for the same reason.)
3. **Image URL pattern.** Once you have a reference and filename:
   `https://portfolio.winchester.ac.uk/E190/{reference}/{filename}`.

The script:

1. Walks `[start-year, end-year]` in `--year-chunk` slices.
2. For each slice, calls `dataquery_json.php?images=only&range=YYYY-YYYY` to
   list digitised port books.
3. For each new book (`reference` not in the manifest), calls
   `imagequery_json.php` to get the image filenames.
4. Downloads each JPEG to a `.part` file and renames on success, so an
   interrupted run never leaves a half-written file.
5. Appends one line per completed book to `manifest.jsonl`.

## Scale

A 1599 sample: 2 books, 10 images, 28 MB. The series spans 233 years with
thousands of digitised books — expect tens to hundreds of GB and a multi-day
run at the default 0.5 s delay. The script is safe to stop (Ctrl-C) and rerun.
