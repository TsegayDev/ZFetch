import os
import tempfile
from unittest.mock import patch, MagicMock
from django.test import TestCase

from apps.downloads.services.metadata import MetadataEmbedder


class MetadataEmbedderTests(TestCase):

    def test_embed_returns_false_for_missing_file(self):
        """Embedding on a non-existent file should return False gracefully."""
        result = MetadataEmbedder.embed('/nonexistent/path/file.mp3', {'title': 'Test'})
        self.assertFalse(result)

    def test_embed_returns_false_for_unsupported_extension(self):
        """Unsupported extension should return False."""
        with tempfile.NamedTemporaryFile(suffix='.avi', delete=False) as f:
            tmp_path = f.name
        try:
            result = MetadataEmbedder.embed(tmp_path, {'title': 'Test'})
            self.assertFalse(result)
        finally:
            os.unlink(tmp_path)

    @patch('apps.downloads.services.metadata.MP3')
    @patch('apps.downloads.services.metadata.MetadataEmbedder.convert_to_jpeg')
    def test_embed_mp3_called_for_mp3_file(self, mock_convert, mock_mp3):
        """When file has .mp3 extension, _embed_mp3 should be invoked."""
        mock_convert.return_value = '/tmp/fake_cover.jpg'

        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            tmp_path = f.name

        try:
            mock_audio_instance = MagicMock()
            mock_audio_instance.tags = MagicMock()
            mock_mp3.return_value = mock_audio_instance

            with patch('builtins.open', MagicMock()):
                with patch('os.path.exists', return_value=True):
                    with patch.object(MetadataEmbedder, '_embed_mp3') as mock_embed_mp3:
                        MetadataEmbedder.embed(tmp_path, {'title': 'Test'}, '/fake/art.jpg')
                        mock_embed_mp3.assert_called_once()
        finally:
            os.unlink(tmp_path)
