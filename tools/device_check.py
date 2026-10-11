"""파고쌤 기기 점검: 인사말을 기기 스피커로 말하고, 기기 마이크로 세 마디 듣고 받아쓴다.

    python tools/device_check.py /dev/cu.usbserial-0001
"""

import asyncio
import sys

import numpy as np
import soundfile as sf

from pago.serial_device import SerialDevice, SerialMicrophone, SerialSpeaker
from pago.stt import SpeechToText
from pago.tts import TextToSpeech


async def main(port: str) -> None:
    device = SerialDevice(port)
    device.set_volume(20)
    mic = SerialMicrophone(device, vad_ratio=3.0)
    speaker = SerialSpeaker(device, mic)
    stt = await asyncio.to_thread(SpeechToText, "medium")

    samples, rate = await TextToSpeech("ko-KR-SunHiNeural").synthesize("안녕하세요, 파고쌤이에요. 아무 말이나 해보세요.")
    await asyncio.to_thread(speaker.play, samples, rate)
    print("듣는 중... 말해보세요")

    count = 0
    async for audio in mic.utterances():
        count += 1
        rms_db = 20 * np.log10(np.sqrt(np.mean(audio**2)) + 1e-9)
        print(f"들은 길이 {len(audio) / 16000:.1f}초, 음량 {rms_db:.1f}dB, 최대 {np.abs(audio).max():.2f}")
        print(f"받아쓰기: {stt.transcribe(audio)!r}")
        sf.write(f"device_check_{count}.wav", audio, 16000)
        if count == 3:
            break


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbserial-0001"))
