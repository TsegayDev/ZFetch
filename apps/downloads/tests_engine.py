import os
import tempfile
from unittest.mock import patch, MagicMock
from django.test import TestCase

from apps.downloads.services.engine import DownloadEngine


class DownloadEngineTests(TestCase):

    def test_sanitize_best_video_format(self):
        args = DownloadEngine.sanitize_args({'quality': 'best'})
        self.assertIn('-f', args)
        idx = args.index('-f')
        self.assertEqual(args[idx + 1], 'bestvideo+bestaudio/best')

    def test_sanitize_720p_video_format(self):
        args = DownloadEngine.sanitize_args({'quality': '720p'})
        self.assertIn('-f', args)
        idx = args.index('-f')
        self.assertIn('720', args[idx + 1])

    def test_sanitize_audio_extraction(self):
        args = DownloadEngine.sanitize_args({'type': 'audio', 'audio_format': 'mp3'})
        self.assertIn('-x', args)
        self.assertIn('--audio-format', args)
        idx = args.index('--audio-format')
        self.assertEqual(args[idx + 1], 'mp3')

    def test_sanitize_subtitle_languages(self):
        args = DownloadEngine.sanitize_args({'subtitle_languages': ['en', 'fr']})
        self.assertIn('--write-subs', args)
        self.assertIn('--sub-langs', args)
        idx = args.index('--sub-langs')
        self.assertEqual(args[idx + 1], 'en,fr')

    def test_sanitize_sponsorblock(self):
        args = DownloadEngine.sanitize_args({'sponsorblock': True})
        self.assertIn('--sponsorblock-remove', args)

    def test_sanitize_blocks_exec_injection(self):
        args = DownloadEngine.sanitize_args({'custom_args': ['--exec', 'evil_command']})
        self.assertNotIn('--exec', args)
        self.assertNotIn('evil_command', args)

    def test_sanitize_aria2c_external_downloader(self):
        args = DownloadEngine.sanitize_args({'use_aria2c': True})
        self.assertIn('--external-downloader', args)
        idx = args.index('--external-downloader')
        self.assertEqual(args[idx + 1], 'aria2c')

    def test_sanitize_rate_limit(self):
        args = DownloadEngine.sanitize_args({'rate_limit': '1M'})
        self.assertIn('-r', args)
        idx = args.index('-r')
        self.assertEqual(args[idx + 1], '1M')

    def test_sanitize_browser_cookies_allowed(self):
        args = DownloadEngine.sanitize_args({'browser': 'firefox'})
        self.assertIn('--cookies-from-browser', args)
        idx = args.index('--cookies-from-browser')
        self.assertEqual(args[idx + 1], 'firefox')

    def test_sanitize_browser_cookies_blocks_unknown_browser(self):
        args = DownloadEngine.sanitize_args({'browser': 'unknown_browser_hack'})
        self.assertNotIn('--cookies-from-browser', args)

    def test_sanitize_embed_metadata(self):
        args = DownloadEngine.sanitize_args({'embed_metadata': True})
        self.assertIn('--embed-metadata', args)

    def test_sanitize_embed_chapters(self):
        args = DownloadEngine.sanitize_args({'embed_chapters': True})
        self.assertIn('--embed-chapters', args)

    @patch('apps.downloads.services.engine.subprocess.Popen')
    def test_analyze_url_success(self, mock_popen):
        """Test that analyze_url parses yt-dlp JSON output correctly."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (
            '{"title": "Test Video", "uploader": "Test Channel", "duration": 300}',
            ''
        )
        mock_popen.return_value = mock_process

        engine = DownloadEngine()
        result = engine.analyze_url('https://www.youtube.com/watch?v=test')

        self.assertEqual(result['title'], 'Test Video')
        self.assertEqual(result['uploader'], 'Test Channel')
        self.assertEqual(result['duration'], 300)

    @patch('apps.downloads.services.engine.subprocess.Popen')
    def test_analyze_url_failure_raises_exception(self, mock_popen):
        """Test that analyze_url raises an exception when yt-dlp returns error."""
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate.return_value = ('', 'ERROR: Unsupported URL')
        mock_popen.return_value = mock_process

        engine = DownloadEngine()
        with self.assertRaises(Exception) as ctx:
            engine.analyze_url('https://bad-url.invalid')
        self.assertIn('yt-dlp extraction failed', str(ctx.exception))
