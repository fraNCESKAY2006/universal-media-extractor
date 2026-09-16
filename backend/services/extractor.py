import re
import yt_dlp
from typing import Dict, Any, Optional
from backend.config import settings

URL_REGEX = re.compile(
    r'^(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)(?P<id>[a-zA-Z0-9_-]{11}|[a-zA-Z0-9_-]+)'
)

class MediaExtractor:
    @staticmethod
    def validate_url(url: str) -> bool:
        return bool(URL_REGEX.match(url.strip()))

    @staticmethod
    def get_ydl_options(extra_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
        }
        if settings.PROXY_URL:
            opts['proxy'] = settings.PROXY_URL
        if extra_opts:
            opts.update(extra_opts)
        return opts

    @classmethod
    def extract_info(cls, url: str) -> Dict[str, Any]:
        ydl_opts = cls.get_ydl_options()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(url, download=False)
            
        is_playlist = 'entries' in data
        if is_playlist:
            return {
                "type": "PLAYLIST",
                "id": data.get("id"),
                "title": data.get("title"),
                "item_count": len(data.get("entries", [])),
                "items": [
                    {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "duration": entry.get("duration"),
                        "url": f"https://www.youtube.com/watch?v={entry.get('id')}"
                    } for entry in data.get("entries", []) if entry
                ]
            }

        formats = data.get("formats", [])
        
        # Identify progressive streams (<=720p with combined audio/video)
        direct_video = []
        for f in formats:
            if f.get("vcodec") != "none" and f.get("acodec") != "none" and f.get("ext") == "mp4":
                direct_video.append({
                    "format_id": f.get("format_id"),
                    "resolution": f.get("resolution") or f"{f.get('height')}p",
                    "ext": f.get("ext"),
                    "direct_url": f.get("url")
                })

        # Identify DASH Video Tracks (1080p, 4K)
        muxed_options = []
        dash_resolutions = ["1080p", "1440p", "2160p"]
        for res in dash_resolutions:
            has_res = any(f.get("format_note") == res or f.get("resolution") == res or str(f.get("height")) in res for f in formats if f.get("vcodec") != "none")
            if has_res:
                muxed_options.append({
                    "resolution": res,
                    "container": "mp4",
                    "requires_processing": True
                })

        # Extract best preview audio
        audio_preview = None
        for f in formats:
            if f.get("vcodec") == "none" and f.get("acodec") != "none":
                audio_preview = f.get("url")
                break

        return {
            "type": "VIDEO",
            "id": data.get("id"),
            "title": data.get("title"),
            "uploader": data.get("uploader"),
            "duration": data.get("duration"),
            "thumbnail": data.get("thumbnail"),
            "preview_audio_url": audio_preview,
            "preview_video_url": direct_video[0]["direct_url"] if direct_video else None,
            "direct_video": direct_video,
            "muxed_video": muxed_options,
            "audio_formats": ["mp3", "m4a", "wav"]
        }