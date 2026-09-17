import os
import sys
import subprocess
import shutil

# --- Auto-install required lightweight packages ---
REQUIRED_LIBS = ["fastapi", "uvicorn", "yt-dlp", "mutagen", "requests", "sse-starlette", "curl_cffi"]
missing = []
for lib in REQUIRED_LIBS:
    try:
        __import__(lib.replace("-", "_"))
    except ImportError:
        missing.append(lib)

if missing:
    print(f"[*] Installing missing dependencies: {', '.join(missing)}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    print("[✓] Dependencies installed.\n")

import re
import json
import asyncio
import collections
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import yt_dlp
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError
import requests
import uvicorn

# --- Setup Directories ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "downloads")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
os.makedirs(STORAGE_DIR, exist_ok=True)

# --- Cookie file path ---
COOKIE_FILE = os.path.join(BASE_DIR, "cookies.txt")
def get_ydl_opts(extra: dict = None):
    opts = {
        'quiet': True,
        'skip_download': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}}
    }
    if os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE
    if extra:
        opts.update(extra)
    return opts

# --- Ensure FFmpeg is accessible ---
FFMPEG_CMD = shutil.which("ffmpeg") or "ffmpeg"

def check_ffmpeg():
    try:
        subprocess.run([FFMPEG_CMD, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False

# --- In-Memory Event Hub ---
class EventBus:
    def __init__(self):
        self.channels: Dict[str, List[asyncio.Queue]] = collections.defaultdict(list)

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        self.channels[job_id].append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue):
        if job_id in self.channels and q in self.channels[job_id]:
            self.channels[job_id].remove(q)
            if not self.channels[job_id]:
                del self.channels[job_id]

    async def publish(self, job_id: str, data: dict):
        if job_id in self.channels:
            for q in self.channels[job_id]:
                await q.put(data)

event_bus = EventBus()

# --- Metadata Tagging ---
def inject_mp3_tags(file_path: str, title: str, artist: str, artwork_url: Optional[str]):
    try:
        try:
            audio = ID3(file_path)
        except ID3NoHeaderError:
            audio = ID3()

        audio.add(TIT2(encoding=3, text=title))
        audio.add(TPE1(encoding=3, text=artist))
        audio.add(TALB(encoding=3, text=title))

        if artwork_url:
            try:
                img_data = requests.get(artwork_url, timeout=5).content
                audio.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data))
            except Exception:
                pass

        audio.save(file_path, v2_version=3)
    except Exception as e:
        print(f"[Tagger Error] {e}")

