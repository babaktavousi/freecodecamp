#!/usr/bin/env python3
"""Fetch real 360 footage to test the pipeline with.

Two sources, both giving material you are allowed to keep and re-use:

  1. Wikimedia Commons (default). Queries the live Commons API for freely
     licensed 360 videos, prints each file's licence and author, and downloads
     the one you pick. Searching for "insta360" finds clips shot on X-series
     cameras; the search string is configurable.

  2. Any URL that yt-dlp can handle (--youtube), for footage you have the right
     to download - your own uploads, or clips whose licence allows it.

Nothing is downloaded without being asked for, and the licence of every
candidate is printed before you choose.

    python3 samples/fetch_real_sample.py --list
    python3 samples/fetch_real_sample.py --index 0
    python3 samples/fetch_real_sample.py --youtube https://... --out samples/real.mp4

An Insta360 X3/X4/X5 records .insv, which is dual-fisheye and not directly
usable. Export an equirectangular MP4 from Insta360 Studio first (File ->
Export, "360" / equirectangular output). See samples/README.md.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

API = "https://commons.wikimedia.org/w/api.php"
# The Wikimedia API requires a descriptive User-Agent identifying the client.
USER_AGENT = "insta360-point-cloud-studio/1.0 (sample fetcher; https://github.com/)"


def api_get(params):
    url = f"{API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def search_commons(query, limit):
    data = api_get({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:video",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
    })
    pages = data.get("query", {}).get("pages", {})
    results = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        if not info.get("url"):
            continue
        meta = info.get("extmetadata", {})
        results.append({
            "title": page.get("title", "").removeprefix("File:"),
            "url": info["url"],
            "size": info.get("size", 0),
            "mime": info.get("mime", ""),
            "licence": strip_html(meta.get("LicenseShortName", {}).get("value", "unknown")),
            "author": strip_html(meta.get("Artist", {}).get("value", "unknown")),
            "page": info.get("descriptionurl", ""),
        })
    results.sort(key=lambda r: r["title"])
    return results


def strip_html(text):
    out = []
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return " ".join("".join(out).split())[:80]


def download(url, destination):
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    print(f"downloading {url}")
    with urllib.request.urlopen(request, timeout=300) as response, open(destination, "wb") as fh:
        total = int(response.headers.get("Content-Length") or 0)
        read = 0
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            fh.write(block)
            read += len(block)
            if total:
                print(f"\r  {read / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print(f"\nwrote {destination}")


def fetch_with_ytdlp(url, destination):
    if not shutil.which("yt-dlp"):
        raise SystemExit("yt-dlp not found. Install it with `pip install yt-dlp`.")
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    cmd = [
        "yt-dlp", "-f", "bestvideo[height<=2880]+bestaudio/best",
        "--merge-output-format", "mp4", "-o", destination, url,
    ]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", default="insta360", help="Commons search terms")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--list", action="store_true", help="only list candidates")
    ap.add_argument("--index", type=int, help="download this entry from the listing")
    ap.add_argument("--youtube", help="download from any yt-dlp supported URL instead")
    ap.add_argument("--out", default="samples/real_360.mp4")
    args = ap.parse_args()

    if args.youtube:
        fetch_with_ytdlp(args.youtube, args.out)
        print_next_steps(args.out)
        return

    try:
        results = search_commons(args.query, args.limit)
    except Exception as error:  # noqa: BLE001 - network failures should be readable
        raise SystemExit(f"Commons search failed: {error}")

    if not results:
        raise SystemExit(f"no video files found on Commons for '{args.query}'")

    print(f"{len(results)} freely licensed video(s) on Wikimedia Commons for '{args.query}':\n")
    for i, item in enumerate(results):
        print(f"[{i:2d}] {item['title']}")
        print(f"     {item['size'] / 1e6:.1f} MB  {item['mime']}  licence: {item['licence']}")
        print(f"     author: {item['author']}")
        if item["page"]:
            print(f"     {item['page']}")
    print()

    if args.list or args.index is None:
        print("Re-run with --index N to download one. Not every 360 video is a walking")
        print("capture, and only 2:1 equirectangular clips reconstruct well - check the")
        print("file page first. Keep the licence and author with any published result.")
        return

    if not 0 <= args.index < len(results):
        raise SystemExit(f"--index must be between 0 and {len(results) - 1}")
    chosen = results[args.index]
    download(chosen["url"], args.out)
    attribution = os.path.splitext(args.out)[0] + ".attribution.txt"
    with open(attribution, "w") as fh:
        fh.write(
            f"title: {chosen['title']}\nauthor: {chosen['author']}\n"
            f"licence: {chosen['licence']}\nsource: {chosen['page']}\n"
        )
    print(f"wrote {attribution}")
    print_next_steps(args.out)


def print_next_steps(path):
    print()
    print("Next:")
    print(f"  python3 tools/reconstruct.py --video {path} --out samples/real_cloud.ply")
    print("  or open the web app and load the file with the Video button.")
    print()
    print("If the clip is not 2:1 equirectangular the pipeline will say so. Convert")
    print("dual-fisheye .insv footage in Insta360 Studio first.")


if __name__ == "__main__":
    main()
