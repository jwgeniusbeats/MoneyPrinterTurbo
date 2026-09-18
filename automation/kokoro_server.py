"""Local Kokoro TTS server, OpenAI-compatible, for app/services/voice.py's
kokoro_tts() (which talks to a self-hosted "/v1/audio/speech" endpoint).

Runs hexgrad/Kokoro-82M fully offline via kokoro-onnx (CPU, no GPU needed).
Model files live in resources/kokoro/ (not committed -- see README note).
Kept alive by the com.nognize.kokoroserver launchd job on port 8880.
"""

import io
import os

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from kokoro_onnx import Kokoro
from pydantic import BaseModel
from pydub import AudioSegment

# launchd runs this with a minimal PATH that doesn't include Homebrew, so
# pydub can't find ffmpeg via PATH lookup -- point it at the binary directly.
AudioSegment.converter = "/opt/homebrew/bin/ffmpeg"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, "resources", "kokoro")

os.environ.setdefault(
    "PHONEMIZER_ESPEAK_LIBRARY", "/opt/homebrew/opt/espeak-ng/lib/libespeak-ng.dylib"
)
os.environ.setdefault(
    "ESPEAK_DATA_PATH", "/opt/homebrew/opt/espeak-ng/share/espeak-ng-data"
)

kokoro = Kokoro(
    os.path.join(MODEL_DIR, "kokoro-v1.0.onnx"),
    os.path.join(MODEL_DIR, "voices-v1.0.bin"),
)

VOICES = [
    "af_heart", "am_adam", "bm_george", "af_bella", "am_michael",
    "bf_emma", "bm_lewis", "af_sarah", "am_eric",
]

app = FastAPI()


class SpeechRequest(BaseModel):
    model: str = "kokoro"
    voice: str = "af_heart"
    input: str
    speed: float = 1.0
    response_format: str = "wav"


@app.get("/v1/audio/voices")
def list_voices():
    return {"voices": VOICES}


@app.post("/v1/audio/speech")
def create_speech(req: SpeechRequest):
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="input text is empty")
    voice = req.voice if req.voice in VOICES else "af_heart"
    samples, sample_rate = kokoro.create(
        text, voice=voice, speed=req.speed, lang="en-us"
    )
    wav_buf = io.BytesIO()
    sf.write(wav_buf, samples, sample_rate, format="WAV")
    wav_buf.seek(0)

    # voice.py's _openai_compatible_tts always requests "mp3" and writes the
    # raw response bytes to a .mp3 file -- return real mp3, not just a
    # renamed wav, so ffmpeg/moviepy decode it without surprises.
    mp3_buf = io.BytesIO()
    AudioSegment.from_wav(wav_buf).export(mp3_buf, format="mp3")
    return Response(content=mp3_buf.getvalue(), media_type="audio/mpeg")