# --- Background Task Worker ---
# --- Background Task Worker (Bulletproof Bypass) ---
def execute_ffmpeg_job(job_id: str, payload: dict, loop: asyncio.AbstractEventLoop):
    def notify(stage: str, percent: int, download_url: Optional[str] = None):
        msg = {"stage": stage, "percent": percent}
        if download_url:
            msg["download_url"] = download_url
        asyncio.run_coroutine_threadsafe(event_bus.publish(job_id, msg), loop)

    notify("INITIALIZING", 5)
    url = payload["url"]
    target_format = payload["target_format"]
    trim = payload.get("trim", {})
    start_time = trim.get("start") if trim.get("enabled") else None
    end_time = trim.get("end") if trim.get("enabled") else None

    notify("DOWNLOADING_MEDIA", 20)
    
    output_file = f"{job_id}.{target_format}"
    output_path = os.path.join(STORAGE_DIR, output_file)

    # Let yt-dlp handle everything automatically without picking fragile formats
    ydl_opts = get_ydl_opts({
        'outtmpl': output_path.replace(f'.{target_format}', ''),
    })

    if target_format in ['mp3', 'm4a', 'wav']:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': target_format if target_format != 'wav' else 'wav',
            'preferredquality': '320' if payload.get("bitrate") == '320k' else '192',
        }]
    else:
        ydl_opts['format'] = 'best'
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        # Fallback raw download
        ydl_opts['format'] = 'bv*+ba/b'
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    # Handle trimming if specified
    if start_time or end_time:
        notify("TRIMMING", 85)
        trimmed_path = output_path.replace(f'.{target_format}', f'_trimmed.{target_format}')
        trim_cmd = [FFMPEG_CMD, "-y"]
        if start_time:
            trim_cmd.extend(["-ss", start_time])
        if end_time:
            trim_cmd.extend(["-to", end_time])
        trim_cmd.extend(["-i", output_path, "-c", "copy", trimmed_path])
        subprocess.run(trim_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(trimmed_path):
            os.replace(trimmed_path, output_path)

    if target_format == "mp3":
        notify("TAGGING_METADATA", 95)
        try:
            with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
                info = ydl.extract_info(url, download=False)
            inject_mp3_tags(output_path, info.get("title", "Media"), info.get("uploader", "Artist"), info.get("thumbnail"))
        except:
            pass

    safe_name = "media"
    dl_url = f"/api/v1/download/{output_file}?name={safe_name}.{target_format}"
    notify("COMPLETE", 100, download_url=dl_url)
    def notify(stage: str, percent: int, download_url: Optional[str] = None):
        msg = {"stage": stage, "percent": percent}
        if download_url:
            msg["download_url"] = download_url
        asyncio.run_coroutine_threadsafe(event_bus.publish(job_id, msg), loop)

    notify("INITIALIZING", 5)
    url = payload["url"]
    target_format = payload["target_format"]
    trim = payload.get("trim", {})
    start_time = trim.get("start") if trim.get("enabled") else None
    end_time = trim.get("end") if trim.get("enabled") else None

    notify("RESOLVING_STREAMS", 10)
    
    output_file = f"{job_id}.{target_format}"
    output_path = os.path.join(STORAGE_DIR, output_file)

    # Use robust fallback format selection
    ydl_download_opts = get_ydl_opts({
        'format': 'best',
        'outtmpl': output_path.replace(f'.{target_format}', ''),
    })

    if target_format in ['mp3', 'm4a', 'wav']:
        ydl_download_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': target_format if target_format != 'wav' else 'wav',
            'preferredquality': '320' if payload.get("bitrate") == '320k' else '192',
        }]
    elif target_format == 'mp4':
        ydl_download_opts['merge_output_format'] = 'mp4'

    notify("DOWNLOADING_AND_PROCESSING", 30)
    with yt_dlp.YoutubeDL(ydl_download_opts) as ydl:
        ydl.download([url])

    # Handle trimming post-download if specified
    if start_time or end_time:
        trimmed_path = output_path.replace(f'.{target_format}', f'_trimmed.{target_format}')
        trim_cmd = [FFMPEG_CMD, "-y"]
        if start_time:
            trim_cmd.extend(["-ss", start_time])
        if end_time:
            trim_cmd.extend(["-to", end_time])
        trim_cmd.extend(["-i", output_path, "-c", "copy", trimmed_path])
        subprocess.run(trim_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(trimmed_path):
            os.replace(trimmed_path, output_path)

    if target_format == "mp3":
        notify("TAGGING_METADATA", 90)
        inject_mp3_tags(output_path, payload.get("title", "Media"), payload.get("artist", "Artist"), payload.get("thumbnail"))

    safe_name = re.sub(r'[\\/*?:"<>|]', "", payload.get("title", "download"))
    dl_url = f"/api/v1/download/{output_file}?name={safe_name}.{target_format}"
    notify("COMPLETE", 100, download_url=dl_url)

# --- FastAPI App ---
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ExtractReq(BaseModel):
    url: str

class TrimOptions(BaseModel):
    enabled: bool = False
    start: Optional[str] = None
    end: Optional[str] = None

class JobReq(BaseModel):
    url: str
    target_format: str
    bitrate: str = "320k"
    resolution: Optional[str] = None
    title: str = "Media"
    artist: str = "Artist"
    thumbnail: Optional[str] = None
    trim: TrimOptions = TrimOptions()

@app.post("/api/v1/extract")
async def extract(req: ExtractReq):
    try:
        with yt_dlp.YoutubeDL(get_ydl_opts({'format': 'best'})) as ydl:
            data = ydl.extract_info(req.url, download=False)
        
        direct_video = []
        formats = data.get("formats", [])
        
        for f in formats:
            if f.get("vcodec") != "none" and f.get("acodec") != "none" and f.get("ext") == "mp4":
                res = f.get("resolution") or f"{f.get('height')}p"
                direct_video.append({
                    "resolution": res,
                    "direct_url": f.get("url")
                })

        seen_res = set()
        unique_direct_video = []
        for v in direct_video:
            if v["resolution"] not in seen_res:
                seen_res.add(v["resolution"])
                unique_direct_video.append(v)

        dash_res = ["1080p", "1440p", "2160p"]
        muxed_video = [{"resolution": r} for r in dash_res if any(r.split()[0] in str(f.get("height", "")) for f in formats)]
        
        preview_audio = next((f["url"] for f in reversed(formats) if f.get("vcodec") == "none" and f.get("acodec") != "none"), None)

        return {
            "title": data.get("title", "Unknown Title"),
            "uploader": data.get("uploader", "Unknown Uploader"),
            "duration": data.get("duration", 0),
            "thumbnail": data.get("thumbnail", ""),
            "preview_audio_url": preview_audio,
            "direct_video": unique_direct_video,
            "muxed_video": muxed_video
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/v1/jobs")
async def create_job(payload: JobReq):
    import uuid
    job_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, execute_ffmpeg_job, job_id, payload.model_dump(), loop)
    return {"job_id": job_id}

@app.get("/api/v1/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    async def sse():
        q = event_bus.subscribe(job_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                data = await q.get()
                yield {"event": "progress", "data": json.dumps(data)}
                if data.get("stage") == "COMPLETE":
                    break
        finally:
            event_bus.unsubscribe(job_id, q)
    return EventSourceResponse(sse())

@app.get("/api/v1/download/{filename}")
async def download(filename: str, name: str = Query("media")):
    path = os.path.join(STORAGE_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File expired.")
    return FileResponse(path, filename=name, media_type="application/octet-stream")

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    if not check_ffmpeg():
        print("\n⚠️  WARNING: FFmpeg not found on system PATH!\n")
    
    port = int(os.environ.get("PORT", 8000))
    print(f"\n🚀 Universal Media Extractor is live on http://0.0.0.0:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)