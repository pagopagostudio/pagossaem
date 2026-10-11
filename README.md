# 파고쌤

롤파고 알림(점멸 예측, 용 타이밍 등)을 말로 알려주고, 게임 중에 음성으로 대화하는 책상용 스피커 로봇.

```
[기기: ESP32 + INMP441 x2 + MAX98357A]  ══USB 시리얼══►  [PC: 이 프로그램]
        16kHz 마이크 ↑ / 스피커 ↓                        음성 인식 → Claude → 음성 합성
                                                           ▲
                                                 롤파고 ── POST /event, /state
```

`.env` 에 `PAGO_SERIAL_PORT` 를 적으면 기기를, 비워두면 PC 마이크/스피커를 쓴다.

## 기기

- 배선(검증된 핀맵): 마이크 SCK 14 / WS 15 / SD 32, 앰프 BCLK 26 / LRC 25 / DIN 22.
  마이크 모듈을 180° 뒤집어 꽂으면 손상된다. 두 모듈의 인쇄 글자 방향을 맞출 것.
- 펌웨어 올리기: `./firmware/flash.sh pago_device` (ESP-IDF v5.5, `~/esp/esp-idf`)
- 점검: `PYTHONPATH=. .venv/bin/python tools/device_check.py` → 기기에서 인사말, 세 마디 받아쓰기
- `firmware/mic_speaker_test` 는 배선 확인용 (L/R 음량 표시, 말하면 3초 녹음 후 재생)

## 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # ANTHROPIC_API_KEY 입력

.venv/bin/python -m pago devices   # 장치 이름 확인
.venv/bin/python -m pago           # 실행
```

다른 터미널에서 롤파고 흉내 내기:

```bash
.venv/bin/python tools/fake_rollpago.py              # 점멸/용 시나리오
.venv/bin/python tools/fake_rollpago.py "바론 1분 전이에요"
```

## 롤파고 연동

롤파고에서 `http://127.0.0.1:8765` 로 JSON 을 보낸다.

| 경로 | 용도 | 예시 |
|---|---|---|
| `POST /event` | 알림 한 건. `text` 가 있으면 바로 읽음. `priority` 는 `high` / `normal` | `{"type": "enemy_flash_used", "priority": "high", "text": "상대 징크스 점멸 빠졌어요", "data": {"ready_at": "14:05"}}` |
| `POST /state` | 현재 게임 상황 스냅샷. 질문에 대답할 때 참고 | `{"game_time": "10:12", "next_dragon": {"spawn_at": "11:00"}}` |
| `POST /reset` | 새 게임 시작 시 초기화 | `{}` |

알림 문장은 롤파고 쪽에서 완성해서 보내는 것을 권장한다. LLM 을 거치지 않으니 지연이 거의 없다.

## 현재 한계 (하드웨어 단계에서 해결)

- 스피커가 말하는 동안은 마이크를 꺼둔다 (반이중). 말 끊고 끼어들기는 에코 제거를 붙인 뒤에 가능.
- 말소리 감지는 단순 음량 기준이라 게임 소리나 음악이 크면 오작동할 수 있다. `PAGO_VAD_RATIO` 로 조절하거나, 나중에 기기 버튼(누르고 말하기)으로 바꾼다.
