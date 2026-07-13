#!/usr/bin/env python3
"""
ZFetch Backend — End-to-End Stateless DaaS Workflow Test Script
===============================================================

Tests the stateless DaaS workflow:
  Phase 1: POST /api/extract  -> Metadata, Formats & Stateless Proxy stream_urls
  Phase 2: User chooses format (video/audio)
  Phase 3: User chooses delivery method:
           1 - Live CDN Proxy Stream (direct on-the-fly streaming via GET /api/stream)
           2 - Direct Server Download (downloads, process-merges/converts, and streams back via POST /api/download)
  Phase 4: Start download & stream

Usage:
    python test_api.py
"""

import os
import re
import sys
import time
import requests

BASE_URL = "https://zfetch-production.up.railway.app/"
TEST_VIDEO_URL = "https://youtu.be/S9g4HY9BbEY?si=TW_QqyBuBeDfEZch"  # Helen Meles

# ─── Cookie Loading ───────────────────────────────────────────────────────────
# Provide cookies in ONE of these ways (checked in order):
#   1. YOUTUBE_COOKIES env var  — paste the full Netscape cookie string
#   2. cookies.txt file         — place a Netscape-format cookies.txt next to this script
# Leave both empty to attempt unauthenticated extraction (may hit bot check).

def load_cookies() -> str:
    """Returns a Netscape-format cookie string, or an empty string if none found."""
    env_cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if env_cookies:
        print("  [cookies] Loaded from YOUTUBE_COOKIES env var")
        return env_cookies

    cookie_file = os.path.join(os.path.dirname(__file__), "cookies.txt")
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            print(f"  [cookies] Loaded from {cookie_file}")
            return content

    print("  [cookies] No cookies found — proceeding without authentication")
    return ""

YOUTUBE_COOKIES = load_cookies()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def banner(text):
    print()
    print("═" * 64)
    print(f"  {text}")
    print("═" * 64)


def sub_banner(text):
    print(f"\n  ── {text} ──")


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name)


def fmt_size(size_bytes) -> str:
    if size_bytes is None:
        return "unknown"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.2f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def download_file_from_url(url: str, filename: str, method="GET", payload=None):
    """
    Downloads a file from `url` to `filename`.
    Shows a real-time progress bar. Supports GET and POST streaming.
    """
    base_headers = {
        'User-Agent': (
            'Mozilla/5.0 (X11; Linux x86_64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/125.0.0.0 Safari/537.36'
        ),
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'identity',
        'Referer': 'https://www.youtube.com/',
        'Origin': 'https://www.youtube.com',
    }

    try:
        start_time = time.time()
        print(f"  Saving to: {os.path.abspath(filename)}")

        if method.upper() == "POST":
            response = requests.post(url, json=payload, headers=base_headers, stream=True, timeout=180)
        else:
            response = requests.get(url, headers=base_headers, stream=True, timeout=180)

        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        wrote = 0

        with open(filename, 'wb') as f:
            for data in response.iter_content(64 * 1024):
                wrote += len(data)
                f.write(data)
                if total_size > 0:
                    pct = (wrote / total_size) * 100
                    speed = wrote / max(time.time() - start_time, 0.001)
                    print(
                        f'\r  Streaming: {pct:.1f}% | {fmt_size(wrote)}/{fmt_size(total_size)}'
                        f' | {fmt_size(speed)}/s',
                        end='', flush=True,
                    )
                else:
                    speed = wrote / max(time.time() - start_time, 0.001)
                    print(f'\r  Streaming: {fmt_size(wrote)} | {fmt_size(speed)}/s',
                          end='', flush=True)

        elapsed = time.time() - start_time
        print(f'\n  ✓ Done in {elapsed:.2f}s  ({fmt_size(wrote)} total)')

    except Exception as e:
        print(f'\n  ✗ Download error: {e}')
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError:
                pass


# ─── Step 0 — System health check ────────────────────────────────────────────

def check_system():
    banner("0 · System Health Check")
    try:
        r = requests.get(f"{BASE_URL}/api/system/version", timeout=5)
        if r.status_code == 200:
            d = r.json()
            print(f"  Name:    {d.get('name')}")
            print(f"  Version: {d.get('version')}")
        else:
            print(f"  ⚠ Version endpoint returned {r.status_code}")
    except requests.exceptions.ConnectionError:
        print("\n  ✗ Cannot connect to backend. Start it with: daphne -b 127.0.0.1 -p 8000 config.asgi:application")
        sys.exit(1)

    r = requests.get(f"{BASE_URL}/api/system/status", timeout=5)
    if r.status_code == 200:
        disk = r.json().get('disk', {})
        print(f"  Disk: {disk.get('total_gb')} GB total, {disk.get('free_gb')} GB free")
    else:
        print(f"  ⚠ Status endpoint returned {r.status_code}")


# ─── Phase 1 — Extraction ────────────────────────────────────────────────────

