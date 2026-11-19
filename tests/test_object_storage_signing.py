"""用固定时间和 AWS 示例凭据验证 S3 预签名，测试全程离线。"""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.config import Settings
from app.services.object_storage_service import ObjectStorageService


@pytest.mark.parametrize(
    ("method", "content_type", "signature"),
    [
        ("GET", None, "7c375639c0a07d61a409b306e1539890d966800ee6b1df1d48a6f8b245f229a8"),
        ("PUT", "audio/mpeg", "192fb0c46009dfbde0e77413fa6eab9ef5a74e4a1ac6fa92b5513feb48e9dfba"),
        ("DELETE", None, "65accadaa67d830870132d217d6f81dea20404b497babcc51928f75aec25ec50"),
    ],
)
def test_s3_presigning_matches_sigv4_reference(monkeypatch, method, content_type, signature):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2013, 5, 24, tzinfo=timezone.utc)

    settings = Settings(
        _env_file=None,
        object_storage_mode="s3",
        object_storage_endpoint_url="https://s3.example.invalid",
        object_storage_bucket="examplebucket",
        object_storage_region="us-east-1",
        object_storage_access_key_id="AKIAIOSFODNN7EXAMPLE",
        object_storage_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    monkeypatch.setattr("app.services.object_storage_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.object_storage_service.datetime", FixedClock)
    storage = ObjectStorageService("")
    url = urlsplit(storage._presign(method, "a b.wav", 600, content_type=content_type))
    assert url.path == "/examplebucket/a%20b.wav"
    assert parse_qs(url.query)["X-Amz-Signature"] == [signature]
