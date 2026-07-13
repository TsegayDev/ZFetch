import json
import os
import re
import subprocess
import logging
import yt_dlp
from typing import Dict, Any, List, Callable, Optional

logger = logging.getLogger('downloads')


class DownloadEngine:
    """
    Wrapper around yt-dlp for secure and robust media extraction and downloading.

    Phase 1 — Metadata extraction: uses yt-dlp Python API (fast, in-process).
    Phase 2 — Download: uses subprocess.Popen for process-based cancellation.
    """

    PROGRESS_REGEX = re.compile(
        r'\[download\]\s+(?P<progress>[\d\.]+)%\s+of\s+(?P<size>[~\d\.]+\w+)\s+at\s+(?P<speed>[\d\.]+\w+/s)\s+ETA\s+(?P<eta>[\d:]+)'
    )
    ALT_PROGRESS_REGEX = re.compile(
        r'\[download\]\s+(?P<progress>[\d\.]+)%\s+at\s+(?P<speed>[\d\.]+\w+/s)\s+ETA\s+(?P<eta>[\d:]+)'
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 – Extraction (Python API, no download)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _player_client(has_cookies: bool) -> str:
        """
        Choose the best YouTube player-client based on auth mode.

        Without cookies → android
            The Android client hits a private API that needs no JS challenge
            and no login. Fast and reliable for public videos.

        With cookies → web
            The web client honours cookies for auth.
        """
        return 'web' if has_cookies else 'android'

    def analyze_info(self, url: str, cookies_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Extracts complete video metadata using the yt-dlp Python API.
        No file is downloaded. Returns the raw info dict.

        Uses the android player client for anonymous requests (avoids JS
        challenges) and the web client when cookies are supplied.
        Automatically falls back to the android client if web+cookies fails.
        """
        has_cookies = bool(cookies_path and os.path.exists(cookies_path))
        client = self._player_client(has_cookies)

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'socket_timeout': 15,
            'extract_flat': False,  # We want full format list
            'extractor_args': {'youtube': {'player_client': [client]}},
        }
        if has_cookies:
            ydl_opts['cookiefile'] = cookies_path

        logger.info(f"Extracting info (Python API, client={client}) for URL: {url}")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            return info
        except Exception as first_exc:
            # If web client with cookies failed, retry with android client
            if has_cookies:
                logger.warning(
                    f"Web client extraction failed ({first_exc}); "
                    "retrying with android client (no cookies)."
                )
                fallback_opts = {
                    **ydl_opts,
                    'extractor_args': {'youtube': {'player_client': ['android']}},
                }
                fallback_opts.pop('cookiefile', None)
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            raise

    @staticmethod
    def build_clean_info(info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts a raw yt-dlp info dict into a clean, frontend-friendly JSON.
        Formats are split into categorised 'video' and 'audio' lists.
        Direct CDN URLs are included for optional client-side downloading.
        """
        raw_formats = info.get('formats', [])

        video_formats = []
        audio_formats = []

        for fmt in raw_formats:
            fmt_id = fmt.get('format_id')
            ext = fmt.get('ext', '')
            vcodec = fmt.get('vcodec') or 'none'
            acodec = fmt.get('acodec') or 'none'
            direct_url = fmt.get('url', '')

            # Skip storyboard / mhtml thumbnails
            if ext in ('mhtml', 'storyboard') or not direct_url:
                continue

            base = {
                'format_id': fmt_id,
                'ext': ext,
                'filesize': fmt.get('filesize') or fmt.get('filesize_approx'),
                'tbr': fmt.get('tbr'),
                'url': direct_url,  # direct YouTube CDN URL for Mode A/B
            }

            if vcodec != 'none':
                # Has video stream (may or may not include audio)
                has_audio = acodec != 'none'
                video_formats.append({
                    **base,
                    'resolution': fmt.get('resolution') or (
                        f"{fmt.get('width', 0)}x{fmt.get('height', 0)}"
                        if fmt.get('width') else 'unknown'
                    ),
                    'width': fmt.get('width'),
                    'height': fmt.get('height'),
                    'fps': fmt.get('fps'),
                    'vcodec': vcodec,
                    'acodec': acodec,
                    'has_audio': has_audio,
                    'dynamic_range': fmt.get('dynamic_range'),
                    'container': fmt.get('container') or ext,
                })
            elif acodec != 'none':
                # Audio-only stream
                audio_formats.append({
                    **base,
                    'acodec': acodec,
                    'abr': fmt.get('abr'),
                    'asr': fmt.get('asr'),
                    'language': fmt.get('language'),
                })

        # Subtitles
        subtitles = []
        raw_subs = info.get('subtitles', {})
        auto_subs = info.get('automatic_captions', {})
        for lang, sub_entries in raw_subs.items():
            for entry in sub_entries:
                subtitles.append({
                    'language': lang,
                    'ext': entry.get('ext'),
                    'url': entry.get('url'),
                    'auto': False,
                })
        for lang, sub_entries in auto_subs.items():
            for entry in sub_entries:
                subtitles.append({
                    'language': lang,
                    'ext': entry.get('ext'),
                    'url': entry.get('url'),
                    'auto': True,
                })

        # Thumbnails sorted by resolution (largest first)
        thumbnails = sorted(
            info.get('thumbnails', []),
            key=lambda t: (t.get('width') or 0) * (t.get('height') or 0),
            reverse=True,
        )

        return {
            'title': info.get('title', 'Unknown Title'),
            'description': info.get('description'),
            'thumbnail': info.get('thumbnail') or (thumbnails[0].get('url') if thumbnails else None),
            'thumbnails': [
                {
                    'url': t.get('url'),
                    'width': t.get('width'),
                    'height': t.get('height'),
                }
                for t in thumbnails[:5]
            ],
            'duration': info.get('duration'),
            'author': info.get('uploader') or info.get('channel') or 'Unknown',
            'uploader_url': info.get('uploader_url') or info.get('channel_url'),
            'views': info.get('view_count'),
            'like_count': info.get('like_count'),
            'upload_date': info.get('upload_date'),
            'age_limit': info.get('age_limit'),
            'is_live': info.get('is_live', False),
            'webpage_url': info.get('webpage_url'),
            'chapters': info.get('chapters') or [],
            'formats': {
                'video': video_formats,
                'audio': audio_formats,
            },
            'subtitles': subtitles,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 – Legacy subprocess-based extraction (kept for compatibility)
    # ──────────────────────────────────────────────────────────────────────────

    def analyze_url(self, url: str, cookies_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Calls yt-dlp CLI to extract info without downloading (legacy method).

        Uses the android player client for anonymous requests and web for
        cookie-authenticated ones, with an automatic fallback to android.
        """
        has_cookies = bool(cookies_path and os.path.exists(cookies_path))
        client = self._player_client(has_cookies)

        def _build_cmd(c: str, with_cookies: bool) -> list:
            args = ['yt-dlp', '--dump-json', '--no-playlist', '--flat-playlist']
            args.extend(['--socket-timeout', '10'])
            args.extend(['--extractor-args', f'youtube:player_client={c}'])
            if with_cookies and cookies_path and os.path.exists(cookies_path):
                args.extend(['--cookies', cookies_path])
            args.append(url)
            return args

        def _run(cmd: list) -> tuple:
            logger.info(f"Running analyze cmd: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                raise Exception("URL analysis timed out.")
            return process.returncode, stdout, stderr

        try:
            cmd = _build_cmd(client, has_cookies)
            returncode, stdout, stderr = _run(cmd)

            if returncode != 0 and has_cookies:
                # Fallback to android client without cookies
                logger.warning(
                    f"Web client CLI extraction failed; retrying with android client."
                )
                cmd = _build_cmd('android', False)
                returncode, stdout, stderr = _run(cmd)

            if returncode != 0:
                logger.error(f"yt-dlp analyze failed: {stderr}")
                raise Exception(f"yt-dlp extraction failed: {stderr.strip()}")

            return json.loads(stdout)
        except Exception as e:
            logger.error(f"Error analyzing URL {url}: {str(e)}")
            raise e

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 – Download by format_id (new DaaS method)
    # ──────────────────────────────────────────────────────────────────────────

    def download_by_format_id(
        self,
        url: str,
        format_id: str,
        output_template: str,
        options: Dict[str, Any],
        progress_callback: Callable[[Dict[str, Any]], None],
        job_id: str,
        is_audio_only: bool = False,
        is_video_only: bool = False,
    ) -> str:
        """
        Downloads a specific format by format_id.

        - If the format is video-only (no embedded audio), yt-dlp automatically
          fetches the best audio stream and merges them via FFmpeg.
        - If it is progressive video, it downloads only the selected format_id.
        - If it is an audio-only format, it extracts and converts it correctly to mp3/m4a.
        - All options from the options dict are sanitised and applied.
        """
        cmd = ['yt-dlp']

        # Output template
        cmd.extend(['-o', output_template])

        # Standard flags
        cmd.extend(['--newline', '--no-warnings'])

        # Player client — android for anonymous, web for cookie-auth
        cookies_path = options.get('cookies_path', '')
        has_cookies = bool(cookies_path and os.path.exists(cookies_path))
        player_client = self._player_client(has_cookies)
        cmd.extend(['--extractor-args', f'youtube:player_client={player_client}'])

        if is_audio_only:
            # Audio download path: download specific format_id, then extract audio
            cmd.extend(['-f', format_id])
            cmd.extend(['-x', '--audio-format', options.get('audio_format', 'mp3')])
            cmd.extend(['--audio-quality', options.get('audio_quality', '0')])
        else:
            # Video path:
            if is_video_only:
                # Merge the chosen video-only format with the best audio stream
                cmd.extend(['-f', f'{format_id}+bestaudio/best'])
            else:
                # Format already contains audio, download format_id directly
                cmd.extend(['-f', format_id])

            # Merge output format container
            container = options.get('container', 'mp4')
            cmd.extend(['--merge-output-format', container])

        # Subtitles
        subtitle_langs = options.get('subtitle_languages', [])
        if subtitle_langs:
            cmd.append('--write-subs')
            if 'all' in subtitle_langs:
                cmd.append('--all-subs')
            else:
                cmd.extend(['--sub-langs', ','.join(subtitle_langs)])

        # Thumbnail
        if options.get('download_thumbnail', False):
            cmd.append('--write-thumbnail')

        # SponsorBlock
        if options.get('sponsorblock', False):
            cmd.extend(['--sponsorblock-remove', 'all'])

        # Metadata & chapters embedding
        if options.get('embed_metadata', False):
            cmd.append('--embed-metadata')
        if options.get('embed_chapters', False):
            cmd.append('--embed-chapters')
        if options.get('embed_thumbnail', False) or options.get('embed_album_art', False):
            cmd.append('--embed-thumbnail')

        # Cookies file (client sent)
        if has_cookies:
            cmd.extend(['--cookies', cookies_path])

        # Rate limit (Ensure NO speed limit is set unless requested)
        rate_limit = options.get('rate_limit', '')
        if rate_limit:
            cmd.extend(['-r', rate_limit])

        # Concurrent fragments: defaulted to 16 for high-speed download
        concurrent_fragments = options.get('concurrent_fragments', 16)
        cmd.extend(['--concurrent-fragments', str(concurrent_fragments)])

        # Retries
        cmd.extend(['--retries', str(options.get('retries', 3))])

        # Optional aria2c external downloader
        if options.get('use_aria2c', False):
            cmd.extend(['--external-downloader', 'aria2c'])
            cmd.extend(['--external-downloader-args', 'aria2c:-x 16 -s 16 -k 1M'])

        cmd.append(url)

        logger.info(f"[Job {job_id}] Executing: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Store PID for cancellation
        os.environ[f"ZFETCH_PID_{job_id}"] = str(process.pid)

        try:
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if not line:
                    continue

                match = self.PROGRESS_REGEX.search(line)
                if match:
                    progress_callback({
                        'status': 'downloading',
                        'progress': float(match.group('progress')),
                        'size': match.group('size'),
                        'speed': match.group('speed'),
                        'eta': match.group('eta'),
                    })
                else:
                    alt_match = self.ALT_PROGRESS_REGEX.search(line)
                    if alt_match:
                        progress_callback({
                            'status': 'downloading',
                            'progress': float(alt_match.group('progress')),
                            'speed': alt_match.group('speed'),
                            'eta': alt_match.group('eta'),
                        })

                if 'Merging formats into' in line or '[ffmpeg]' in line:
                    progress_callback({
                        'status': 'processing',
                        'progress': 100.0,
                        'speed': '0B/s',
                        'eta': '00:00',
                    })

            process.wait()

            if f"ZFETCH_PID_{job_id}" in os.environ:
                del os.environ[f"ZFETCH_PID_{job_id}"]

            if process.returncode != 0:
                raise Exception(f"yt-dlp terminated with return code {process.returncode}")

            return "Download completed successfully"

        except Exception as e:
            if process.poll() is None:
                process.kill()
            if f"ZFETCH_PID_{job_id}" in os.environ:
                del os.environ[f"ZFETCH_PID_{job_id}"]
            logger.error(f"[Job {job_id}] Download error: {str(e)}")
            raise e

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 – Legacy generic download (kept for backward compatibility)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def sanitize_args(options: Dict[str, Any]) -> List[str]:
        """
        Processes options dictionary into safe list of command arguments.
        """
        args = []

        quality = options.get('quality', 'best')

        if options.get('type') == 'audio':
            args.extend(['-x', '--audio-format', options.get('audio_format', 'mp3')])
            args.extend(['--audio-quality', options.get('audio_quality', '0')])
        else:
            if quality == 'best':
                args.extend(['-f', 'bestvideo+bestaudio/best'])
            elif quality == '1080p':
                args.extend(['-f', 'bestvideo[height<=1080]+bestaudio/best'])
            elif quality == '720p':
                args.extend(['-f', 'bestvideo[height<=720]+bestaudio/best'])
            elif quality == '480p':
                args.extend(['-f', 'bestvideo[height<=480]+bestaudio/best'])
            else:
                args.extend(['-f', 'best'])

        container = options.get('container')
        if container and options.get('type') != 'audio':
            args.extend(['--merge-output-format', container])

        subtitle_langs = options.get('subtitle_languages', [])
        if subtitle_langs:
            args.append('--write-subs')
            if 'all' in subtitle_langs:
                args.append('--all-subs')
            else:
                args.extend(['--sub-langs', ','.join(subtitle_langs)])

        if options.get('download_thumbnail', False):
            args.append('--write-thumbnail')

        if options.get('sponsorblock', False):
            args.append('--sponsorblock-remove')
            args.append('all')

        if options.get('embed_metadata', False):
            args.append('--embed-metadata')
        if options.get('embed_chapters', False):
            args.append('--embed-chapters')
        if options.get('embed_thumbnail', False) or options.get('embed_album_art', False):
            args.append('--embed-thumbnail')

        cookies_path = options.get('cookies_path')
        has_cookies = bool(cookies_path and os.path.exists(cookies_path))
        if has_cookies:
            args.extend(['--cookies', cookies_path])

        # Player client
        client = 'web' if has_cookies else 'android'
        args.extend(['--extractor-args', f'youtube:player_client={client}'])

        rate_limit = options.get('rate_limit')
        if rate_limit:
            args.extend(['-r', rate_limit])

        retries = options.get('retries', 3)
        args.extend(['--retries', str(retries)])

        concurrent_fragments = options.get('concurrent_fragments', 5)
        args.extend(['--concurrent-fragments', str(concurrent_fragments)])

        if options.get('use_aria2c', False):
            args.extend(['--external-downloader', 'aria2c'])
            args.extend(['--external-downloader-args', 'aria2c:-x 16 -s 16 -k 1M'])

        custom_args = options.get('custom_args', [])
        skip_next = False
        for arg in custom_args:
            if skip_next:
                skip_next = False
                continue
            if any(blocked in arg for blocked in ['--exec', '--postprocessor-args', 'alias']):
                if arg.startswith('-'):
                    skip_next = True
                continue
            args.append(arg)

        return args

    def download(
        self,
        url: str,
        output_template: str,
        options: Dict[str, Any],
        progress_callback: Callable[[Dict[str, Any]], None],
        job_id: str,
    ) -> str:
        """
        Legacy download execution using sanitize_args (used by existing /start endpoint).
        """
        cmd = ['yt-dlp']
        cmd.extend(['-o', output_template])
        cmd.extend(['--newline', '--no-warnings', '--verbose'])
        cmd.extend(self.sanitize_args(options))
        cmd.append(url)

        logger.info(f"Executing download command: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        os.environ[f"ZFETCH_PID_{job_id}"] = str(process.pid)

        try:
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if not line:
                    continue

                match = self.PROGRESS_REGEX.search(line)
                if match:
                    progress_callback({
                        'status': 'downloading',
                        'progress': float(match.group('progress')),
                        'size': match.group('size'),
                        'speed': match.group('speed'),
                        'eta': match.group('eta'),
                    })
                else:
                    alt_match = self.ALT_PROGRESS_REGEX.search(line)
                    if alt_match:
                        progress_callback({
                            'status': 'downloading',
                            'progress': float(alt_match.group('progress')),
                            'speed': alt_match.group('speed'),
                            'eta': alt_match.group('eta'),
                        })

                if 'Merging formats into' in line or 'ffmpeg' in line:
                    progress_callback({
                        'status': 'processing',
                        'progress': 100.0,
                        'speed': '0B/s',
                        'eta': '00:00',
                    })

            process.wait()

            if f"ZFETCH_PID_{job_id}" in os.environ:
                del os.environ[f"ZFETCH_PID_{job_id}"]

            if process.returncode != 0:
                raise Exception(f"Download terminated with return code {process.returncode}")

            return "Download completed successfully"

        except Exception as e:
            if process.poll() is None:
                process.kill()
            if f"ZFETCH_PID_{job_id}" in os.environ:
                del os.environ[f"ZFETCH_PID_{job_id}"]
            logger.error(f"Download execution error: {str(e)}")
            raise e
