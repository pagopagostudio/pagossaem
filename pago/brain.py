"""대화 두뇌 (Claude). 롤파고 게임 상태를 참고해서 짧게 말로 대답한다."""

import anthropic

from .game import GameState

SYSTEM_PROMPT = """너는 '파고쌤'이야. 리그 오브 레전드를 하고 있는 플레이어 옆 책상 위에 놓인 작은 스피커 로봇이고, 롤파고라는 분석 서비스가 보내주는 게임 정보(점멸 쿨타임 예측, 드래곤·바론 타이밍 등)를 바탕으로 플레이어와 음성으로 대화해.

너의 대답은 음성 합성으로 바로 읽힌다. 그래서:
- 한두 문장, 길어도 세 문장 안으로 짧게 말해. 플레이어는 게임 중이라 긴 말을 들을 여유가 없어.
- 마크다운, 목록, 이모지, 괄호 설명은 쓰지 마. 소리 내어 읽기 자연스러운 구어체 존댓말로 말해.
- 시간은 '3분 20초', '40초 뒤'처럼 귀로 듣기 쉽게 말해.
- 게임 상태에 없는 정보는 지어내지 말고, 모르면 모른다고 짧게 말해.
- 음성 인식 결과라 오타나 잘못 알아들은 단어가 있을 수 있어. 게임 맥락으로 가장 그럴듯하게 해석해.

매 질문마다 롤파고가 보낸 최신 게임 상태가 <game_state> 안에 같이 들어와."""

MAX_HISTORY_TURNS = 8


class Brain:
    def __init__(self, model: str, state: GameState):
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.state = state
        self.history: list[dict] = []

    async def reply(self, user_text: str) -> str | None:
        content = f"<game_state>\n{self.state.describe()}\n</game_state>\n\n{user_text}"
        messages = [*self.history, {"role": "user", "content": content}]

        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=messages,
                # 음성 대화는 빠른 응답이 중요해서 생각을 짧게 한다
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            return "잠시 요청이 많아서 대답을 못 했어요. 조금 뒤에 다시 물어봐 주세요."
        except anthropic.APIConnectionError:
            return "인터넷 연결이 불안정해서 대답을 못 했어요."
        except anthropic.APIStatusError as e:
            print(f"[brain] API 오류 {e.status_code}: {e.message}")
            return None

        if response.stop_reason == "refusal":
            return "그건 대답하기 어려워요."

        answer = "".join(block.text for block in response.content if block.type == "text").strip()
        if not answer:
            return None

        # 게임 상태는 매번 새로 넣으니 기록에는 질문만 남긴다
        self.history += [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer},
        ]
        self.history = self.history[-MAX_HISTORY_TURNS * 2 :]
        return answer
