import os
from unittest.mock import patch, MagicMock
from rest_framework import status
from rest_framework.test import APITestCase

MOCK_INFO = {
    "title": "Test Video",
    "uploader": "Test Channel",
    "channel": "Test Channel",
    "duration": 300,
    "thumbnail": "https://example.com/thumb.jpg",
    "thumbnails": [{"url": "https://example.com/thumb.jpg", "width": 1280, "height": 720}],
    "description": "A test video",
    "view_count": 42000,
    "like_count": 500,
    "upload_date": "20240101",
    "webpage_url": "https://www.youtube.com/watch?v=test",
    "formats": [
        {
            "format_id": "137",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "none",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "url": "https://googlevideo.com/video1080?id=abc",
            "filesize": 50_000_000,
            "tbr": 5000,
        },
        {
            "format_id": "140",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a",
            "url": "https://googlevideo.com/audio?id=abc",
            "filesize": 3_000_000,
            "tbr": 128,
            "abr": 128,
        },
    ],
    "subtitles": {},
    "automatic_captions": {},
    "chapters": [],
}


class StatelessAPITests(APITestCase):

    @patch('apps.downloads.views.DownloadEngine.analyze_info', return_value=MOCK_INFO)
    def test_extract_endpoint_returns_json(self, _mock):
        response = self.client.post(
            '/api/extract',
            {"url": "https://www.youtube.com/watch?v=test"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(data['title'], "Test Video")
        self.assertEqual(data['author'], "Test Channel")
        self.assertIn('formats', data)
        self.assertIn('video', data['formats'])
        self.assertIn('audio', data['formats'])
        self.assertEqual(data['formats']['video'][0]['format_id'], '137')
        self.assertIn('stream_url', data['formats']['video'][0])
        self.assertIn('download_url', data['formats']['video'][0])
        self.assertIn('url', data['formats']['video'][0])

    @patch('apps.downloads.views.DownloadEngine.analyze_info', side_effect=Exception("Network error"))
    def test_extract_fails_on_exception(self, _mock):
        response = self.client.post(
            '/api/extract',
            {"url": "https://www.youtube.com/watch?v=bad"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_extract_requires_url(self):
        response = self.client.post('/api/extract', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.downloads.views.DownloadEngine.analyze_info', return_value=MOCK_INFO)
    @patch('apps.downloads.views.get_active_download_file')
    @patch('apps.downloads.views.DownloadEngine.download_by_format_id')
    def test_download_starts_and_returns_stream(self, mock_download, mock_get_file, _mock_analyze):
        mock_get_file.return_value = "/tmp/test_download_media.mp4"
        
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=1024):
                with patch('apps.downloads.views.open', create=True) as mock_open:
                    mock_file = MagicMock()
                    mock_file.read.return_value = b"chunk"
                    mock_open.return_value = mock_file
                    response = self.client.post(
                        '/api/download',
                        {
                            "url": "https://www.youtube.com/watch?v=test",
                            "format_id": "137",
                        },
                        format='json',
                    )
                    self.assertEqual(response.status_code, status.HTTP_200_OK)
                    self.assertEqual(response['Content-Type'], 'application/octet-stream')

    @patch('apps.downloads.views.DownloadEngine.analyze_info', return_value=MOCK_INFO)
    @patch('requests.get')
    def test_stream_endpoint_proxies_chunks(self, mock_get, _mock_analyze):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'video/mp4', 'Content-Length': '1000'}
        mock_response.iter_content.return_value = [b'chunk1', b'chunk2']
        mock_get.return_value = mock_response

        response = self.client.get(
            '/api/stream',
            {"url": "https://www.youtube.com/watch?v=test", "format_id": "137"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'video/mp4')
        self.assertEqual(response['Content-Length'], '1000')
