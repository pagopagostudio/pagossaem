"""브라우저에서 여는 파고쌤 조절 화면. http://127.0.0.1:8765/

기기는 PC 에 사운드카드가 아니라 시리얼 포트로 잡혀서 OS 볼륨으로는 조절이 안 된다.
그래서 볼륨은 이 화면에서 기기로 직접 명령을 보내 바꾼다.
"""

import json

from aiohttp import web

from .game import Announce
from .serial_device import SerialDevice

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>파고쌤</title>
<style>
  body { font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; background: #111827; color: #f3f4f6;
         display: grid; place-items: center; min-height: 100vh; margin: 0; padding: 0 16px; }
  .card { background: #1f2937; border-radius: 16px; padding: 28px; width: 100%; max-width: 360px; }
  h1 { margin: 0 0 20px; font-size: 22px; }
  label { display: flex; justify-content: space-between; font-size: 15px; margin-bottom: 10px; }
  #value { font-variant-numeric: tabular-nums; font-weight: 600; }
  input[type=range] { width: 100%; accent-color: #60a5fa; }
  button { margin-top: 20px; width: 100%; padding: 12px; border: 0; border-radius: 10px;
           background: #374151; color: inherit; font-size: 15px; cursor: pointer; }
  button:hover { background: #4b5563; }
</style>
<div class="card">
  <h1>파고쌤</h1>
  <label for="volume">스피커 볼륨 <span id="value">VOLUME</span></label>
  <input id="volume" type="range" min="0" max="100" step="5" value="VOLUME">
  <button id="test">테스트 소리 듣기</button>
</div>
<script>
  const slider = document.getElementById("volume");
  const value = document.getElementById("value");
  slider.addEventListener("input", () => {
    value.textContent = slider.value;
    fetch("/volume", { method: "POST", body: JSON.stringify({ percent: Number(slider.value) }) });
  });
  document.getElementById("test").addEventListener("click", () => fetch("/speak-test", { method: "POST" }));
</script>
"""


def routes(device: SerialDevice, announce: Announce) -> list[web.RouteDef]:
    async def page(request: web.Request) -> web.Response:
        return web.Response(text=PAGE.replace("VOLUME", str(device.volume)), content_type="text/html")

    async def set_volume(request: web.Request) -> web.Response:
        body = json.loads(await request.text())
        device.set_volume(int(body["percent"]))
        return web.json_response({"volume": device.volume})

    async def speak_test(request: web.Request) -> web.Response:
        await announce(f"지금 볼륨은 {device.volume}이에요.", 0)
        return web.json_response({"ok": True})

    return [
        web.get("/", page),
        web.post("/volume", set_volume),
        web.post("/speak-test", speak_test),
    ]
