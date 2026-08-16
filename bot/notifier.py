"""알림 모듈 — 콘솔에는 항상 출력하고, 텔레그램이 설정돼 있으면 함께 보낸다.

텔레그램 설정 (선택):
  1. 텔레그램에서 @BotFather 에게 /newbot -> 토큰 발급
  2. 만든 봇에게 아무 메시지나 보낸 뒤
     https://api.telegram.org/bot<토큰>/getUpdates 에서 chat id 확인
  3. 환경변수 설정:
       TELEGRAM_BOT_TOKEN=123456:ABC...
       TELEGRAM_CHAT_ID=987654321
"""

import os

import requests


class Notifier:
    def __init__(self) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    @property
    def telegram_on(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        print(text, flush=True)
        if not self.telegram_on:
            return
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"(텔레그램 전송 실패: {resp.status_code} {resp.text[:100]})",
                      flush=True)
        except requests.RequestException as e:
            print(f"(텔레그램 전송 실패: {e})", flush=True)
