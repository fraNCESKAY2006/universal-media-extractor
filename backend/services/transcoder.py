import re
import subprocess
import os
import json
from typing import Callable, Optional
import redis

class MediaTranscoder:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _publish_progress(self, job_id: str, stage: str, percent: int):
        self.redis.publish(
            f"job:{job_id}",
            json.dumps({"stage": stage, "percent": percent})
        )

    def execute_pipeline(
        self,
        job_id: str,
        video_stream_url: Optional[str],
        audio_stream_url: str,
        output_path: str,
        format_type: str,
        bitrate: str = "192k",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        total_duration: float = 0.0
    ):
        self._publish_progress(job_id, "PREPARING", 5)

        cmd = ["ffmpeg", "-y", "-nostats", "-progress", "pipe:1"]

        # Timestamp seeks applied before input
        if start_time:
            cmd.extend(["-ss", start_time])
        if end_time:
            cmd.extend(["-to", end_time])

        # Inputs setup
        if video_stream_url:
            cmd.extend(["-i", video_stream_url])
        cmd.extend(["-i", audio_stream_url])

        # Output formatting configuration
        if format_type == "mp3":
            cmd.extend(["-vn", "-codec:a", "libmp3lame", "-b:a", bitrate, "-ar", "44100"])
        elif format_type == "m4a":
            cmd.extend(["-vn", "-codec:a", "aac", "-b:a", bitrate])
        elif format_type == "wav":
            cmd.extend(["-vn", "-acodec", "pcm_s16le", "-ar", "44100"])
        elif format_type == "mp4":
            cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"])
        else:
            raise ValueError(f"Unsupported format: {format_type}")

        cmd.append(output_path)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        time_regex = re.compile(r"out_time_ms=(\d+)")
        effective_duration = total_duration

        self._publish_progress(job_id, "PROCESSING", 15)

        for line in process.stdout:
            match = time_regex.search(line)
            if match and effective_duration > 0:
                elapsed_us = int(match.group(1))
                elapsed_s = elapsed_us / 1000000.0
                percent = min(90, 15 + int((elapsed_s / effective_duration) * 75))
                self._publish_progress(job_id, "PROCESSING", percent)

        process.wait()
        if process.returncode != 0:
            err = process.stderr.read()
            raise RuntimeError(f"FFmpeg pipeline failure: {err}")

        self._publish_progress(job_id, "PROCESSING", 95)