"""Cloudflare R2(S3 호환) 업로드. Instagram Graph API는 이미지를 공개 URL로 fetch하므로
어딘가에는 호스팅해야 하는데, R2는 무료 티어에 이그레스 비용도 없어서 이 용도로 충분하다.
"""
from __future__ import annotations

import io
import os
from typing import Protocol

from PIL import Image


class S3LikeClient(Protocol):
    def put_object(self, **kwargs): ...


def get_r2_client() -> S3LikeClient:
    import boto3

    account_id = os.getenv("R2_ACCOUNT_ID", "")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
        region_name="auto",
    )


def upload_image(client: S3LikeClient, image: Image.Image, key: str) -> str:
    bucket = os.getenv("R2_BUCKET_NAME", "")
    public_base_url = os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/")

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    client.put_object(Bucket=bucket, Key=key, Body=buf, ContentType="image/jpeg")
    return f"{public_base_url}/{key}"
