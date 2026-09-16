import os
import json
import redis
import yt_dlp
from celery import Celery
from backend.config import settings
from backend.services.transcoder import MediaTranscoder
from backend.services.tagger import MediaTagger

celery_app = Celery("ume_worker", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
redis_client = redis.Redis.from_url(settings.REDIS_URL)

@celery_app.task(bind=True)
def process_media_task(self, payload: dict):
    job_id = self.request.id
    target_format = payload.get("target_format", "mp3")
    media_url = payload.get("url")
    title = payload.get("title", "download")
    artist = payload.get("artist", "Unknown")
    thumbnail = payload.get("thumbnail")
    resolution = payload.get("resolution")
    trim = payload.get("trim", {})
    start_time = trim.get("start") if trim.get("enabled") else None
    end_time = trim.get("end") if trim.get("enabled") else None

    output_filename = f"{job_id}.{target_format}"
    output_path = os.path.join(settings.STORAGE_DIR, output_filename)

    redis_client.publish(f"job:{job_id}", json.dumps({"stage": "RESOLVING_STREAMS", "percent": 5}))

    # Extract target stream URLs
    ydl_opts = {'quiet': True}
    if settings.PROXY_URL:
        ydl_opts['proxy'] = settings.PROXY_URL

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(media_url, download=False)
        total_duration = info.get("duration", 0)

        audio_url = None
        video_url = None

        if target_format in ["mp3", "m4a", "wav"]:
            # Pick best audio-only stream
            for f in reversed(info.get("formats", [])):
                if f.get("vcodec") == "none" and f.get("acodec") != "none":
                    audio_url = f.get("url")
                    break
        else:
            # Video muxing: Grab appropriate video DASH stream and audio DASH stream
            for f in reversed(info.get("formats", [])):
                if not video_url and f.get("vcodec") != "none":
                    if resolution and (str(f.get("height")) in resolution or f.get("resolution") == resolution):
                        video_url = f.get("url")
                if not audio_url and f.get("vcodec") == "none" and f.get("acodec") != "none":
                    audio_url = f.get("url")

    if not audio_url:
        raise RuntimeError("Failed to resolve audio stream extraction parameters.")

    transcoder = MediaTranscoder(redis_client)
    transcoder.execute_pipeline(
        job_id=job_id,
        video_stream_url=video_url,
        audio_stream_url=audio_url,
        output_path=output_path,
        format_type=target_format,
        bitrate=payload.get("bitrate", "320k"),
        start_time=start_time,
        end_time=end_time,
        total_duration=float(total_duration)
    )

    if target_format == "mp3":
        redis_client.publish(f"job:{job_id}", json.dumps({"stage": "INJECTING_METADATA", "percent": 96}))
        MediaTagger.inject_mp3_tags(output_path, title=title, artist=artist, album=title, artwork_url=thumbnail)

    download_link = f"/api/v1/download/{output_filename}?name={title}.{target_format}"
    redis_client.publish(f"job:{job_id}", json.dumps({
        "stage": "COMPLETE",
        "percent": 100,
        "download_url": download_link
    }))

    return {"status": "SUCCESS", "path": output_path}