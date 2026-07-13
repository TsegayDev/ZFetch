from django.urls import path, include
from apps.downloads.views import ExtractMetadataView, DownloadView, StreamView

urlpatterns = [
    # ── Stateless DaaS top-level routes ──────────────────────────────────────
    # Phase 1: POST /api/extract  → Extract metadata & format list
    path('api/extract', ExtractMetadataView.as_view(), name='api_extract'),
    # Phase 2: POST /api/download → Download a media format (returns binary file)
    path('api/download', DownloadView.as_view(), name='api_download'),
    # Phase 3: GET  /api/stream   → Live proxy stream of YouTube CDN URL
    path('api/stream', StreamView.as_view(), name='api_stream'),

    # ── System health & dependency routes ────────────────────────────────────
    path('api/system/', include('apps.system.urls')),
]