def phase_1_extract():
    banner("Phase 1 · POST /api/extract  — Metadata & Format Extraction")
    print(f"  URL: {TEST_VIDEO_URL}")
    print("  Extracting (may take a few seconds)... ", end="", flush=True)

    t0 = time.time()
    payload = {"url": TEST_VIDEO_URL}
    if YOUTUBE_COOKIES:
        payload["cookies"] = YOUTUBE_COOKIES
    r = requests.post(f"{BASE_URL}/api/extract", json=payload, timeout=60)
    elapsed = time.time() - t0

    if r.status_code != 200:
        print("FAILED")
        print(f"  {r.status_code}: {r.text}")
        sys.exit(1)

    print(f"OK ({elapsed:.2f}s)")
    data = r.json()

    print(f"\n  Title:    {data.get('title')}")
    print(f"  Author:   {data.get('author')}")
    print(f"  Duration: {data.get('duration')}s")
    print(f"  Views:    {data.get('views')}")

    video_fmts = data.get('formats', {}).get('video', [])
    audio_fmts = data.get('formats', {}).get('audio', [])
    subtitles  = data.get('subtitles', [])

    sub_banner("VIDEO FORMATS")
    for i, fmt in enumerate(video_fmts, 1):
        res   = fmt.get('resolution', 'unknown')
        fps   = f" @ {fmt.get('fps')}fps" if fmt.get('fps') else ""
        size  = fmt_size(fmt.get('filesize'))
        audio = " +audio" if fmt.get('has_audio') else " video-only"
        print(f"    v{i:<2} | ID:{fmt.get('format_id'):<6} | {fmt.get('ext'):<4} | "
              f"{res}{fps:<10} | {size:<10} |{audio}")

    sub_banner("AUDIO FORMATS")
    for i, fmt in enumerate(audio_fmts, 1):
        br   = f"{int(fmt.get('tbr', 0))}kbps" if fmt.get('tbr') else "unknown"
        size = fmt_size(fmt.get('filesize'))
        lang = fmt.get('language') or ''
        print(f"    a{i:<2} | ID:{fmt.get('format_id'):<6} | {fmt.get('ext'):<4} | "
              f"{br:<10} | {size:<10} | {lang}")

    sub_banner(f"SUBTITLES ({len(subtitles)} available)")
    langs = sorted({s['language'] for s in subtitles})
    if langs:
        print(f"    {', '.join(langs[:20])}")

    return data


# ─── Phase 2 — Select format ────────────────────────────────────────────────

def phase_2_select_format(info):
    banner("Phase 2 · Select Format")
    
    video_fmts = info.get('formats', {}).get('video', [])
    audio_fmts = info.get('formats', {}).get('audio', [])
    
    choice = input(
        "\n  Enter format selection (e.g. v17 for 1080p, a3 for audio):\n  Selection: "
    ).strip().lower()

    selected_fmt  = None
    is_audio_only = False

    if choice.startswith('v'):
        try:
            selected_fmt = video_fmts[int(choice[1:]) - 1]
        except (ValueError, IndexError):
            print("  ✗ Invalid choice.")
            sys.exit(1)
    elif choice.startswith('a'):
        try:
            selected_fmt  = audio_fmts[int(choice[1:]) - 1]
            is_audio_only = True
        except (ValueError, IndexError):
            print("  ✗ Invalid choice.")
            sys.exit(1)
    else:
        print("  ✗ Invalid input format.")
        sys.exit(1)
    
    format_id = selected_fmt['format_id']
    stream_url = selected_fmt.get('stream_url', '')
    print(f"\n  ✓ Selected format: {format_id} ({'audio' if is_audio_only else 'video'})")
    return format_id, is_audio_only, stream_url


# ─── Phase 3 — Select delivery method ───────────────────────────────────────

def phase_3_select_delivery_method():
    banner("Phase 3 · Select Delivery Method")
    
    print("\n  Delivery methods:")
    print("    1 — Live CDN Proxy Stream (direct on-the-fly streaming via GET /api/stream)")
    print("    2 — Direct Server Download (downloads, process-merges/converts, and streams back via POST /api/download)")
    
    mode = input("\n  Select method [1/2, default=2]: ").strip() or '2'
    if mode not in ('1', '2'):
        mode = '2'
    
    method_names = {
        '1': 'Live CDN Proxy Stream',
        '2': 'Direct Server Download'
    }
    print(f"  ✓ Selected: {method_names[mode]}")
    return mode


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    check_system()

    # ── Phase 1: Extract metadata ──────────────────────────────────────────
    info     = phase_1_extract()
    title    = info['title']

    # ── Phase 2: Select format ─────────────────────────────────────────────
    format_id, is_audio_only, stream_url = phase_2_select_format(info)

    # ── Phase 3: Select delivery method ────────────────────────────────────
    delivery_mode = phase_3_select_delivery_method()

    # ── Phase 4: Execute selected delivery method ──────────────────────────
    ext = 'mp3' if is_audio_only else 'mp4'
    filename = sanitize_filename(title or 'download') + f'.{ext}'

    if delivery_mode == '1':
        # Live CDN Proxy Stream (Mode B)
        banner("Phase 4 · Live CDN Proxy Stream")
        # Append cookies to the stream URL as a query param if provided
        if YOUTUBE_COOKIES:
            sep = '&' if '?' in stream_url else '?'
            import urllib.parse
            stream_url = stream_url + sep + 'cookies=' + urllib.parse.quote(YOUTUBE_COOKIES)
        download_file_from_url(stream_url, filename)

    elif delivery_mode == '2':
        # Direct Server Download
        banner("Phase 4 · Direct Server Download")
        payload = {
            "url": TEST_VIDEO_URL,
            "format_id": format_id,
            "is_audio_only": is_audio_only,
            "embed_metadata": True,
            "embed_thumbnail": True,
        }
        if YOUTUBE_COOKIES:
            payload["cookies"] = YOUTUBE_COOKIES
        url = f"{BASE_URL}/api/download"
        download_file_from_url(url, filename, method="POST", payload=payload)

    banner("✓ All tests complete")


if __name__ == "__main__":
    main()