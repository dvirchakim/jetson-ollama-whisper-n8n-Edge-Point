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

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import gradio as gr
from gradio.routes import mount_gradio_app

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


def run_transcription_from_path(
    file_path: str,
    language: Optional[str] = None,
    task: str = "transcribe",
    word_timestamps: bool = False
):
    """Run transcription using an existing file path."""
    whisper_model = get_model()
    segments, info = whisper_model.transcribe(
        file_path,
        language=language,
        task=task,
        word_timestamps=word_timestamps,
        beam_size=5
    )
    
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
    return text, info, segment_list


async def _process_transcription(
    request: Request,
    audio_file: Optional[UploadFile] = None,
    language: Optional[str] = None,
    task: str = "transcribe",
    output: str = "json",
    word_timestamps: bool = False
):
    """Internal transcription processing function."""
    try:
        # Detect content type and handle accordingly
        content_type = request.headers.get("content-type", "")
        
        if "multipart/form-data" in content_type:
            # Standard multipart upload
            if audio_file is None:
                raise HTTPException(status_code=400, detail="audio_file is required for multipart uploads")
            suffix = Path(audio_file.filename).suffix if audio_file.filename else ".wav"
            content = await audio_file.read()
        else:
            # Raw binary upload (n8n compatibility)
            content = await request.body()
            if not content:
                raise HTTPException(status_code=400, detail="Empty audio data")
            # Parse query params for options
            query_params = dict(request.query_params)
            language = query_params.get("language", language)
            task = query_params.get("task", task)
            output = query_params.get("output", output)
            word_timestamps = query_params.get("word_timestamps", "false").lower() == "true"
            suffix = ".wav"
        
        # Save to temp location
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            text, info, segment_list = run_transcription_from_path(
                tmp_path,
                language=language,
                task=task,
                word_timestamps=word_timestamps
            )
            
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


def build_gradio_app():
    """Create Gradio UI for Whisper."""
    language_choices = [
        "",
        "en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "hi", "ar"
    ]

    def gradio_transcribe(audio, language, task, word_timestamps):
        if not audio:
            return "Please provide audio.", {}
        lang = language or None
        text, info, segments = run_transcription_from_path(
            audio,
            language=lang,
            task=task,
            word_timestamps=word_timestamps
        )
        metadata = {
            "language": info.language,
            "duration": info.duration,
            "segments": segments
        }
        return text, metadata

    with gr.Blocks(title="Jetson Whisper UI") as demo:
        gr.Markdown(
            "# Jetson Whisper UI\n"
            "Upload or record audio to transcribe using the onboard faster-whisper model. "
            "This UI talks to the same GPU-accelerated backend used by the API."
        )
        with gr.Row():
            audio_input = gr.Audio(
                sources=["upload", "microphone"],
                type="filepath",
                label="Audio Input"
            )
            with gr.Column():
                language_input = gr.Dropdown(
                    language_choices,
                    value="",
                    label="Language (optional)",
                    info="Leave blank for auto-detect"
                )
                task_input = gr.Radio(
                    ["transcribe", "translate"],
                    value="transcribe",
                    label="Task"
                )
                ts_input = gr.Checkbox(
                    label="Word timestamps",
                    value=False
                )
        run_button = gr.Button("Transcribe")
        transcript_output = gr.Textbox(
            label="Transcript",
            lines=6
        )
        metadata_output = gr.JSON(
            label="Metadata"
        )

        run_button.click(
            gradio_transcribe,
            inputs=[audio_input, language_input, task_input, ts_input],
            outputs=[transcript_output, metadata_output]
        )

    return demo


@app.post("/asr", response_model=TranscriptionResponse)
async def transcribe(
    request: Request,
    audio_file: Optional[UploadFile] = File(None),
    language: Optional[str] = Form(None),
    task: Optional[str] = Form("transcribe"),
    output: Optional[str] = Form("json"),
    word_timestamps: Optional[bool] = Form(False)
):
    """
    Transcribe audio file to text.
    Accepts both multipart/form-data and raw binary (application/octet-stream).
    
    - **audio_file**: Audio file (wav, mp3, m4a, etc.) for multipart uploads
    - **language**: Language code (e.g., 'en', 'es'). Auto-detect if not specified.
    - **task**: 'transcribe' or 'translate' (translate to English)
    - **output**: Output format ('json', 'text')
    - **word_timestamps**: Include word-level timestamps
    
    For n8n binary uploads, send raw audio as application/octet-stream.
    Query params: ?language=en&task=transcribe&output=json
    """
    return await _process_transcription(
        request=request,
        audio_file=audio_file,
        language=language,
        task=task,
        output=output,
        word_timestamps=word_timestamps
    )


@app.post("/v1/audio/transcriptions")
async def openai_compatible_transcribe(
    request: Request,
    file: Optional[UploadFile] = File(None),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    response_format: Optional[str] = Form(None)
):
    """
    OpenAI-compatible transcription endpoint.
    Accepts both multipart/form-data and raw binary.
    For binary uploads, use query params: ?language=en&response_format=json
    """
    return await _process_transcription(
        request=request,
        audio_file=file,
        language=language,
        task="transcribe",
        output=response_format or "json",
        word_timestamps=False
    )

# Mount Gradio UI at /ui
gradio_app = build_gradio_app()
app = mount_gradio_app(app, gradio_app, path="/ui")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9000))
    uvicorn.run(app, host="0.0.0.0", port=port)
