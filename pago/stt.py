"""음성 인식 (로컬 faster-whisper). 첫 실행 때 모델을 내려받는다."""

import re

import numpy as np
from faster_whisper import WhisperModel

# 게임 용어를 미리 알려주면 인식률이 올라간다
GAME_TERMS = "롤, 점멸, 드래곤, 용, 바론, 전령, 공허 유충, 정글, 탑, 미드, 원딜, 서폿, 텔레포트, 궁극기, 쿨타임"


def _prepare(audio: np.ndarray, rate: int = 16000) -> np.ndarray:
    """책상 진동 같은 저음(100Hz 이하)을 걸러내고 음량을 일정하게 맞춘다.

    기기 마이크 소리는 저음 잡음이 대부분이고 말소리가 작아서, 그대로 넣으면 인식률이 떨어진다.
    """
    a = float(np.exp(-2 * np.pi * 100 / rate))
    y = audio.astype(np.float64)
    for _ in range(2):  # 1차 고역 통과 필터 두 번 = 기울기 12dB/oct
        out = np.empty_like(y)
        prev_x = prev_y = 0.0
        for i, x in enumerate(y):
            prev_y = a * (prev_y + x - prev_x)
            prev_x = x
            out[i] = prev_y
        y = out
    rms = float(np.sqrt(np.mean(y**2)))
    gain = min(0.1 / max(rms, 1e-6), 30.0)  # 목표 -20dBFS, 무음을 과하게 키우지 않도록 최대 30배
    return np.clip(y * gain, -1, 1).astype(np.float32)


_TERM_SET = {term.strip() for term in GAME_TERMS.split(",")}


class SpeechToText:
    def __init__(self, model_size: str):
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            _prepare(audio),
            language="ko",
            beam_size=1,
            vad_filter=True,  # 말소리 없는 구간에서 헛소리 받아쓰는 것 방지
            initial_prompt=GAME_TERMS,
        )
        # 잡음만 있을 때 whisper 는 힌트 단어(GAME_TERMS)를 그대로 늘어놓는 헛소리를 만든다. 그런 구간은 버린다
        kept = [seg.text for seg in segments if seg.no_speech_prob < 0.6 and seg.avg_logprob > -1.0]
        text = "".join(kept).strip()
        words = [w.strip() for w in re.split(r"[,\s]+", text) if w.strip()]
        if len(words) >= 3 and sum(w in _TERM_SET for w in words) / len(words) >= 0.5:
            return ""
        return text
