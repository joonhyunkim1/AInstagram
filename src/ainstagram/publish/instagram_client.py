"""Instagram Graph API 클라이언트.

2024년 7월부터 생긴 'Instagram API with Instagram Login' 방식을 쓴다.
Facebook 페이지 연결 없이 Instagram 비즈니스/크리에이터 계정으로 바로 로그인해서
토큰을 발급받을 수 있어서, 페이지 연결이 필요한 구방식(graph.facebook.com)보다 설정이 간단하다.
본인 소유 계정에만 게시하는 경우 Meta 앱 리뷰도 필요 없다.

캐러셀 발행 순서: 아이템별 미디어 컨테이너 생성 -> 캐러셀 부모 컨테이너 생성 -> 발행.
"""
from __future__ import annotations

import os
import time
from typing import Any, Protocol


class HttpClient(Protocol):
    def post(self, url: str, **kwargs: Any): ...
    def get(self, url: str, **kwargs: Any): ...


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

    def _post(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.access_token
        response = self._http.post(f"{self.base_url}/{path}", data=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def _get(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.access_token
        response = self._http.get(f"{self.base_url}/{path}", params=params, timeout=30)
        response.raise_for_status()
        return response.json()

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

    def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        children_ids = [self.create_carousel_item(url) for url in image_urls]
        container_id = self.create_carousel_container(children_ids, caption)
        self.wait_until_finished(container_id)
        return self.publish(container_id)

    @classmethod
    def from_env(cls) -> "InstagramClient":
        return cls(
            business_account_id=os.getenv("IG_BUSINESS_ACCOUNT_ID", ""),
            access_token=os.getenv("IG_ACCESS_TOKEN", ""),
        )
