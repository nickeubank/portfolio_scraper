"""
Download digitised Exchequer Port Book images from portfolio.winchester.ac.uk.

The site (a 2014-era PHP/jQuery app) gates image access behind a TCs=accepted
cookie; the JSON endpoints return {"error": "Terms and Conditions not accepted."}
without it. Its SSL chain is also broken at the time of writing, so verify=False.

Use only with permission from the project author and within the copyright terms
of The National Archives (research / private study / education).

Usage:
    python download_portfolio.py --out ./images
    python download_portfolio.py --out ./images --start-year 1600 --end-year 1620
    python download_portfolio.py --out ./images --resume

Resumes safely: existing files are skipped, and a manifest tracks completed books.
"""

import argparse
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


def make_session() -> requests.Session:
    s = requests.Session()
    s.cookies.set("TCs", "accepted")
    s.headers.update({"User-Agent": USER_AGENT})
    s.verify = False
    return s


def query_books(session: requests.Session, year_start: int, year_end: int) -> list[dict]:
    """Return digitised port books whose date range overlaps [year_start, year_end]."""
    params = {
        "headport": "",
        "member": "",
        "range": f"{year_start}-{year_end}",
        "like": "",
        "unlike": "",
        "images": "only",
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


def get_image_list(session: requests.Session, reference: str) -> list[dict]:
    """Return image metadata for a single port book reference (e.g. '867/14')."""
    r = session.post(
        f"{BASE}/utilities/imagequery_json.php",
        data={"debug": "false", "reference": reference},
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(f"API error for {reference}: {payload['error']}")
    return payload.get("data", [])


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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default="images", help="Output directory")
    p.add_argument("--start-year", type=int, default=1565)
    p.add_argument("--end-year", type=int, default=1798)
    p.add_argument(
        "--year-chunk",
        type=int,
        default=5,
        help="Years per dataquery call (smaller avoids 1000-result cap)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between HTTP requests (be polite to the server)",
    )
    p.add_argument(
        "--manifest",
        default="manifest.jsonl",
        help="Append-only log of completed books (relative to --out)",
    )
    args = p.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / args.manifest

    done_refs: set[str] = set()
    if manifest_path.exists():
        with open(manifest_path) as f:
            for line in f:
                try:
                    done_refs.add(json.loads(line)["reference"])
                except (json.JSONDecodeError, KeyError):
                    pass
        print(f"Resuming: {len(done_refs)} books already completed.")

    session = make_session()

    seen_refs: set[str] = set(done_refs)
    n_books = 0
    n_downloaded = 0
    n_skipped = 0
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

        new = [b for b in books if b["reference"] not in seen_refs]
        print(f"  {len(books)} books returned, {len(new)} new")

        for book in new:
            ref = book["reference"]
            seen_refs.add(ref)
            n_books += 1
            try:
                images = get_image_list(session, ref)
            except Exception as e:
                print(f"  ERROR image list {ref}: {e}", file=sys.stderr)
                n_errors += 1
                continue
            time.sleep(args.delay)

            book_dl = 0
            book_skip = 0
            for img in images:
                fn = img["filename"]
                try:
                    status = download_image(session, ref, fn, out_dir)
                except Exception as e:
                    print(f"  ERROR download {ref}/{fn}: {e}", file=sys.stderr)
                    n_errors += 1
                    continue
                if status == "downloaded":
                    book_dl += 1
                    n_downloaded += 1
                else:
                    book_skip += 1
                    n_skipped += 1
                time.sleep(args.delay)

            print(
                f"  {ref}: {len(images)} images "
                f"({book_dl} downloaded, {book_skip} skipped)"
            )
            with open(manifest_path, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "reference": ref,
                            "headport": book.get("headport"),
                            "daterange": book.get("daterange"),
                            "n_images": len(images),
                        }
                    )
                    + "\n"
                )

    print(
        f"\nDone. Books processed: {n_books}, "
        f"images downloaded: {n_downloaded}, "
        f"skipped: {n_skipped}, errors: {n_errors}"
    )
    return 0 if n_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
