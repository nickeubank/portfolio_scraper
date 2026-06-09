# portfolio_scraper

Download digitised images of the English & Welsh Exchequer Port Books (TNA E 190
& E 122, 1565–1798) from the [Winchester Portfolio Project][site].

[site]: https://portfolio.winchester.ac.uk/

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
