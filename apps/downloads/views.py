import logging
import os
import tempfile
import threading
import time
import uuid
import urllib.parse
import shutil
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from django.conf import settings
from django.http import StreamingHttpResponse, Http404

from .serializers import ExtractRequestSerializer, DownloadRequestSerializer
from .services.engine import DownloadEngine

logger = logging.getLogger('api')


def create_cookie_file(cookies: str):
    """
    Writes a Netscape-format cookie string to a temporary file that
    yt-dlp can consume via --cookies.  Returns the file path, or None
    if `cookies` is empty/None.  Caller is responsible for deleting the
    file once it is no longer needed.
    """
    if not cookies or not cookies.strip():
        return None
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="zfetch_cookie_")
    try:
        with os.fdopen(fd, "w") as f:
            # Ensure Netscape header is present (yt-dlp requires it)
            if not cookies.strip().startswith("# Netscape HTTP Cookie File"):
                f.write("# Netscape HTTP Cookie File\n")
            f.write(cookies)
    except Exception:
        os.remove(path)
        raise
    return path


def get_active_download_file(temp_dir):
    """
    Scans the temporary directory for the active growing download file.
    Prioritizes finished/renamed media files, then active .part files,
    sorting by size to choose the main media stream.
    """
    if not os.path.exists(temp_dir):
        return None
    try:
        files = os.listdir(temp_dir)
    except OSError:
        return None
    if not files:
        return None

    media_files = []
    part_files = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        # Skip metadata, subtitles, thumbnails
        if ext in ['.jpg', '.png', '.webp', '.vtt', '.srt', '.ass', '.json']:
            continue
        file_path = os.path.join(temp_dir, f)
        if ext == '.part':
            part_files.append(file_path)
        elif ext in ['.mp3', '.m4a', '.mp4', '.mkv', '.webm', '.opus', '.ogg', '.flac']:
            media_files.append(file_path)

    if media_files:
        return max(media_files, key=os.path.getsize)
    if part_files:
        return max(part_files, key=os.path.getsize)
    return None


# ─── Throttle classes ────────────────────────────────────────────────────────

class AnalyzeThrottle(AnonRateThrottle):
    scope = 'analyze_url'


class DownloadThrottle(AnonRateThrottle):
    scope = 'start_download'


# ═══════════════════════════════════════════════════════════════════════════════
#  STATELESS DaaS WORKFLOW
# ═══════════════════════════════════════════════════════════════════════════════

