"""롤파고 흉내. 실제 서비스 연결 전에 파고쌤 알림/대화를 테스트할 때 쓴다.

    python tools/fake_rollpago.py            # 시나리오 재생
    python tools/fake_rollpago.py "할 말"    # 알림 한 건만 보내기
"""

import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765"


def post(path: str, body: dict) -> None:
    request = urllib.request.Request(
        URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request).read()


def scenario() -> None:
    post("/reset", {})
    post(
        "/state",
        {
            "game_time": "10:12",
            "my_champion": "Ahri",
            "next_dragon": {"type": "화염", "spawn_at": "11:00"},
            "enemy_flash_ready_at": {"Jinx(원딜)": "14:05", "Lee Sin(정글)": "준비됨"},
        },
    )
    post("/event", {"type": "enemy_flash_used", "priority": "high",
                    "text": "상대 징크스 점멸 빠졌어요. 14분 5초에 돌아와요.",
                    "data": {"champion": "Jinx", "ready_at": "14:05"}})
    time.sleep(6)
    post("/event", {"type": "dragon_soon", "priority": "normal",
                    "text": "화염 드래곤 40초 뒤에 나와요. 바텀 시야 잡아두세요.",
                    "data": {"dragon": "화염", "spawn_at": "11:00"}})
    print("보냄. 이제 마이크에 '용 언제 나와?' 나 '징크스 점멸 있어?' 라고 물어보세요.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        post("/event", {"type": "manual", "priority": "high", "text": sys.argv[1]})
    else:
        scenario()
