#!/usr/bin/env python3
"""
Whisper HTTP API Server for Jetson
Provides REST endpoints for speech-to-text using faster-whisper with GPU acceleration.
"""

import os
import tempfile
import logging
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Whisper ASR API",
    description="Speech-to-Text API using faster-whisper on Jetson",
    version="1.0.0"
)

# Global model instance
model = None
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")


class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None
    segments: Optional[list] = None


def get_model():
    """Lazy load the whisper model."""
    global model
    if model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Loading Whisper model: {MODEL_SIZE} on {DEVICE} with {COMPUTE_TYPE}")
        model = WhisperModel(
            MODEL_SIZE,
            device=DEVICE,
            compute_type=COMPUTE_TYPE
        )
        logger.info("Model loaded successfully")
    return model


@app.on_event("startup")
async def startup_event():
    """Pre-load model on startup."""
    try:
        get_model()
    except Exception as e:
        logger.error(f"Failed to load model on startup: {e}")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "model": MODEL_SIZE, "device": DEVICE}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/asr", response_model=TranscriptionResponse)
async def transcribe(
    audio_file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    task: Optional[str] = Form("transcribe"),
    output: Optional[str] = Form("json"),
    word_timestamps: Optional[bool] = Form(False)
):
    """
    Transcribe audio file to text.
    
    - **audio_file**: Audio file (wav, mp3, m4a, etc.)
    - **language**: Language code (e.g., 'en', 'es'). Auto-detect if not specified.
    - **task**: 'transcribe' or 'translate' (translate to English)
    - **output**: Output format ('json', 'text')
    - **word_timestamps**: Include word-level timestamps
    """
    try:
        whisper_model = get_model()
        
        # Save uploaded file to temp location
        suffix = Path(audio_file.filename).suffix if audio_file.filename else ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await audio_file.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            # Transcribe
            segments, info = whisper_model.transcribe(
                tmp_path,
                language=language,
                task=task,
                word_timestamps=word_timestamps,
                beam_size=5
            )
            
            # Collect segments
            segment_list = []
            full_text = []
            for segment in segments:
                segment_list.append({
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip()
                })
                full_text.append(segment.text.strip())
            
            text = " ".join(full_text)
            
            if output == "text":
                return JSONResponse(content={"text": text})
            
            return TranscriptionResponse(
                text=text,
                language=info.language,
                duration=info.duration,
                segments=segment_list
            )
            
        finally:
            # Cleanup temp file
            os.unlink(tmp_path)
            
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/audio/transcriptions")
async def openai_compatible_transcribe(
    file: UploadFile = File(...),
    model: Optional[str] = Form("whisper-1"),
    language: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json")
):
    """
    OpenAI-compatible transcription endpoint.
    """
    result = await transcribe(
        audio_file=file,
        language=language,
        output=response_format
    )
    return result


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9000))
    uvicorn.run(app, host="0.0.0.0", port=port)
