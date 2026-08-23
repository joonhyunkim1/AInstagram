"""Telegram Bot API 래퍼.

GitHub Actions는 상시로 떠있는 서버가 아니라서, 인터랙티브 봇 프레임워크 대신
'주기적으로 getUpdates를 폴링'하는 방식으로 검수 흐름을 구현한다. 그래서 여기서는
requests로 필요한 엔드포인트만 얇게 감싼다.

버튼은 항상 행(row) 단위 리스트(list[list[dict]])로 받는다 - 수정 메뉴처럼 여러 줄로
나눠야 하는 키보드가 생겨서, 한 줄짜리 버튼도 [[...]] 형태로 감싸서 넘겨야 한다.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol

Buttons = list[list[dict[str, str]]]


class HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any): ...
    def post(self, url: str, **kwargs: Any): ...


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, http: HttpClient | None = None):
        if http is None:
            import requests as http  # type: ignore[no-redef]
        self._http = http
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._file_base_url = f"https://api.telegram.org/file/bot{bot_token}"
        self.chat_id = chat_id

    def send_photo_with_buttons(
        self, image_bytes: bytes, caption: str, buttons: Buttons
    ) -> dict:
        response = self._http.post(
            f"{self._base_url}/sendPhoto",
            data={
                "chat_id": self.chat_id,
                "caption": caption,
                "reply_markup": json.dumps({"inline_keyboard": buttons}),
            },
            files={"photo": ("preview.jpg", image_bytes, "image/jpeg")},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def send_media_group(self, media_items: list[bytes | str]) -> dict:
        """슬라이드 전체를 하나의 앨범(캐러셀 미리보기)으로 전송한다.

        각 항목은 새로 생성/업로드한 이미지 바이트이거나, 이미 R2에 올라가 있는
        이미지의 공개 URL 문자열일 수 있다 (URL이면 다시 업로드하지 않고 그대로 참조).

        Telegram sendMediaGroup은 개별 아이템에 버튼을 못 붙이기 때문에,
        채택/폐기 버튼은 뒤이어 send_message로 별도 전송해야 한다.
        """
        media = []
        files = {}
        for i, item in enumerate(media_items):
            if isinstance(item, str):
                media.append({"type": "photo", "media": item})
            else:
                name = f"photo{i}"
                media.append({"type": "photo", "media": f"attach://{name}"})
                files[name] = (f"{name}.jpg", item, "image/jpeg")

        kwargs: dict[str, Any] = {
            "data": {"chat_id": self.chat_id, "media": json.dumps(media)},
            "timeout": 60,
        }
        if files:
            kwargs["files"] = files

        response = self._http.post(f"{self._base_url}/sendMediaGroup", **kwargs)
        data = response.json()
        if not data.get("ok") and "WEBPAGE_CURL_FAILED" in data.get("description", ""):
            # R2에 막 올라간 URL을 Telegram이 아직 못 가져오는 경우가 있어서, 잠깐 쉬었다 한 번 더 시도
            time.sleep(2)
            response = self._http.post(f"{self._base_url}/sendMediaGroup", **kwargs)
            data = response.json()
        response.raise_for_status()
        return data

    def send_message(self, text: str, buttons: Buttons | None = None) -> dict:
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        response = self._http.post(f"{self._base_url}/sendMessage", data=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_updates(self, offset: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"timeout": 0}
        if offset is not None:
            params["offset"] = offset
        response = self._http.get(f"{self._base_url}/getUpdates", params=params, timeout=30)
        data = response.json()
        if not data.get("ok") and data.get("error_code") == 409:
            # 웹훅이 등록되어 있으면 getUpdates는 항상 409를 반환한다 (텔레그램 API 제약).
            # 웹훅 모드에서는 기존 스케줄 폴링이 이 메서드를 계속 호출해도 조용히 넘어가게 한다.
            return []
        response.raise_for_status()
        return data.get("result", [])

    def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self._http.post(
            f"{self._base_url}/answerCallbackQuery",
            data={"callback_query_id": callback_query_id, "text": text},
            timeout=30,
        )

    def get_file_path(self, file_id: str) -> str:
        response = self._http.get(
            f"{self._base_url}/getFile", params={"file_id": file_id}, timeout=30
        )
        response.raise_for_status()
        return response.json()["result"]["file_path"]

    def download_file(self, file_id: str) -> bytes:
        """사용자가 Telegram으로 보낸 사진을 다운로드한다 (직접 업로드로 슬라이드 추가할 때 사용)."""
        file_path = self.get_file_path(file_id)
        response = self._http.get(f"{self._file_base_url}/{file_path}", timeout=60)
        response.raise_for_status()
        return response.content

    @classmethod
    def from_env(cls) -> "TelegramClient":
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
