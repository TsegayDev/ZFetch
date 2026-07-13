from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

class SystemAPITests(APITestCase):
    def test_version_endpoint_is_public(self):
        response = self.client.get(reverse('system_version'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('name', response.data)
        self.assertIn('version', response.data)
        self.assertEqual(response.data['name'], 'ZFetch Backend')

    def test_status_endpoint_returns_system_info(self):
        response = self.client.get(reverse('system_status'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('disk', response.data)

    def test_dependencies_endpoint_returns_tools(self):
        response = self.client.get(reverse('system_dependencies'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('yt-dlp', response.data)
        self.assertIn('ffmpeg', response.data)
        self.assertIn('aria2c', response.data)
