"""
Download transcribed Port Book entries from portfolio.winchester.ac.uk.

Companion to download_portfolio.py (same cookie / SSL quirks — see that file).
For every port book with transcripts (t_count > 0 in dataquery_json.php), this
fetches transcriptquery_json.php, which returns structured entries: voyage
(date, route, vessel, master), cargos (merchants), goods (qty/measure/
commodity), and "clips" — the photo links shown next to each entry, giving the
image filename plus the viewer geometry locating the entry on the page.

Use only with permission from the project author and within the copyright terms
of The National Archives (research / private study / education).

Usage:
    python download_transcripts.py --out ./images
    python download_transcripts.py --out ./images --crop

Outputs (under --out):
    transcripts/{reference}.json   raw API response per book (resume marker)
    transcripts.csv                one row per good (entry x merchant x item)
    clips.csv                      one row per photo clip, with both the raw
                                   viewer geometry and computed pixel coords
                                   on the original image
    clips/{reference}/voyage{id}_clip{id}.jpg   with --crop: the entry region
                                   cut from the original photo (approximate)

Missing clip images are downloaded into the same layout as
download_portfolio.py uses, so the two scripts share one image store.
"""

import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib3
from pathlib import Path

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://portfolio.winchester.ac.uk"
USER_AGENT = (
    "PortfolioArchiveDownloader/1.0 "
    "(academic research; contact nick@nickeubank.com; permission on file)"
)

VOYAGE_FIELDS = [
    "id",
    "reference",
    "folio",
    "rectoverso",
    "seq",
    "i_o",
    "certcoq",
    "date",
    "serial",
    "date_other",
    "duty",
    "vessel_name",
    "vessel_home",
    "vessel_burthen",
    "master_forename",
    "master_surname",
    "from",
    "to",
]
CARGO_FIELDS = [
    "id",
    "merchant_forename",
    "merchant_surname",
    "merchant_occupation",
    "merchant_abode",
    "miscellanea",
    "marginalia",
    "tonnage",
]
GOOD_FIELDS = ["id", "qty", "measure", "commodity", "description"]
CLIP_FIELDS = [
    "id",
    "voyage_id",
    "image_filename",
    "scale",
    "rotation",
    "translation_x",
    "translation_y",
    "clip_x1",
    "clip_y1",
    "clip_width",
    "clip_height",
]


def make_session() -> requests.Session:
    s = requests.Session()
    s.cookies.set("TCs", "accepted")
    s.headers.update({"User-Agent": USER_AGENT})
    s.verify = False
    return s


