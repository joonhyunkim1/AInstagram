"""Telegram Bot API 래퍼.

GitHub Actions는 상시로 떠있는 서버가 아니라서, 인터랙티브 봇 프레임워크 대신
'주기적으로 getUpdates를 폴링'하는 방식으로 검수 흐름을 구현한다. 그래서 여기서는
requests로 필요한 엔드포인트만 얇게 감싼다.
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol


class HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any): ...
    def post(self, url: str, **kwargs: Any): ...


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, http: HttpClient | None = None):
        if http is None:
            import requests as http  # type: ignore[no-redef]
        self._http = http
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self.chat_id = chat_id

    def send_photo_with_buttons(
        self, image_bytes: bytes, caption: str, buttons: list[dict[str, str]]
    ) -> dict:
        response = self._http.post(
            f"{self._base_url}/sendPhoto",
            data={
                "chat_id": self.chat_id,
                "caption": caption,
                "reply_markup": json.dumps({"inline_keyboard": [buttons]}),
            },
            files={"photo": ("preview.jpg", image_bytes, "image/jpeg")},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_updates(self, offset: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"timeout": 0}
        if offset is not None:
            params["offset"] = offset
        response = self._http.get(f"{self._base_url}/getUpdates", params=params, timeout=30)
        response.raise_for_status()
        return response.json().get("result", [])

    def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self._http.post(
            f"{self._base_url}/answerCallbackQuery",
            data={"callback_query_id": callback_query_id, "text": text},
            timeout=30,
        )

    @classmethod
    def from_env(cls) -> "TelegramClient":
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
