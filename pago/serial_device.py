"""파고쌤 기기(ESP32)와 USB 시리얼로 오디오를 주고받는다.

프레임 형식과 타입 번호는 firmware/pago_device/main/main.c 와 같아야 한다.
"""

import contextlib
import struct
import threading
import time
from collections.abc import Callable

import numpy as np
import serial

from .audio_io import VoiceInput, resample

BAUD = 921600
RATE = 16000
SYNC = b"\xa5\x5a"

FRAME_MIC = 0x01
FRAME_HELLO = 0x02
FRAME_SPEAKER = 0x11
FRAME_VOLUME = 0x12
FRAME_STOP = 0x13

SPEAKER_CHUNK = 512  # 샘플. 32ms 분량
SPEAKER_LEAD_S = 0.25  # 기기 버퍼(1초)에 이만큼 앞서서 채워 둔다


class SerialDevice:
    def __init__(self, port: str):
        self.port = serial.Serial()
        self.port.port = port
        self.port.baudrate = BAUD
        self.port.timeout = 0.1
        # 포트를 열 때 DTR/RTS 가 바뀌면 보드가 리셋되므로 미리 꺼둔다
        self.port.dtr = False
        self.port.rts = False
        self.port.open()
        self.hello: str | None = None
        self.volume = 0
        self._write_lock = threading.Lock()
        self._stop_playback = threading.Event()

    def _send(self, frame_type: int, payload: bytes = b"") -> None:
        with self._write_lock:
            self.port.write(SYNC + struct.pack("<BH", frame_type, len(payload)) + payload)

    def read_mic(self, on_frame: Callable[[np.ndarray], None]) -> None:
        """마이크 프레임을 받을 때마다 on_frame(float32 16kHz) 호출. 블로킹, 스레드에서 돌린다."""
        buffer = bytearray()
        while True:
            buffer += self.port.read(max(1, self.port.in_waiting))
            while True:
                start = buffer.find(SYNC)
                if start < 0:
                    del buffer[:-1]
                    break
                if len(buffer) < start + 5:
                    del buffer[:start]
                    break
                frame_type, length = struct.unpack_from("<BH", buffer, start + 2)
                end = start + 5 + length
                if len(buffer) < end:
                    del buffer[:start]
                    break
                payload = bytes(buffer[start + 5 : end])
                del buffer[:end]
                if frame_type == FRAME_MIC:
                    on_frame(np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768)
                elif frame_type == FRAME_HELLO:
                    self.hello = payload.decode(errors="replace")

    def set_volume(self, percent: int) -> None:
        self.volume = max(0, min(100, percent))
        self._send(FRAME_VOLUME, bytes([self.volume]))

    def stop_playback(self) -> None:
        self._stop_playback.set()
        self._send(FRAME_STOP)

    def play(self, samples: np.ndarray) -> None:
        """16kHz float32 오디오를 실시간 속도에 맞춰 보낸다. 블로킹."""
        self._stop_playback.clear()
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        started = time.monotonic()
        for pos in range(0, len(pcm), SPEAKER_CHUNK):
            if self._stop_playback.is_set():
                return
            # 기기 버퍼가 넘치지 않도록, 재생 시각보다 SPEAKER_LEAD_S 이상 앞서가면 기다린다
            ahead = pos / RATE - (time.monotonic() - started)
            if ahead > SPEAKER_LEAD_S:
                time.sleep(ahead - SPEAKER_LEAD_S)
            self._send(FRAME_SPEAKER, pcm[pos : pos + SPEAKER_CHUNK].tobytes())
        # 기기가 마지막 조각까지 다 재생할 때까지 기다린다
        remaining = len(pcm) / RATE - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)


class SerialMicrophone(VoiceInput):
    def __init__(self, device: SerialDevice, vad_ratio: float):
        super().__init__(RATE, vad_ratio)
        self.device = device

    @contextlib.contextmanager
    def _open(self, push):
        threading.Thread(target=self.device.read_mic, args=(push,), daemon=True).start()
        yield


class SerialSpeaker:
    def __init__(self, device: SerialDevice, mic: VoiceInput):
        self.device = device
        self.mic = mic

    def play(self, samples: np.ndarray, rate: int) -> None:
        """블로킹 재생. 재생 중과 직후 잠깐은 마이크를 막는다."""
        self.mic.muted = True
        try:
            self.device.play(resample(samples, rate, RATE))
            time.sleep(0.3)  # 스피커 잔향이 마이크로 들어가는 것 방지
        finally:
            self.mic.muted = False
