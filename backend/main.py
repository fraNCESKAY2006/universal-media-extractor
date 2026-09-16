import os
import json
import asyncio
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import redis.asyncio as aioredis
from sse_starlette.sse import EventSourceResponse

from backend.config import settings
from backend.services.extractor import MediaExtractor
from backend.worker import process_media_task

app = FastAPI(title="Universal Media Extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_async = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

class ExtractionRequest(BaseModel):
    url: str

class TrimPayload(BaseModel):
    enabled: bool = False
    start: str | None = None
    end: str | None = None

class JobCreationRequest(BaseModel):
    url: str
    target_format: str
    bitrate: str = "320k"
    resolution: str | None = None
    title: str = "Media"
    artist: str = "Artist"
    thumbnail: str | None = None
    trim: TrimPayload = TrimPayload()

@app.post("/api/v1/extract")
async def extract_media(payload: ExtractionRequest):
    if not MediaExtractor.validate_url(payload.url):
        raise HTTPException(status_code=400, detail="Invalid YouTube or YouTube Music URL.")
    try:
        data = MediaExtractor.extract_info(payload.url)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/jobs")
async def create_job(payload: JobCreationRequest):
    task = process_media_task.delay(payload.model_dump())
    return {"job_id": task.id}

@app.get("/api/v1/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    async def event_generator():
        pubsub = redis_async.pubsub()
        await pubsub.subscribe(f"job:{job_id}")
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    data = message["data"]
                    yield {"event": "progress", "data": data}
                    parsed = json.loads(data)
                    if parsed.get("stage") == "COMPLETE":
                        break
                await asyncio.sleep(0.2)
        finally:
            await pubsub.unsubscribe(f"job:{job_id}")
            await pubsub.close()

    return EventSourceResponse(event_generator())

@app.get("/api/v1/download/{filename}")
async def download_file(filename: str, name: str = Query("media")):
    file_path = os.path.join(settings.STORAGE_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Requested media file has expired or does not exist.")
    return FileResponse(
        path=file_path,
        media_type="application/octet-stream",
        filename=name
    )

# Serve Frontend static build directly
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")