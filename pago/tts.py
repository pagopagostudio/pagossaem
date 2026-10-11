"""음성 합성 (Microsoft Edge 온라인 TTS, 무료, 키 필요 없음)."""

import io

import edge_tts
import numpy as np
import soundfile as sf


class TextToSpeech:
    def __init__(self, voice: str):
        self.voice = voice

    async def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        mp3 = bytearray()
        async for chunk in edge_tts.Communicate(text, self.voice, rate="+10%").stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
        samples, rate = sf.read(io.BytesIO(bytes(mp3)), dtype="float32")
        return samples, rate
