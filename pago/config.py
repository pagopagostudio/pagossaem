import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    serial_port: str | None
    volume: int
    input_device: str | None
    output_device: str | None
    port: int
    whisper_model: str
    tts_voice: str
    vad_ratio: float
    claude_model: str


def _optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def load() -> Config:
    return Config(
        serial_port=_optional("PAGO_SERIAL_PORT"),
        volume=int(os.getenv("PAGO_VOLUME", "30")),
        input_device=_optional("PAGO_INPUT_DEVICE"),
        output_device=_optional("PAGO_OUTPUT_DEVICE"),
        port=int(os.getenv("PAGO_PORT", "8765")),
        whisper_model=os.getenv("PAGO_WHISPER_MODEL", "medium"),
        tts_voice=os.getenv("PAGO_TTS_VOICE", "ko-KR-SunHiNeural"),
        vad_ratio=float(os.getenv("PAGO_VAD_RATIO", "3.0")),
        claude_model=os.getenv("PAGO_CLAUDE_MODEL", "claude-opus-5"),
    )
