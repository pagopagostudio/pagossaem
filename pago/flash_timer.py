"""음성 명령 점멸 타이머.

    "징크스 점멸"           → 타이머 등록, 30초 전과 돌아올 때 알림
    "징크스 점멸 언제 와?"   → 남은 시간 대답

음성 인식이 "진크스 전멸" 처럼 틀려도 알아듣도록 자모 단위로 비슷한 이름을 찾는다.
시간은 PC 시계 기준이라 게임을 일시정지하면 그만큼 어긋난다.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from pathlib import Path

FLASH_COOLDOWN_S = int(os.getenv("PAGO_FLASH_COOLDOWN", "300"))  # 테스트할 때 짧게 줄일 수 있다
WARN_BEFORE_S = 30

# 자주 쓰는 줄임말 → 정식 이름
ALIASES = {
    "징크": "징크스", "블츠": "블리츠크랭크", "블리츠": "블리츠크랭크", "트페": "트위스티드 페이트",
    "미포": "미스 포츈", "이즈": "이즈리얼", "케틀": "케이틀린", "리신": "리 신",
    "마이": "마스터 이", "문도": "문도 박사", "아우솔": "아우렐리온 솔", "탐켄치": "탐 켄치",
    "자르반": "자르반 4세", "누누": "누누와 윌럼프", "레나타": "레나타 글라스크",
}
QUESTION_WORDS = ("언제", "남았", "몇 초", "몇초", "몇 분", "몇분", "얼마", "있어", "됐어", "돌아와", "돌아왔")
FLASH_WORD = "점멸"

Announce = Callable[[str, int], Awaitable[None]]


def _jamo(text: str) -> str:
    """한글 음절을 초성·중성·종성으로 풀어서 비교한다. '징크스' 와 '진크스' 가 한 글자만 다르게 보이도록."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [chr(0x1100 + code // 588), chr(0x1161 + code % 588 // 28)]
            if code % 28:
                out.append(chr(0x11A7 + code % 28))
        elif not ch.isspace():
            out.append(ch)
    return "".join(out)


def _best_window(text: str, word: str) -> tuple[float, int, int]:
    """text 안에서 word 와 가장 비슷한 구간의 (유사도, 시작, 끝)."""
    target = _jamo(word)
    best = (0.0, 0, 0)
    for size in range(max(1, len(word) - 1), len(word) + 2):
        for start in range(0, max(1, len(text) - size + 1)):
            ratio = SequenceMatcher(None, _jamo(text[start : start + size]), target).ratio()
            if ratio > best[0]:
                best = (ratio, start, start + size)
    return best


def _remaining_text(seconds: float) -> str:
    seconds = max(0, round(seconds))
    minutes, sec = divmod(seconds, 60)
    if minutes and sec:
        return f"{minutes}분 {sec}초"
    return f"{minutes}분" if minutes else f"{sec}초"


class FlashTimers:
    def __init__(self, announce: Announce):
        self.announce = announce
        names = json.loads((Path(__file__).parent / "data" / "champions_ko.json").read_text())["names"]
        self.names = {name.replace(" ", ""): name for name in names}
        self.names.update({alias: full for alias, full in ALIASES.items()})
        self.ready_at: dict[str, float] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _find_champion(self, name_part: str) -> str | None:
        """name_part 전체와 가장 비슷한 챔피언. 부분 일치로 찾으면 '진크스' 가 '진' 으로 잡힌다."""
        target = _jamo(name_part)
        best_name, best_ratio = None, 0.0
        for key, name in self.names.items():
            ratio = SequenceMatcher(None, target, _jamo(key)).ratio()
            if ratio > best_ratio:
                best_name, best_ratio = name, ratio
        return best_name if best_ratio >= 0.7 else None

    async def handle(self, text: str, spoken_at: float) -> str | None:
        """점멸 명령이면 대답할 문장을, 아니면 None 을 돌려준다.

        spoken_at 은 말을 끝낸 시각(time.monotonic). 음성 인식에 걸린 시간만큼 타이머가 늦어지지 않게 한다.
        """
        compact = re.sub(r"[^\w]", "", text)
        ratio, start, end = _best_window(compact, FLASH_WORD)
        if ratio < 0.7:
            return None
        # 챔피언 이름은 보통 '점멸' 앞에 온다 ("징크스 점멸 언제 와")
        name_part = compact[:start] or compact[end:]
        champion = self._find_champion(name_part) if name_part else None
        if champion is None:
            return "누구 점멸인지 못 알아들었어요. 챔피언 이름이랑 같이 다시 말해 주세요."

        if any(word in text for word in QUESTION_WORDS):
            return self._status(champion)
        self._start(champion, spoken_at)
        return f"{champion} 점멸, {_remaining_text(self.ready_at[champion] - time.monotonic())} 뒤에 돌아와요."

    def _status(self, champion: str) -> str:
        ready_at = self.ready_at.get(champion)
        if ready_at is None:
            return f"{champion} 점멸은 기록이 없어요."
        left = ready_at - time.monotonic()
        if left <= 0:
            return f"{champion} 점멸 돌아와 있어요."
        return f"{champion} 점멸 {_remaining_text(left)} 남았어요."

    def _start(self, champion: str, spoken_at: float) -> None:
        if old := self._tasks.pop(champion, None):
            old.cancel()  # 같은 챔피언을 다시 말하면 새로 시작
        self.ready_at[champion] = spoken_at + FLASH_COOLDOWN_S
        self._tasks[champion] = asyncio.create_task(self._notify(champion))

    async def _notify(self, champion: str) -> None:
        ready_at = self.ready_at[champion]
        await asyncio.sleep(max(0, ready_at - WARN_BEFORE_S - time.monotonic()))
        await self.announce(f"{champion} 점멸 {WARN_BEFORE_S}초 남았어요.", 0)
        await asyncio.sleep(max(0, ready_at - time.monotonic()))
        await self.announce(f"{champion} 점멸 돌아왔어요.", 0)
        self._tasks.pop(champion, None)
