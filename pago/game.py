"""롤파고 → 파고쌤 연결부.

롤파고는 아래 두 가지만 로컬 HTTP 로 보내면 된다.

  POST /event   알림 한 건. text 가 있으면 바로 읽어준다.
      {"type": "enemy_flash_used", "text": "상대 원딜 점멸 빠졌어요",
       "priority": "high", "data": {"champion": "Jinx", "ready_at": 845}}

  POST /state   현재 게임 상황 전체 스냅샷. 대화할 때 참고 자료로만 쓴다.
      {"game_time": 612, "dragon_spawn_at": 660, "enemy_flash": {"Jinx": 845}}
"""

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

PRIORITIES = {"high": 0, "normal": 1}


class GameState:
    def __init__(self):
        self.snapshot: dict[str, Any] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=30)

    def describe(self) -> str:
        """LLM 에게 넘겨줄 현재 상황 요약."""
        if not self.snapshot and not self.events:
            return "현재 롤파고에서 받은 게임 정보가 없음."
        now = time.time()
        recent = [
            f"- {int(now - e['received_at'])}초 전: {e.get('text') or e['type']} {json.dumps(e.get('data', {}), ensure_ascii=False)}"
            for e in self.events
        ]
        return (
            "현재 게임 상태:\n"
            + json.dumps(self.snapshot, ensure_ascii=False, indent=1)
            + "\n\n최근 알림 (오래된 순):\n"
            + ("\n".join(recent) or "- 없음")
        )


Announce = Callable[[str, int], Awaitable[None]]


async def start_server(
    state: GameState, announce: Announce, port: int, extra_routes: list[web.RouteDef] = ()
) -> web.AppRunner:
    async def on_event(request: web.Request) -> web.Response:
        event = await request.json()
        if "type" not in event:
            return web.json_response({"error": "type 필요"}, status=400)
        event["received_at"] = time.time()
        state.events.append(event)
        if text := event.get("text"):
            await announce(text, PRIORITIES.get(event.get("priority", "normal"), 1))
        return web.json_response({"ok": True})

    async def on_state(request: web.Request) -> web.Response:
        state.snapshot = await request.json()
        return web.json_response({"ok": True})

    async def on_reset(request: web.Request) -> web.Response:
        state.snapshot = {}
        state.events.clear()
        return web.json_response({"ok": True})

    async def on_health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application()
    app.add_routes(
        [
            web.post("/event", on_event),
            web.post("/state", on_state),
            web.post("/reset", on_reset),
            web.get("/health", on_health),
            *extra_routes,
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
