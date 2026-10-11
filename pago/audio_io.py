"""마이크 입력(말소리 구간 검출)과 스피커 출력.

PC 에 연결된 일반 마이크/스피커용. 파고쌤 기기(ESP32)는 serial_device.py 를 쓴다.
"""

import asyncio
import collections
import contextlib
import time
from collections.abc import AsyncIterator, Callable

import numpy as np
import sounddevice as sd

STT_RATE = 16000  # whisper 입력 샘플레이트
FRAME_MS = 30
PRE_ROLL_MS = 300  # 말 시작 직전 소리도 같이 잘라서 첫 음절이 안 잘리게
END_SILENCE_MS = 800  # 이만큼 조용하면 말이 끝난 것으로 판단
MIN_SPEECH_MS = 400
MAX_SPEECH_MS = 15000
MIN_RMS = 0.004  # 아주 조용한 방에서 잡음이 말로 인식되는 것 방지
CALIBRATE_MS = 500  # 시작 직후 이만큼은 주변 소음 측정만 한다


def find_device(name: str | None, kind: str) -> int | None:
    """장치 이름 일부로 장치 번호를 찾는다. name 이 없으면 시스템 기본 장치."""
    if name is None:
        return None
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    for index, device in enumerate(sd.query_devices()):
        if name.lower() in device["name"].lower() and device[channel_key] > 0:
            return index
    raise RuntimeError(f"{kind} 장치 '{name}' 를 찾을 수 없음. `python -m pago devices` 로 목록 확인")


def list_devices() -> None:
    default_in, default_out = sd.default.device
    for index, device in enumerate(sd.query_devices()):
        roles = []
        if device["max_input_channels"] > 0:
            roles.append("입력" + ("(기본)" if index == default_in else ""))
        if device["max_output_channels"] > 0:
            roles.append("출력" + ("(기본)" if index == default_out else ""))
        rate = int(device["default_samplerate"])
        print(f"[{index:2}] {device['name']}  {'/'.join(roles)}  {rate}Hz")


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return samples
    duration = len(samples) / src_rate
    src_t = np.linspace(0, duration, len(samples), endpoint=False)
    dst_t = np.linspace(0, duration, int(duration * dst_rate), endpoint=False)
    return np.interp(dst_t, src_t, samples).astype(np.float32)


class VoiceInput:
    """마이크 프레임을 받아 말소리 구간(한 마디)으로 잘라낸다. 프레임 공급은 하위 클래스가 맡는다."""

    def __init__(self, rate: int, vad_ratio: float):
        self.rate = rate
        self.vad_ratio = vad_ratio
        # 스피커가 말하는 동안에는 마이크를 막는다 (에코 제거 붙이기 전 임시 방편)
        self.muted = False
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue()

    def _open(self, push: Callable[[np.ndarray], None]) -> contextlib.AbstractContextManager:
        """push(frame) 를 다른 스레드에서 계속 호출하는 입력을 연다."""
        raise NotImplementedError

    async def utterances(self) -> AsyncIterator[np.ndarray]:
        """사람이 한 마디 할 때마다 16kHz float32 오디오를 하나씩 내보낸다."""
        loop = asyncio.get_running_loop()

        def push(frame: np.ndarray) -> None:
            if not self.muted:
                loop.call_soon_threadsafe(self._queue.put_nowait, frame)

        pre_roll: collections.deque[np.ndarray] = collections.deque()
        pre_roll_ms = 0.0
        noise_floor = MIN_RMS
        calibration: list[float] = []
        speech: list[np.ndarray] = []
        speech_ms = silent_ms = 0.0

        with self._open(push):
            while True:
                frame = await self._queue.get()
                if self.muted:
                    speech.clear()
                    continue

                frame_ms = len(frame) * 1000 / self.rate
                # 책상 진동·스피커 잔향 같은 저음(100Hz 이하)에 반응하지 않도록 고음을 강조해서 잰다
                emphasized = frame[1:] - 0.95 * frame[:-1]
                rms = float(np.sqrt(np.mean(emphasized**2)))

                # 소음 기준이 없으면 첫 프레임부터 전부 말소리로 판정되므로 먼저 주변 소음을 잰다
                if len(calibration) * frame_ms < CALIBRATE_MS:
                    calibration.append(rms)
                    noise_floor = max(float(np.median(calibration)), MIN_RMS)
                    continue
                is_loud = rms > max(noise_floor * self.vad_ratio, MIN_RMS)

                if not speech:
                    if is_loud:
                        speech = [*pre_roll, frame]
                        speech_ms = pre_roll_ms + frame_ms
                        silent_ms = 0.0
                    else:
                        # 말하지 않을 때만 주변 소음 수준을 천천히 따라간다
                        noise_floor = 0.95 * noise_floor + 0.05 * rms
                        pre_roll.append(frame)
                        pre_roll_ms += frame_ms
                        while pre_roll_ms > PRE_ROLL_MS:
                            pre_roll_ms -= len(pre_roll.popleft()) * 1000 / self.rate
                    continue

                speech.append(frame)
                speech_ms += frame_ms
                silent_ms = 0.0 if is_loud else silent_ms + frame_ms

                if silent_ms >= END_SILENCE_MS or speech_ms >= MAX_SPEECH_MS:
                    if speech_ms >= MAX_SPEECH_MS:
                        # 15초 내내 시끄러웠다면 소음 기준이 너무 낮은 것. 이번 구간 수준으로 올린다
                        noise_floor = max(noise_floor, float(np.median([np.sqrt(np.mean((f[1:] - 0.95 * f[:-1]) ** 2)) for f in speech])))
                    if speech_ms - silent_ms >= MIN_SPEECH_MS:
                        audio = np.concatenate(speech)
                        yield resample(audio, self.rate, STT_RATE)
                    speech = []
                    pre_roll.clear()
                    pre_roll_ms = 0.0


class Microphone(VoiceInput):
    """PC 에 연결된 일반 마이크 (웹캠 등)."""

    def __init__(self, device: str | None, vad_ratio: float):
        self.device = find_device(device, "input")
        super().__init__(int(sd.query_devices(self.device, "input")["default_samplerate"]), vad_ratio)

    def _open(self, push):
        return sd.InputStream(
            device=self.device,
            samplerate=self.rate,
            channels=1,
            dtype="float32",
            blocksize=self.rate * FRAME_MS // 1000,
            callback=lambda indata, *_: push(indata[:, 0].copy()),
        )


class Speaker:
    def __init__(self, device: str | None, mic: VoiceInput):
        self.device = find_device(device, "output")
        self.mic = mic

    def play(self, samples: np.ndarray, rate: int) -> None:
        """블로킹 재생. 재생 중과 직후 잠깐은 마이크를 막는다."""
        self.mic.muted = True
        try:
            sd.play(samples, rate, device=self.device)
            sd.wait()
            time.sleep(0.3)  # 스피커 잔향이 마이크로 들어가는 것 방지
        finally:
            self.mic.muted = False
