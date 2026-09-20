"""Instagram Graph API 클라이언트.

2024년 7월부터 생긴 'Instagram API with Instagram Login' 방식을 쓴다.
Facebook 페이지 연결 없이 Instagram 비즈니스/크리에이터 계정으로 바로 로그인해서
토큰을 발급받을 수 있어서, 페이지 연결이 필요한 구방식(graph.facebook.com)보다 설정이 간단하다.
본인 소유 계정에만 게시하는 경우 Meta 앱 리뷰도 필요 없다.

캐러셀 발행 순서: 아이템별 미디어 컨테이너 생성 -> 캐러셀 부모 컨테이너 생성 -> 발행.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class HttpClient(Protocol):
    def post(self, url: str, **kwargs: Any): ...
    def get(self, url: str, **kwargs: Any): ...


class InstagramAPIError(RuntimeError):
    """Graph API가 에러 응답을 줬을 때. 응답 본문의 사유를 메시지에 담는다.

    requests의 기본 HTTPError는 토큰이 쿼리스트링에 담긴 URL을 메시지에 넣기 때문에
    (GET 요청), 그대로 텔레그램 등으로 내보내면 토큰이 노출될 수 있다. 여기서는 URL 대신
    경로와 응답 본문만 담는다.
    """


class ContainerProcessingError(RuntimeError):
    """미디어 컨테이너가 ERROR 상태로 처리 실패했을 때."""


class ContainerProcessingTimeout(RuntimeError):
    """미디어 컨테이너가 제한 시간 안에 FINISHED가 되지 않았을 때."""


class InstagramClient:
    def __init__(
        self,
        business_account_id: str,
        access_token: str,
        http: HttpClient | None = None,
        api_version: str = "v21.0",
    ):
        if http is None:
            import requests as http  # type: ignore[no-redef]
        self._http = http
        self.business_account_id = business_account_id
        self.access_token = access_token
        self.base_url = f"https://graph.instagram.com/{api_version}"

    @staticmethod
    def _describe_error(response: Any, path: str) -> str:
        status = getattr(response, "status_code", "?")
        try:
            error = response.json().get("error", {})
        except Exception:
            error = {}
        if error:
            details = ", ".join(
                f"{key}={error[key]}"
                for key in ("type", "code", "error_subcode")
                if error.get(key) is not None
            )
            user_msg = error.get("error_user_msg") or error.get("error_user_title")
            message = error.get("message", "")
            if user_msg:
                message = f"{message} / {user_msg}"
            return f"Instagram API 오류 {status} ({path}): {message} [{details}]"
        text = str(getattr(response, "text", ""))[:300]
        return f"Instagram API 오류 {status} ({path}): {text}"

    def _check(self, response: Any, path: str) -> dict:
        try:
            response.raise_for_status()
        except Exception:
            raise InstagramAPIError(self._describe_error(response, path)) from None
        return response.json()

    def _post(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.access_token
        response = self._http.post(f"{self.base_url}/{path}", data=params, timeout=30)
        return self._check(response, path)

    def _get(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.access_token
        response = self._http.get(f"{self.base_url}/{path}", params=params, timeout=30)
        return self._check(response, path)

    def create_carousel_item(self, image_url: str) -> str:
        data = self._post(
            f"{self.business_account_id}/media", image_url=image_url, is_carousel_item="true"
        )
        return data["id"]

    def create_carousel_container(self, children_ids: list[str], caption: str) -> str:
        data = self._post(
            f"{self.business_account_id}/media",
            media_type="CAROUSEL",
            children=",".join(children_ids),
            caption=caption,
        )
        return data["id"]

    def publish(self, creation_id: str) -> str:
        data = self._post(f"{self.business_account_id}/media_publish", creation_id=creation_id)
        return data["id"]

    def get_container_status(self, container_id: str) -> str:
        data = self._get(container_id, fields="status_code")
        return data["status_code"]

    def wait_until_finished(
        self, container_id: str, timeout: float = 60, interval: float = 3
    ) -> None:
        """캐러셀 부모 컨테이너는 비동기로 처리되기 때문에, FINISHED가 되기 전에
        media_publish를 호출하면 'Media ID is not available' 에러가 난다.
        """
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_container_status(container_id)
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise ContainerProcessingError(f"미디어 컨테이너 처리 실패: {container_id}")
            if time.monotonic() >= deadline:
                raise ContainerProcessingTimeout(
                    f"미디어 컨테이너 처리 대기 시간 초과: {container_id} (status={status})"
                )
            time.sleep(interval)

    @staticmethod
    def _normalize_caption(caption: str) -> str:
        return " ".join(caption.split())

    def find_recent_media(
        self,
        caption: str,
        since: datetime,
        attempts: int = 3,
        interval: float = 5,
    ) -> str | None:
        """since 이후에 올라간 게시물 중 캡션이 같은 것의 ID를 찾는다 (없으면 None).

        media_publish가 에러를 반환했지만 실제로는 게시된 경우를 가려내는 용도.
        게시물이 목록에 반영되기까지 약간 걸릴 수 있어서 몇 번 재조회한다.
        """
        wanted = self._normalize_caption(caption)
        for attempt in range(attempts):
            try:
                data = self._get("me/media", fields="id,caption,timestamp", limit=10)
            except Exception:
                logger.warning("최근 게시물 조회에 실패해 게시 여부를 확인하지 못했습니다.")
                return None
            for media in data.get("data", []):
                try:
                    posted_at = datetime.strptime(media["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
                except (KeyError, ValueError):
                    continue
                if posted_at >= since and self._normalize_caption(media.get("caption") or "") == wanted:
                    return media["id"]
            if attempt < attempts - 1:
                time.sleep(interval)
        return None

    def iter_media(self, page_size: int = 50, max_pages: int = 20):
        """계정의 게시물을 최신순으로 순회한다 (id, caption, timestamp, media_type)."""
        after = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"fields": "id,caption,timestamp,media_type", "limit": page_size}
            if after:
                params["after"] = after
            data = self._get("me/media", **params)
            yield from data.get("data", [])
            paging = data.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not after:
                return

    def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        started_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        children_ids = [self.create_carousel_item(url) for url in image_urls]
        container_id = self.create_carousel_container(children_ids, caption)
        self.wait_until_finished(container_id)
        try:
            return self.publish(container_id)
        except InstagramAPIError as e:
            # 인스타그램이 게시는 해놓고 에러 응답을 주는 경우가 있어서, 실제로 올라갔는지 확인한다.
            media_id = self.find_recent_media(caption, since=started_at)
            if media_id is None:
                raise
            logger.warning("media_publish는 에러를 반환했지만 게시물은 올라간 것을 확인했습니다: %s / %s", media_id, e)
            return media_id

    @classmethod
    def from_env(cls) -> "InstagramClient":
        return cls(
            business_account_id=os.getenv("IG_BUSINESS_ACCOUNT_ID", ""),
            access_token=os.getenv("IG_ACCESS_TOKEN", ""),
        )
