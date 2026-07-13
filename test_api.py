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
import json
import shutil
import sqlite3
import tempfile
import base64
import subprocess
import requests
from pathlib import Path
from typing import Optional, Dict, List

#BASE_URL = "https://zfetch-production.up.railway.app/"
BASE_URL = "http://127.0.0.1:8000/"
TEST_VIDEO_URL = "https://youtu.be/S9g4HY9BbEY?si=TW_QqyBuBeDfEZch"  # Helen Meles

# ─── Robust Cookie Extraction ────────────────────────────────────────────────
# Provides multiple methods to extract YouTube cookies:
#   1. YOUTUBE_COOKIES env var — paste the full Netscape cookie string
#   2. cookies.txt file — place a Netscape-format cookies.txt next to this script
#   3. Browser cookie extraction (Chrome, Firefox, Edge, Brave, Opera)
#   4. yt-dlp's built-in --cookies-from-browser functionality

class CookieExtractor:
    """Robust cookie extraction from multiple sources"""
    
    # Common browser cookie locations (Linux)
    BROWSER_COOKIE_PATHS = {
        'chrome': [
            Path.home() / '.config/google-chrome/Default/Cookies',
            Path.home() / '.config/google-chrome/Profile */Cookies',
            Path.home() / '.var/app/com.google.Chrome/config/google-chrome/Default/Cookies',
        ],
        'chromium': [
            Path.home() / '.config/chromium/Default/Cookies',
            Path.home() / '.var/app/org.chromium.Chromium/config/chromium/Default/Cookies',
        ],
        'firefox': [
            Path.home() / '.mozilla/firefox/*.default-release/cookies.sqlite',
            Path.home() / '.mozilla/firefox/*.default/cookies.sqlite',
            Path.home() / '.var/app/org.mozilla.firefox/.mozilla/firefox/*.default-release/cookies.sqlite',
        ],
        'brave': [
            Path.home() / '.config/BraveSoftware/Brave-Browser/Default/Cookies',
            Path.home() / '.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default/Cookies',
        ],
        'edge': [
            Path.home() / '.config/microsoft-edge/Default/Cookies',
            Path.home() / '.var/app/com.microsoft.Edge/config/microsoft-edge/Default/Cookies',
        ],
        'opera': [
            Path.home() / '.config/opera/Default/Cookies',
            Path.home() / '.var/app/com.opera.Opera/config/opera/Default/Cookies',
        ],
        'vivaldi': [
            Path.home() / '.config/vivaldi/Default/Cookies',
            Path.home() / '.var/app/com.vivaldi.Vivaldi/config/vivaldi/Default/Cookies',
        ],
    }
    
    @staticmethod
    def find_browser_cookie_files() -> Dict[str, List[Path]]:
        """Find available browser cookie databases"""
        found = {}
        for browser, patterns in CookieExtractor.BROWSER_COOKIE_PATHS.items():
            for pattern in patterns:
                try:
                    rel_pattern = pattern.relative_to(Path.home())
                    matches = list(Path.home().glob(str(rel_pattern)))
                except ValueError:
                    matches = [pattern] if pattern.exists() else []
                matches = [m for m in matches if m.exists()]
                if matches:
                    if browser not in found:
                        found[browser] = []
                    found[browser].extend(matches)
        return found
    
    @staticmethod
    def extract_chrome_cookies(cookie_db: Path) -> str:
        """Extract YouTube cookies from Chrome/Chromium cookie database"""
        try:
            # Create a temporary copy to avoid locking issues
            with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
                shutil.copy2(cookie_db, tmp.name)
                tmp_path = tmp.name
            
            conn = sqlite3.connect(tmp_path)
            conn.text_factory = bytes
            cursor = conn.cursor()
            
            # Query YouTube cookies
            cursor.execute("""
                SELECT host_key, name, value, path, expires_utc, is_secure, is_httponly
                FROM cookies
                WHERE host_key LIKE '%youtube.com'
            """)
            
            cookies = []
            for row in cursor.fetchall():
                host_key = row[0].decode('utf-8', errors='replace')
                name = row[1].decode('utf-8', errors='replace')
                value = row[2].decode('utf-8', errors='replace')
                path = row[3].decode('utf-8', errors='replace')
                expires = row[4]
                is_secure = row[5]
                is_httponly = row[6]
                
                # Convert to Netscape format
                if not value or value.startswith('v10') or value.startswith('v11') or not value.strip():
                    continue
                secure_flag = "TRUE" if is_secure else "FALSE"
                httponly_flag = "TRUE" if is_httponly else "FALSE"
                expires_str = str(expires) if expires else "0"
                
                cookies.append(
                    f"{host_key}\tTRUE\t{path}\t{secure_flag}\t{expires_str}\t{name}\t{value}"
                )
            
            conn.close()
            os.unlink(tmp_path)
            
            if cookies:
                return "# Netscape HTTP Cookie File\n# Extracted from Chrome/Chromium\n\n" + "\n".join(cookies)
            return ""
            
        except Exception as e:
            print(f"  [cookies] Failed to extract Chrome cookies: {e}")
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return ""
    
    @staticmethod
    def extract_firefox_cookies(cookie_db: Path) -> str:
        """Extract YouTube cookies from Firefox cookie database"""
        try:
            # Create a temporary copy
            with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
                shutil.copy2(cookie_db, tmp.name)
                tmp_path = tmp.name
            
            conn = sqlite3.connect(tmp_path)
            cursor = conn.cursor()
            
            # Query YouTube cookies
            cursor.execute("""
                SELECT host, name, value, path, expiry, isSecure, isHttpOnly
                FROM moz_cookies
                WHERE host LIKE '%youtube.com'
            """)
            
            cookies = []
            for row in cursor.fetchall():
                host = row[0]
                name = row[1]
                value = row[2]
                path = row[3]
                expiry = row[4]
                is_secure = row[5]
                is_httponly = row[6]
                
                secure_flag = "TRUE" if is_secure else "FALSE"
                httponly_flag = "TRUE" if is_httponly else "FALSE"
                expires_str = str(expiry) if expiry else "0"
                
                cookies.append(
                    f"{host}\tTRUE\t{path}\t{secure_flag}\t{expires_str}\t{name}\t{value}"
                )
            
            conn.close()
            os.unlink(tmp_path)
            
            if cookies:
                return "# Netscape HTTP Cookie File\n# Extracted from Firefox\n\n" + "\n".join(cookies)
            return ""
            
        except Exception as e:
            print(f"  [cookies] Failed to extract Firefox cookies: {e}")
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return ""
    
    @staticmethod
    def extract_using_ytdlp() -> Optional[str]:
        """Use yt-dlp's built-in browser cookie extraction"""
        browsers_to_try = ['chrome', 'firefox', 'brave', 'edge', 'opera', 'chromium', 'vivaldi']
        
        for browser in browsers_to_try:
            try:
                print(f"  [cookies] Trying to extract from {browser} using yt-dlp...")
                
                # Check if yt-dlp is available
                if shutil.which('yt-dlp'):
                    cmd = ['yt-dlp', '--cookies-from-browser', browser, '--cookies', '/dev/stdout', 
                           '--skip-download', '--quiet', 'https://www.youtube.com']
                    
                    result = subprocess.run(
                        cmd, 
                        capture_output=True, 
                        text=True, 
                        timeout=10,
                        env={**os.environ, 'PYTHONUNBUFFERED': '1'}
                    )
                    
                    if result.returncode == 0 and result.stdout.strip():
                        # Check if output looks like Netscape cookies
                        if '.youtube.com' in result.stdout and '\t' in result.stdout:
                            return result.stdout.strip()
                    else:
                        print(f"    yt-dlp {browser} extraction failed: {result.stderr[:100]}")
                
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                print(f"    {browser} extraction timed out")
            except Exception as e:
                print(f"    {browser} extraction error: {e}")
        
        return None
    
    @staticmethod
    def extract_using_python_browsercookie() -> Optional[str]:
        """Try using the browser_cookie3 library if available"""
        try:
            import browser_cookie3
            
            browsers_to_try = [
                ('chrome', browser_cookie3.chrome),
                ('firefox', browser_cookie3.firefox),
                ('brave', browser_cookie3.brave),
                ('edge', browser_cookie3.edge),
                ('opera', browser_cookie3.opera),
            ]
            
            for browser_name, loader in browsers_to_try:
                try:
                    print(f"  [cookies] Trying to extract from {browser_name} using browser_cookie3...")
                    cj = loader(domain_name='youtube.com')
                    
                    cookies = []
                    for cookie in cj:
                        if 'youtube.com' in cookie.domain:
                            # Skip empty or encrypted cookie values (Chrome uses v10/v11 prefixes on Linux/Windows for encrypted bytes)
                            val = cookie.value
                            if not val or val.startswith('v10') or val.startswith('v11') or not val.strip():
                                continue
                            secure_flag = "TRUE" if cookie.secure else "FALSE"
                            expires = str(int(cookie.expires)) if cookie.expires else "0"
                            
                            cookies.append(
                                f"{cookie.domain}\tTRUE\t{cookie.path}\t{secure_flag}"
                                f"\t{expires}\t{cookie.name}\t{val}"
                            )
                    
                    if cookies:
                        return "# Netscape HTTP Cookie File\n" + "\n".join(cookies)
                except Exception:
                    continue
                    
        except ImportError:
            pass
        
        return None


