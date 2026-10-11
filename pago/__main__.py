"""파고쌤 실행.

    python -m pago           # 실행
    python -m pago devices   # 오디오 장치 목록
"""

import asyncio
import itertools
import os
import sys
import time
import webbrowser

from . import config, control_panel
from .audio_io import Microphone, Speaker, list_devices
from .brain import Brain
from .flash_timer import FlashTimers
from .game import GameState, start_server
from .serial_device import SerialDevice, SerialMicrophone, SerialSpeaker
from .stt import SpeechToText
from .tts import TextToSpeech

CHAT_PRIORITY = 2  # 게임 알림(0 high, 1 normal)이 대화 대답보다 먼저 나간다


async def run() -> None:
    cfg = config.load()
    device = None
    if cfg.serial_port:
        device = SerialDevice(cfg.serial_port)
        device.set_volume(cfg.volume)
        mic = SerialMicrophone(device, cfg.vad_ratio)
        speaker = SerialSpeaker(device, mic)
        print(f"[pago] 파고쌤 기기 사용: {cfg.serial_port}")
    else:
        mic = Microphone(cfg.input_device, cfg.vad_ratio)
        speaker = Speaker(cfg.output_device, mic)
    tts = TextToSpeech(cfg.tts_voice)
    state = GameState()
    # API 키가 없으면 점멸 타이머 같은 정해진 명령만 동작하고 자유 대화는 꺼진다
    brain = Brain(cfg.claude_model, state) if os.getenv("ANTHROPIC_API_KEY") else None
    if brain is None:
        print("[pago] ANTHROPIC_API_KEY 없음: 점멸 타이머만 동작, 자유 대화는 꺼짐")

    print(f"[pago] 음성 인식 모델({cfg.whisper_model}) 불러오는 중... 첫 실행은 다운로드 때문에 오래 걸림")
    stt = await asyncio.to_thread(SpeechToText, cfg.whisper_model)

    speech_queue: asyncio.PriorityQueue[tuple[int, int, str]] = asyncio.PriorityQueue()
    order = itertools.count()

    async def announce(text: str, priority: int) -> None:
        await speech_queue.put((priority, next(order), text))

    timers = FlashTimers(announce)

    async def speak_loop() -> None:
        while True:
            priority, _, text = await speech_queue.get()
            print(f"[pago] 🔊 {text}")
            try:
                samples, rate = await tts.synthesize(text)
                await asyncio.to_thread(speaker.play, samples, rate)
            except Exception as e:
                print(f"[pago] 말하기 실패: {e}")

    async def listen_loop() -> None:
        async for audio in mic.utterances():
            spoken_at = time.monotonic()  # 음성 인식에 걸린 시간만큼 타이머가 늦어지지 않게 미리 기록
            text = await asyncio.to_thread(stt.transcribe, audio)
            if not text:
                continue
            print(f"[pago] 🎤 {text}")
            if command_reply := await timers.handle(text, spoken_at):
                await announce(command_reply, 0)
            elif brain and (answer := await brain.reply(text)):
                await announce(answer, CHAT_PRIORITY)

    panel = control_panel.routes(device, announce) if device else []
    await start_server(state, announce, cfg.port, panel)
    print(f"[pago] 롤파고 이벤트 대기 중: http://127.0.0.1:{cfg.port}/event")
    if device:
        print(f"[pago] 조절 화면: http://127.0.0.1:{cfg.port}/")
        webbrowser.open(f"http://127.0.0.1:{cfg.port}/")
    await announce("파고쌤 준비됐어요.", CHAT_PRIORITY)
    await asyncio.gather(speak_loop(), listen_loop())


def main() -> None:
    if sys.argv[1:] == ["devices"]:
        list_devices()
        return
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


main()