class ExtractMetadataView(APIView):
    """
    POST /api/extract
    ─────────────────
    Extracts metadata from a YouTube URL and returns a list of formatted streams.
    Injects a query-string-based stateless stream_url for live proxying.
    """
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [AnalyzeThrottle]

    def post(self, request):
        serializer = ExtractRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        url = serializer.validated_data['url']
        cookies = serializer.validated_data.get('cookies')

        cookie_file = create_cookie_file(cookies)
        try:
            engine = DownloadEngine()
            info = engine.analyze_info(url, cookies_path=cookie_file)
            clean = engine.build_clean_info(info)

            is_playlist = info.get('_type') == 'playlist' or 'entries' in info

            # Generate stateless stream/download URLs for each format pointing to StreamView,
            # while keeping the raw CDN URL intact in 'url'.
            base_uri = request.build_absolute_uri('/')
            for group in ['video', 'audio']:
                for fmt in clean['formats'].get(group, []):
                    params = urllib.parse.urlencode({
                        'url': url,
                        'format_id': fmt['format_id']
                    })
                    # Provide a backend proxy direct download link
                    fmt['download_url'] = f"{base_uri.rstrip('/')}/api/stream?{params}"
                    # Keep stream_url for compatibility
                    fmt['stream_url'] = fmt['download_url']

            response_data = {
                **clean,
                'is_playlist': is_playlist,
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as exc:
            logger.error(f"Extraction failed for URL {url}: {exc}")
            return Response(
                {"error": f"Failed to extract media info: {str(exc)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        finally:
            if cookie_file and os.path.exists(cookie_file):
                os.remove(cookie_file)


class StreamView(APIView):
    """
    GET /api/stream?url=<encoded_url>&format_id=<id>
    ───────────────────────────────────────────────
    Live proxy streaming server.
    Extracts a fresh CDN link and streams chunks directly to the client.
    """
    permission_classes = (permissions.AllowAny,)
    CHUNK_SIZE = 65536

    _YT_HEADERS = {
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

    def get(self, request):
        import requests as req
        url = request.query_params.get('url')
        format_id = request.query_params.get('format_id')
        cookies = request.query_params.get('cookies')

        if not url or not format_id:
            return Response(
                {"error": "Missing 'url' or 'format_id' query parameters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        logger.info(f"StreamView: extracting fresh CDN URL for format {format_id}")
        cookie_file = create_cookie_file(cookies)
        try:
            engine = DownloadEngine()
            info = engine.analyze_info(url, cookies_path=cookie_file)
        except Exception as exc:
            logger.error(f"StreamView analyze failed: {exc}")
            return Response(
                {'error': 'Failed to resolve stream URL.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            if cookie_file and os.path.exists(cookie_file):
                os.remove(cookie_file)

        cdn_url = None
        chosen_fmt = None
        for fmt in info.get('formats', []):
            if str(fmt.get('format_id')) == str(format_id):
                cdn_url = fmt.get('url')
                chosen_fmt = fmt
                break

        if not cdn_url:
            return Response(
                {'error': f"Format '{format_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        ext = chosen_fmt.get('ext', 'mp4')
        title = info.get('title', 'download')
        safe_title = ''.join(
            c for c in title if c.isalnum() or c in ' _-'
        ).strip() or 'download'
        filename = f'{safe_title}.{ext}'

        headers = dict(self._YT_HEADERS)
        if 'HTTP_RANGE' in request.META:
            headers['Range'] = request.META['HTTP_RANGE']

        try:
            upstream = req.get(cdn_url, stream=True, headers=headers, timeout=(15, 120))
            upstream.raise_for_status()
        except req.RequestException as exc:
            logger.error(f"StreamView: CDN request failed: {exc}")
            return Response(
                {'error': 'Failed to fetch stream from CDN.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        def stream_chunks():
            try:
                for chunk in upstream.iter_content(chunk_size=self.CHUNK_SIZE):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        proxy_status = upstream.status_code
        resp = StreamingHttpResponse(
            stream_chunks(),
            status=proxy_status,
            content_type=upstream.headers.get('Content-Type', 'application/octet-stream'),
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        if 'Content-Length' in upstream.headers:
            resp['Content-Length'] = upstream.headers['Content-Length']
        if 'Content-Range' in upstream.headers:
            resp['Content-Range'] = upstream.headers['Content-Range']
        resp['Accept-Ranges'] = 'bytes'

        return resp


class DownloadView(APIView):
    """
    POST /api/download
    ──────────────────
    Triggers download using yt-dlp and streams the growing media file back to the client.
    Self-cleans the temporary working directory once the stream terminates.
    """
    permission_classes = (permissions.AllowAny,)
    throttle_classes = [DownloadThrottle]
    CHUNK_SIZE = 64 * 1024

    def post(self, request):
        serializer = DownloadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        url = data['url']
        format_id = data['format_id']
        is_audio_only = data.get('is_audio_only', False)
        cookies = data.get('cookies')

        try:
            # 1. Fetch metadata to build exact output filename
            cookie_file = create_cookie_file(cookies)
            try:
                engine = DownloadEngine()
                info = engine.analyze_info(url, cookies_path=cookie_file)
                clean = engine.build_clean_info(info)
            finally:
                if cookie_file and os.path.exists(cookie_file):
                    os.remove(cookie_file)

            title = clean['title']
            chosen_ext = 'mp4'
            for group in ['video', 'audio']:
                for fmt in clean['formats'].get(group, []):
                    if str(fmt.get('format_id')) == str(format_id):
                        chosen_ext = fmt.get('ext', 'mp4')
                        break

            ext = data.get('audio_format', 'mp3') if is_audio_only else chosen_ext
            safe_title = ''.join(c for c in title if c.isalnum() or c in ' _-').strip() or 'download'
            filename = f"{safe_title}.{ext}"

            # 2. Setup unique temp directory
            download_id = str(uuid.uuid4())
            temp_dir = os.path.join(settings.TEMP_DOWNLOADS_ROOT, download_id)
            os.makedirs(temp_dir, exist_ok=True)
            output_template = os.path.join(temp_dir, "media.%(ext)s")

            # 3. State tracker and background download thread
            state = {
                'status': 'starting',
                'error': None,
                'completed': False
            }

            def progress_callback(progress_data):
                state.update(progress_data)

            def run_download():
                # Build a fresh cookie file for the download subprocess
                dl_cookie_file = create_cookie_file(cookies)
                # Pass the path into options so download_by_format_id can use it
                merged_options = {**data, 'cookies_path': dl_cookie_file or ''}
                try:
                    engine.download_by_format_id(
                        url=url,
                        format_id=format_id,
                        output_template=output_template,
                        options=merged_options,
                        progress_callback=progress_callback,
                        job_id=download_id,
                        is_audio_only=is_audio_only,
                    )
                    state['status'] = 'completed'
                    state['completed'] = True
                except Exception as e:
                    state['status'] = 'failed'
                    state['error'] = str(e)
                finally:
                    if dl_cookie_file and os.path.exists(dl_cookie_file):
                        os.remove(dl_cookie_file)

            thread = threading.Thread(target=run_download, daemon=True)
            thread.start()

            # 4. Wait for download to start and create the growing file
            wait_deadline = time.time() + 30
            active_file = None
            while not active_file and time.time() < wait_deadline:
                if state['status'] == 'failed':
                    return Response(
                        {"error": f"Download failed to start: {state['error']}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                active_file = get_active_download_file(temp_dir)
                if not active_file:
                    time.sleep(0.5)

            if not active_file:
                # Force kill process in environment if timeout
                if f"ZFETCH_PID_{download_id}" in os.environ:
                    try:
                        pid = int(os.environ[f"ZFETCH_PID_{download_id}"])
                        os.kill(pid, 9)
                        del os.environ[f"ZFETCH_PID_{download_id}"]
                    except Exception:
                        pass
                shutil.rmtree(temp_dir, ignore_errors=True)
                return Response(
                    {"error": "Download failed to write output within 30 seconds."},
                    status=status.HTTP_504_TIMEOUT
                )

            # 5. Build the tail-streaming response generator
            def growing_file_iterator(initial_path, temp_path):
                current_offset = 0
                fd = None
                try:
                    while True:
                        if state['status'] == 'failed':
                            raise Exception(f"Download thread failed: {state['error']}")

                        active_path = initial_path
                        if not os.path.exists(active_path):
                            resolved = get_active_download_file(temp_path)
                            if resolved:
                                active_path = resolved
                            else:
                                # Search for final post-processed file
                                files = [os.path.join(temp_path, f) for f in os.listdir(temp_path)
                                         if os.path.splitext(f)[1].lower() in ['.mp3', '.m4a', '.mp4', '.mkv', '.webm', '.opus', '.ogg', '.flac']]
                                if files:
                                    active_path = max(files, key=os.path.getsize)

                        if not os.path.exists(active_path):
                            time.sleep(0.2)
                            continue

                        if fd is None or fd.name != active_path:
                            if fd:
                                fd.close()
                            fd = open(active_path, 'rb')
                            fd.seek(current_offset)

                        current_size = os.path.getsize(active_path)
                        if current_size > current_offset:
                            chunk = fd.read(self.CHUNK_SIZE)
                            if chunk:
                                yield chunk
                                current_offset += len(chunk)
                        else:
                            if state['status'] == 'completed':
                                if os.path.getsize(active_path) <= current_offset:
                                    break
                            time.sleep(0.2)
                finally:
                    if fd:
                        fd.close()
                    # Clean up process environment variable if any
                    if f"ZFETCH_PID_{download_id}" in os.environ:
                        del os.environ[f"ZFETCH_PID_{download_id}"]
                    # Delete the temporary directory
                    shutil.rmtree(temp_path, ignore_errors=True)
                    logger.info(f"Stateless cleanup: removed temp workspace {temp_path}")

            response = StreamingHttpResponse(
                growing_file_iterator(active_file, temp_dir),
                content_type='application/octet-stream'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response

        except Exception as exc:
            logger.error(f"Download request failed: {exc}")
            return Response(
                {"error": f"Failed to start download: {str(exc)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
