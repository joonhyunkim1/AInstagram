"""Instagram Graph API 클라이언트.

본인 소유 계정에만 게시하는 경우 Meta 앱 리뷰 없이 Development mode +
Instagram Tester 등록만으로 사용 가능하다.

캐러셀 발행 순서: 아이템별 미디어 컨테이너 생성 -> 캐러셀 부모 컨테이너 생성 -> 발행.
"""
from __future__ import annotations

import os
from typing import Any, Protocol


class HttpClient(Protocol):
    def post(self, url: str, **kwargs: Any): ...


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
        self.base_url = f"https://graph.facebook.com/{api_version}"

    def _post(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.access_token
        response = self._http.post(f"{self.base_url}/{path}", data=params, timeout=30)
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

    def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        children_ids = [self.create_carousel_item(url) for url in image_urls]
        container_id = self.create_carousel_container(children_ids, caption)
        return self.publish(container_id)

    @classmethod
    def from_env(cls) -> "InstagramClient":
        return cls(
            business_account_id=os.getenv("IG_BUSINESS_ACCOUNT_ID", ""),
            access_token=os.getenv("IG_ACCESS_TOKEN", ""),
        )
