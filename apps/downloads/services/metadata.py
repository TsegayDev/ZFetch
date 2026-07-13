import os
import logging
from PIL import Image
from typing import Dict, Any, Optional

# Mutagen imports
import mutagen
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TRCK, COMM, TDRC, error as ID3Error
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

logger = logging.getLogger('downloads')


class MetadataEmbedder:
    """
    Service to embed rich metadata and album art/thumbnails directly into media files.
    """

    @staticmethod
    def convert_to_jpeg(image_path: str) -> str:
        """
        Converts any image file format to a compatible standard JPEG image.
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found at: {image_path}")

        base, ext = os.path.splitext(image_path)
        output_path = f"{base}_embedded.jpg"

        try:
            with Image.open(image_path) as img:
                rgb_img = img.convert('RGB')
                rgb_img.save(output_path, 'JPEG', quality=90)
            return output_path
        except Exception as e:
            logger.error(f"Failed to convert image to JPEG: {str(e)}")
            raise e

    @classmethod
    def embed(cls, file_path: str, metadata: Dict[str, Any], art_path: Optional[str] = None) -> bool:
        """
        Orchestrates metadata and cover art embedding based on file extension.
        """
        if not os.path.exists(file_path):
            logger.error(f"Target media file not found for embedding: {file_path}")
            return False

        ext = os.path.splitext(file_path)[1].lower()
        temp_jpeg = None

        try:
            # Process cover art if provided
            jpeg_data = None
            if art_path and os.path.exists(art_path):
                temp_jpeg = cls.convert_to_jpeg(art_path)
                with open(temp_jpeg, 'rb') as f:
                    jpeg_data = f.read()

            if ext == '.mp3':
                cls._embed_mp3(file_path, metadata, jpeg_data)
            elif ext in ['.m4a', '.mp4', '.m4b']:
                cls._embed_mp4(file_path, metadata, jpeg_data)
            elif ext == '.flac':
                cls._embed_flac(file_path, metadata, jpeg_data)
            elif ext in ['.opus', '.ogg']:
                cls._embed_vorbis(file_path, metadata, jpeg_data, ext)
            else:
                logger.warning(f"Metadata embedding not supported for extension: {ext}")
                return False

            logger.info(f"Successfully embedded metadata into {file_path}")
            return True

        except Exception as e:
            logger.error(f"Error embedding metadata into {file_path}: {str(e)}")
            return False
        finally:
            # Clean up the converted temporary image
            if temp_jpeg and os.path.exists(temp_jpeg):
                try:
                    os.remove(temp_jpeg)
                except OSError:
                    pass

    @staticmethod
    def _embed_mp3(file_path: str, metadata: Dict[str, Any], jpeg_data: Optional[bytes]) -> None:
        """Embeds ID3 tags and APIC frame into MP3 files."""
        # Load or initialize tags
        try:
            audio = MP3(file_path, ID3=ID3)
        except Exception:
            audio = MP3(file_path)

        if audio.tags is None:
            audio.add_tags()

        tags = audio.tags

        # Set text fields
        if metadata.get('title'):
            tags['TIT2'] = TIT2(encoding=3, text=metadata['title'])
        if metadata.get('artist') or metadata.get('uploader'):
            tags['TPE1'] = TPE1(encoding=3, text=metadata.get('artist') or metadata['uploader'])
        if metadata.get('album'):
            tags['TALB'] = TALB(encoding=3, text=metadata['album'])
        if metadata.get('track_number'):
            tags['TRCK'] = TRCK(encoding=3, text=str(metadata['track_number']))
        if metadata.get('description') or metadata.get('comment'):
            tags['COMM'] = COMM(encoding=3, lang='eng', desc='desc', text=metadata.get('description') or metadata.get('comment'))
        if metadata.get('upload_date'):
            tags['TDRC'] = TDRC(encoding=3, text=metadata['upload_date'])

        # Set cover art APIC frame
        if jpeg_data:
            tags.setall('APIC', [APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,  # 3 is cover art
                desc='Cover',
                data=jpeg_data
            )])

        audio.save()

    @staticmethod
    def _embed_mp4(file_path: str, metadata: Dict[str, Any], jpeg_data: Optional[bytes]) -> None:
        """Embeds MP4 tags and covr atom into M4A/MP4 files."""
        audio = MP4(file_path)

        # Map standard keys
        if metadata.get('title'):
            audio['\xa9nam'] = [metadata['title']]
        if metadata.get('artist') or metadata.get('uploader'):
            audio['\xa9ART'] = [metadata.get('artist') or metadata['uploader']]
        if metadata.get('album'):
            audio['\xa9alb'] = [metadata['album']]
        if metadata.get('track_number'):
            audio['trkn'] = [(int(metadata['track_number']), 0)]
        if metadata.get('description') or metadata.get('comment'):
            audio['\xa9cmt'] = [metadata.get('description') or metadata.get('comment')]
        if metadata.get('upload_date'):
            audio['\xa9day'] = [metadata['upload_date']]

        # Set cover art
        if jpeg_data:
            audio['covr'] = [MP4Cover(jpeg_data, imageformat=MP4Cover.FORMAT_JPEG)]

        audio.save()

    @staticmethod
    def _embed_flac(file_path: str, metadata: Dict[str, Any], jpeg_data: Optional[bytes]) -> None:
        """Embeds Vorbis comments and Picture block into FLAC files."""
        audio = FLAC(file_path)

        # Set tags
        if metadata.get('title'):
            audio['TITLE'] = metadata['title']
        if metadata.get('artist') or metadata.get('uploader'):
            audio['ARTIST'] = metadata.get('artist') or metadata['uploader']
        if metadata.get('album'):
            audio['ALBUM'] = metadata['album']
        if metadata.get('track_number'):
            audio['TRACKNUMBER'] = str(metadata['track_number'])
        if metadata.get('description') or metadata.get('comment'):
            audio['COMMENT'] = metadata.get('description') or metadata.get('comment')
        if metadata.get('upload_date'):
            audio['DATE'] = metadata['upload_date']

        # Add picture
        if jpeg_data:
            pic = Picture()
            pic.data = jpeg_data
            pic.type = 3  # cover front
            pic.mime = 'image/jpeg'
            pic.description = 'Cover'
            audio.clear_pictures()
            audio.add_picture(pic)

        audio.save()

    @staticmethod
    def _embed_vorbis(file_path: str, metadata: Dict[str, Any], jpeg_data: Optional[bytes], ext: str) -> None:
        """Embeds Vorbis comments into OGG and OPUS files."""
        if ext == '.opus':
            audio = OggOpus(file_path)
        else:
            audio = OggVorbis(file_path)

        # Set tags
        if metadata.get('title'):
            audio['title'] = metadata['title']
        if metadata.get('artist') or metadata.get('uploader'):
            audio['artist'] = metadata.get('artist') or metadata['uploader']
        if metadata.get('album'):
            audio['album'] = metadata['album']
        if metadata.get('track_number'):
            audio['tracknumber'] = str(metadata['track_number'])
        if metadata.get('description') or metadata.get('comment'):
            audio['comment'] = metadata.get('description') or metadata.get('comment')
        if metadata.get('upload_date'):
            audio['date'] = metadata['upload_date']

        # OGG and OPUS embed cover art using base64 encoded METADATA_BLOCK_PICTURE
        if jpeg_data:
            import base64
            pic = Picture()
            pic.data = jpeg_data
            pic.type = 3
            pic.mime = 'image/jpeg'
            pic.description = 'Cover'
            
            # Encode metadata block picture as base64 string
            serialized_pic = pic.write()
            encoded_pic = base64.b64encode(serialized_pic).decode('ascii')
            audio['metadata_block_picture'] = [encoded_pic]

        audio.save()