def load_cookies() -> str:
    """
    Robust cookie loading with multiple fallback methods.
    Returns a Netscape-format cookie string, or an empty string if none found.
    """
    print("\n  [cookies] Attempting to load YouTube cookies...")
    
    # Method 1: Environment variable
    env_cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if env_cookies:
        # Check if it's base64 encoded (for easier copy-paste)
        try:
            decoded = base64.b64decode(env_cookies).decode('utf-8')
            if '.youtube.com' in decoded and '\t' in decoded:
                print("  [cookies] ✓ Loaded from YOUTUBE_COOKIES env var (base64 decoded)")
                return decoded
        except Exception:
            pass
        
        if '.youtube.com' in env_cookies:
            print("  [cookies] ✓ Loaded from YOUTUBE_COOKIES env var")
            return env_cookies
    
    # Method 2: cookies.txt file in script directory
    cookie_files = [
        Path(__file__).parent / "cookies.txt",
        Path(__file__).parent / "youtube_cookies.txt",
        Path.home() / "cookies.txt",
        Path.home() / "youtube_cookies.txt",
    ]
    
    for cookie_file in cookie_files:
        if cookie_file.exists():
            try:
                content = cookie_file.read_text(encoding='utf-8').strip()
                if content and '.youtube.com' in content:
                    print(f"  [cookies] ✓ Loaded from {cookie_file}")
                    return content
            except Exception as e:
                print(f"  [cookies] Failed to read {cookie_file}: {e}")
    
    # Method 3: Extract from browsers using yt-dlp
    ytdlp_cookies = CookieExtractor.extract_using_ytdlp()
    if ytdlp_cookies:
        print("  [cookies] ✓ Extracted using yt-dlp browser integration")
        return ytdlp_cookies
    
    # Method 4: Extract using Python browser_cookie3 library
    python_cookies = CookieExtractor.extract_using_python_browsercookie()
    if python_cookies:
        print("  [cookies] ✓ Extracted using Python browser_cookie3")
        return python_cookies
    
    # Method 5: Direct SQLite extraction from browser cookie databases
    browser_files = CookieExtractor.find_browser_cookie_files()
    
    # Try Chrome-based browsers first (easier format)
    for browser in ['chrome', 'chromium', 'brave', 'edge', 'opera', 'vivaldi']:
        if browser in browser_files:
            for cookie_db in browser_files[browser]:
                print(f"  [cookies] Attempting direct extraction from {browser} at {cookie_db}")
                cookies = CookieExtractor.extract_chrome_cookies(cookie_db)
                if cookies and '.youtube.com' in cookies:
                    print(f"  [cookies] ✓ Successfully extracted from {browser}")
                    return cookies
    
    # Try Firefox
    if 'firefox' in browser_files:
        for cookie_db in browser_files['firefox']:
            print(f"  [cookies] Attempting Firefox extraction from {cookie_db}")
            cookies = CookieExtractor.extract_firefox_cookies(cookie_db)
            if cookies and '.youtube.com' in cookies:
                print("  [cookies] ✓ Successfully extracted from Firefox")
                return cookies
    
    print("  [cookies] ✗ No cookies found — proceeding without authentication")
    print("  [cookies] 💡 To fix, either:")
    print("    1. Export cookies: yt-dlp --cookies-from-browser chrome --cookies cookies.txt")
    print("    2. Set env var: export YOUTUBE_COOKIES='...paste cookies here...'")
    print("    3. Install browser_cookie3: pip install browser-cookie3")
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
        print(f"\n  [cookies] Including {len(YOUTUBE_COOKIES)} bytes of cookie data")
    
    r = requests.post(f"{BASE_URL}/api/extract", json=payload, timeout=60)
    elapsed = time.time() - t0

    if r.status_code != 200:
        print("FAILED")
        print(f"  {r.status_code}: {r.text}")
        
        # Provide helpful error message
        if "Sign in to confirm" in r.text or "bot" in r.text:
            print("\n  💡 YouTube bot detection triggered. To resolve:")
            print("    1. Export cookies: yt-dlp --cookies-from-browser chrome --cookies cookies.txt")
            print("    2. Place cookies.txt in the same directory as this script")
            print("    3. Or set YOUTUBE_COOKIES environment variable")
        
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