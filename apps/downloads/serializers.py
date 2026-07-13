from rest_framework import serializers

class ExtractRequestSerializer(serializers.Serializer):
    url = serializers.URLField(required=True)

class DownloadRequestSerializer(serializers.Serializer):
    """
    Serializer for the stateless media download endpoint.
    All parameters are passed directly to the yt-dlp downloader engine.
    """
    url = serializers.URLField(required=True)
    format_id = serializers.CharField(required=True)

    # Whether the selected format is audio-only
    is_audio_only = serializers.BooleanField(default=False)

    # Audio re-encoding target (only applied when is_audio_only=True)
    audio_format = serializers.ChoiceField(
        choices=['mp3', 'm4a', 'flac', 'opus', 'ogg', 'aac'],
        default='mp3',
        required=False,
    )
    audio_quality = serializers.CharField(default='0', required=False)

    # Output container for video merge (e.g. "mp4", "mkv")
    container = serializers.CharField(default='mp4', required=False)

    # Subtitles
    subtitle_languages = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

    # Optional extras
    download_thumbnail = serializers.BooleanField(default=False)
    sponsorblock = serializers.BooleanField(default=False)
    embed_metadata = serializers.BooleanField(default=False)
    embed_chapters = serializers.BooleanField(default=False)
    embed_album_art = serializers.BooleanField(default=False)
    embed_thumbnail = serializers.BooleanField(default=False)
    use_aria2c = serializers.BooleanField(default=False)
    browser = serializers.CharField(required=False, allow_blank=True, default='')
    rate_limit = serializers.CharField(required=False, allow_blank=True, default='')
    custom_args = serializers.ListField(child=serializers.CharField(), required=False, default=list)