def query_books(session: requests.Session, year_start: int, year_end: int) -> list[dict]:
    """Return all port books (with image/transcript counts) overlapping the range."""
    params = {
        "headport": "",
        "member": "",
        "range": f"{year_start}-{year_end}",
        "like": "",
        "unlike": "",
        "images": "count",
    }
    r = session.get(f"{BASE}/utilities/dataquery_json.php", params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if data.get("count") == 1000:
        print(
            f"  WARN: hit 1000-result cap for {year_start}-{year_end} — "
            "narrow the chunk size",
            file=sys.stderr,
        )
    return data.get("data", [])


def get_transcripts(session: requests.Session, reference: str) -> dict:
    r = session.get(
        f"{BASE}/utilities/transcriptquery_json.php",
        params={"debug": "false", "reference": reference},
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(f"API error for {reference}: {payload['error']}")
    return payload


def download_image(
    session: requests.Session, reference: str, filename: str, out_dir: Path
) -> str:
    """Download one image; return 'skipped' or 'downloaded'."""
    safe_ref = reference.replace("/", "_")
    out_path = out_dir / safe_ref / filename
    if out_path.exists() and out_path.stat().st_size > 0:
        return "skipped"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/E190/{reference}/{urllib.parse.quote(filename)}"
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
    tmp.rename(out_path)
    return "downloaded"


def iso_date(raw: str) -> str:
    """'16810805' -> '1681-08-05'; anything unparseable -> ''."""
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def clip_pixel_box(
    clip: dict, img_width: int, img_height: int
) -> tuple[float, float, float, float] | None:
    """Map viewer geometry to (x, y, w, h) in original-image pixels.

    The viewer renders the photo with CSS
    `transform: translate(tx,ty) scale(s)` about the image CENTRE
    (default transform-origin), and the clip rect is the viewer frame's
    screen position at save time. Inverting that needs the image dimensions
    for the centre-origin term. Rotation is ignored (nearly always 0), so
    treat as approximate when rotation != 0.
    """
    try:
        s = float(clip["scale"])
        tx = float(clip["translation_x"])
        ty = float(clip["translation_y"])
        k = (1 - s) / s
        x = (float(clip["clip_x1"]) - tx) / s - img_width / 2 * k
        y = (float(clip["clip_y1"]) - ty) / s - img_height / 2 * k
        w = float(clip["clip_width"]) / s
        h = float(clip["clip_height"]) / s
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    return (x, y, w, h)


def image_size(path: Path, cache: dict) -> tuple[int, int] | None:
    """Image (width, height), cached; None if missing or Pillow unavailable."""
    if path not in cache:
        try:
            from PIL import Image

            with Image.open(path) as im:
                cache[path] = im.size
        except Exception:
            cache[path] = None
    return cache[path]


def write_csvs(transcripts_dir: Path, out_dir: Path) -> tuple[int, int]:
    """Flatten all per-book JSON files into transcripts.csv and clips.csv."""
    t_path = out_dir / "transcripts.csv"
    c_path = out_dir / "clips.csv"

    t_cols = (
        [f"voyage_{f}" for f in VOYAGE_FIELDS]
        + ["voyage_date_iso"]
        + [f"cargo_{f}" for f in CARGO_FIELDS]
        + [f"good_{f}" for f in GOOD_FIELDS]
        + ["n_clips", "clip_image_filenames"]
    )
    c_cols = (
        ["reference"]
        + [f"clip_{f}" for f in CLIP_FIELDS]
        + ["image_path", "pixel_x", "pixel_y", "pixel_width", "pixel_height"]
    )

    n_rows = 0
    n_clips = 0
    size_cache: dict = {}
    with open(t_path, "w", newline="") as tf, open(c_path, "w", newline="") as cf:
        tw = csv.writer(tf)
        cw = csv.writer(cf)
        tw.writerow(t_cols)
        cw.writerow(c_cols)

        for jf in sorted(transcripts_dir.glob("*.json")):
            with open(jf) as f:
                payload = json.load(f)
            for voyage in payload.get("data", []):
                ref = voyage.get("reference", "")
                safe_ref = ref.replace("/", "_")
                clips = voyage.get("clips") or []
                v_row = [voyage.get(f, "") for f in VOYAGE_FIELDS]
                v_row += [iso_date(voyage.get("date", ""))]
                clip_files = "|".join(c.get("image_filename", "") for c in clips)

                cargos = voyage.get("cargos") or [{}]
                for cargo in cargos:
                    c_row = [cargo.get(f, "") for f in CARGO_FIELDS]
                    goods = cargo.get("goods") or [{}]
                    for good in goods:
                        g_row = [good.get(f, "") for f in GOOD_FIELDS]
                        tw.writerow(
                            v_row + c_row + g_row + [len(clips), clip_files]
                        )
                        n_rows += 1

                for clip in clips:
                    image_path = f"{safe_ref}/{clip.get('image_filename', '')}"
                    size = image_size(out_dir / image_path, size_cache)
                    box = clip_pixel_box(clip, *size) if size else None
                    cw.writerow(
                        [ref]
                        + [clip.get(f, "") for f in CLIP_FIELDS]
                        + [image_path]
                        + ([round(v, 1) for v in box] if box else ["", "", "", ""])
                    )
                    n_clips += 1

    return n_rows, n_clips


def crop_clips(transcripts_dir: Path, out_dir: Path) -> None:
    """Cut each clip region out of its source image (requires Pillow)."""
    from PIL import Image

    n_done = 0
    n_missing = 0
    for jf in sorted(transcripts_dir.glob("*.json")):
        with open(jf) as f:
            payload = json.load(f)
        for voyage in payload.get("data", []):
            ref = voyage.get("reference", "")
            safe_ref = ref.replace("/", "_")
            for clip in voyage.get("clips") or []:
                src = out_dir / safe_ref / clip.get("image_filename", "")
                dst = (
                    out_dir
                    / "clips"
                    / safe_ref
                    / f"voyage{voyage['id']}_clip{clip['id']}.jpg"
                )
                if dst.exists():
                    continue
                if not src.exists():
                    n_missing += 1
                    continue
                with Image.open(src) as im:
                    box = clip_pixel_box(clip, im.width, im.height)
                    if box is None:
                        continue
                    x, y, w, h = box
                    left = max(0, round(x))
                    top = max(0, round(y))
                    right = min(im.width, round(x + w))
                    bottom = min(im.height, round(y + h))
                    if right <= left or bottom <= top:
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    im.crop((left, top, right, bottom)).save(dst, quality=90)
                n_done += 1
    print(f"Clips cropped: {n_done} (source image missing: {n_missing})")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default="images", help="Output directory")
    p.add_argument("--start-year", type=int, default=1565)
    p.add_argument("--end-year", type=int, default=1798)
    p.add_argument(
        "--year-chunk",
        type=int,
        default=1,
        help="Years per dataquery call (this script lists ALL books, not just "
        "digitised ones, so dense periods hit the 1000-result cap quickly)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between HTTP requests (be polite to the server)",
    )
    p.add_argument(
        "--crop",
        action="store_true",
        help="Also cut each clip region out of its image (requires Pillow)",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch transcripts even if a per-book JSON already exists",
    )
    args = p.parse_args()

    out_dir = Path(args.out).expanduser()
    transcripts_dir = out_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    session = make_session()

    seen_refs: set[str] = set()
    n_books = 0
    n_fetched = 0
    n_img_dl = 0
    n_errors = 0

    for year in range(args.start_year, args.end_year + 1, args.year_chunk):
        chunk_end = min(year + args.year_chunk - 1, args.end_year)
        print(f"\n=== {year}-{chunk_end} ===")
        try:
            books = query_books(session, year, chunk_end)
        except Exception as e:
            print(f"  ERROR querying {year}-{chunk_end}: {e}", file=sys.stderr)
            n_errors += 1
            continue
        time.sleep(args.delay)

        with_transcripts = [
            b
            for b in books
            if b["reference"] not in seen_refs and int(b.get("t_count") or 0) > 0
        ]
        seen_refs.update(b["reference"] for b in books)
        print(f"  {len(books)} books returned, {len(with_transcripts)} with transcripts")

        for book in with_transcripts:
            ref = book["reference"]
            n_books += 1
            json_path = transcripts_dir / (ref.replace("/", "_") + ".json")

            if json_path.exists() and not args.refresh:
                with open(json_path) as f:
                    payload = json.load(f)
                status = "cached"
            else:
                try:
                    payload = get_transcripts(session, ref)
                except Exception as e:
                    print(f"  ERROR transcripts {ref}: {e}", file=sys.stderr)
                    n_errors += 1
                    continue
                time.sleep(args.delay)
                tmp = json_path.with_suffix(".json.part")
                with open(tmp, "w") as f:
                    json.dump(payload, f, indent=1)
                tmp.rename(json_path)
                n_fetched += 1
                status = "fetched"

            # Make sure every photo a clip points at is on disk.
            voyages = payload.get("data", [])
            book_img_dl = 0
            for voyage in voyages:
                for clip in voyage.get("clips") or []:
                    fn = clip.get("image_filename")
                    if not fn:
                        continue
                    try:
                        if download_image(session, ref, fn, out_dir) == "downloaded":
                            book_img_dl += 1
                            n_img_dl += 1
                            time.sleep(args.delay)
                    except Exception as e:
                        print(
                            f"  ERROR download {ref}/{fn}: {e}", file=sys.stderr
                        )
                        n_errors += 1

            print(
                f"  {ref}: {len(voyages)} entries ({status}, "
                f"{book_img_dl} clip images downloaded)"
            )

    n_rows, n_clips = write_csvs(transcripts_dir, out_dir)
    print(
        f"\nWrote {out_dir / 'transcripts.csv'} ({n_rows} rows) and "
        f"{out_dir / 'clips.csv'} ({n_clips} clips)."
    )

    if args.crop:
        crop_clips(transcripts_dir, out_dir)

    print(
        f"Done. Books with transcripts: {n_books}, fetched: {n_fetched}, "
        f"clip images downloaded: {n_img_dl}, errors: {n_errors}"
    )
    return 0 if n_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
