"""[新增] 可切换的本地 Mock 与 S3 兼容 OSS 存储适配器。"""

import base64
import binascii
import codecs
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from fastapi import HTTPException
from fastapi import Request as FastAPIRequest

from app.core.config import get_settings


class ObjectStorageService:
    """Mock 模式写入本机磁盘；S3 模式生成短时 SigV4 URL。"""

    def __init__(self, public_base_url: str) -> None:
        settings = get_settings()
        self.settings = settings
        self.public_base_url = public_base_url.rstrip("/")
        self.endpoint = (settings.object_storage_endpoint_url or "").rstrip("/")
        self.local_root: Path | None = None

        if settings.object_storage_mode == "mock":
            configured_path = Path(settings.object_storage_mock_directory)
            self.local_root = (
                configured_path
                if configured_path.is_absolute()
                else Path(__file__).resolve().parents[2] / configured_path
            )
            self.local_root.mkdir(parents=True, exist_ok=True)
        elif (
            not self.endpoint
            or not settings.object_storage_bucket
            or not settings.object_storage_access_key_id
            or not settings.object_storage_secret_access_key.get_secret_value()
        ):
            raise HTTPException(status_code=503, detail="S3 模式下对象存储尚未完整配置")

    @property
    def mode(self) -> str:
        return self.settings.object_storage_mode

    @property
    def provider(self) -> str:
        return "mock" if self.mode == "mock" else self.settings.object_storage_provider

    @property
    def region(self) -> str:
        return "local" if self.mode == "mock" else self.settings.object_storage_region

    @property
    def bucket(self) -> str:
        return "local-mock" if self.mode == "mock" else self.settings.object_storage_bucket

    @staticmethod
    def _signing_key(secret: str, date: str, region: str) -> bytes:
        def sign(key: bytes, value: str) -> bytes:
            return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()

        date_key = sign(("AWS4" + secret).encode("utf-8"), date)
        region_key = sign(date_key, region)
        service_key = sign(region_key, "s3")
        return sign(service_key, "aws4_request")

    def _presign(
        self, method: str, key: str, expires_in: int, content_type: str | None = None
    ) -> str:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        parts = urlsplit(self.endpoint)
        encoded_path = "/".join(quote(part, safe="-_.~") for part in key.split("/"))
        canonical_uri = f"{parts.path.rstrip('/')}/{quote(self.bucket, safe='-_.~')}/{encoded_path}"
        headers = {"host": parts.netloc}
        signed_headers = "host"
        if content_type is not None:
            headers["content-type"] = content_type.strip()
            headers["if-none-match"] = "*"
            signed_headers = ";".join(sorted(headers))
        credential_scope = f"{date}/{self.settings.object_storage_region}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.settings.object_storage_access_key_id}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": signed_headers,
        }
        canonical_query = urlencode(sorted(params.items()), quote_via=quote, safe="-_.~")
        # SigV4 要求规范请求头块以换行结尾，后续字段另起一行。
        canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in sorted(headers))
        canonical_request = "\n".join(
            (
                method,
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                "UNSIGNED-PAYLOAD",
            )
        )
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        signature = hmac.new(
            self._signing_key(
                self.settings.object_storage_secret_access_key.get_secret_value(),
                date,
                self.settings.object_storage_region,
            ),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                canonical_uri,
                f"{canonical_query}&X-Amz-Signature={signature}",
                "",
            )
        )

    def _mock_token(
        self,
        method: str,
        key: str,
        content_type: str,
        expires_in: int,
        max_bytes: int | None = None,
    ) -> str:
        payload = {
            "method": method,
            "key": key,
            "content_type": content_type,
            "expires_at": int(datetime.now(timezone.utc).timestamp()) + expires_in,
            "max_bytes": max_bytes,
        }
        encoded = (
            base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        secret = self.settings.object_storage_mock_secret.get_secret_value().encode()
        signature = hmac.new(secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _decode_mock_token(self, token: str, expected_method: str) -> dict:
        try:
            encoded, signature = token.rsplit(".", 1)
            secret = self.settings.object_storage_mock_secret.get_secret_value().encode()
            expected = hmac.new(secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload["method"] != expected_method or payload["expires_at"] <= int(
                datetime.now(timezone.utc).timestamp()
            ):
                raise ValueError
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise HTTPException(status_code=403, detail="Mock 存储链接无效或已过期") from exc

    def _local_path(self, key: str) -> Path:
        assert self.local_root is not None
        path = (self.local_root / key).resolve()
        if not path.is_relative_to(self.local_root.resolve()):
            raise HTTPException(status_code=403, detail="无效的 Mock 对象键")
        return path

    def create_upload_url(
        self, key: str, content_type: str, expires_in: int, max_bytes: int
    ) -> str:
        if self.mode == "mock":
            token = self._mock_token("PUT", key, content_type, expires_in, max_bytes)
            return f"{self.public_base_url}/api/mock-storage/upload/{token}"
        return self._presign("PUT", key, expires_in, content_type)

    async def receive_mock_upload(self, token: str, request: FastAPIRequest) -> None:
        if self.mode != "mock":
            raise HTTPException(status_code=404, detail="Mock 上传接口未启用")
        payload = self._decode_mock_token(token, "PUT")
        if request.headers.get("content-type", "").strip() != payload["content_type"]:
            raise HTTPException(status_code=422, detail="Content-Type 与上传授权不匹配")
        if request.headers.get("if-none-match") != "*":
            raise HTTPException(status_code=428, detail="上传请求必须携带 If-None-Match: *")
        path = self._local_path(payload["key"])
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = path.with_suffix(path.suffix + ".meta.json")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise HTTPException(status_code=412, detail="该上传授权已使用") from exc
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as target:
                async for block in request.stream():
                    size += len(block)
                    if size > payload["max_bytes"]:
                        raise HTTPException(status_code=413, detail="上传文件超过授权大小")
                    target.write(block)
            metadata_path.write_text(
                json.dumps({"content_type": payload["content_type"]}), encoding="utf-8"
            )
        except BaseException:
            path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            raise

    def mock_download(self, token: str) -> tuple[Path, str]:
        if self.mode != "mock":
            raise HTTPException(status_code=404, detail="Mock 下载接口未启用")
        payload = self._decode_mock_token(token, "GET")
        path = self._local_path(payload["key"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Mock 文件不存在")
        metadata_path = path.with_suffix(path.suffix + ".meta.json")
        try:
            content_type = json.loads(metadata_path.read_text(encoding="utf-8"))["content_type"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=404, detail="Mock 文件信息不存在") from exc
        return path, content_type

    def inspect_and_hash(
        self, key: str, max_bytes: int, validate_utf8: bool = False
    ) -> tuple[int, str, str, bytes]:
        utf8_decoder = codecs.getincrementaldecoder("utf-8")() if validate_utf8 else None

        def validate_text_chunk(block: bytes, final: bool = False) -> None:
            if utf8_decoder is None:
                return
            if b"\x00" in block:
                raise HTTPException(status_code=422, detail="文本文件不能包含 NUL 字节")
            try:
                utf8_decoder.decode(block, final=final)
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=422, detail="文本文件必须是有效的 UTF-8") from exc

        if self.mode == "mock":
            path = self._local_path(key)
            metadata_path = path.with_suffix(path.suffix + ".meta.json")
            if not path.is_file() or not metadata_path.is_file():
                raise HTTPException(status_code=409, detail="Mock 存储中未找到已上传文件")
            content_type = json.loads(metadata_path.read_text(encoding="utf-8"))["content_type"]
            digest = hashlib.sha256()
            size = 0
            prefix = bytearray()
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > max_bytes:
                        raise HTTPException(status_code=413, detail="上传文件超过已声明大小")
                    validate_text_chunk(block)
                    if len(prefix) < 32:
                        prefix.extend(block[: 32 - len(prefix)])
                    digest.update(block)
            validate_text_chunk(b"", final=True)
            return size, content_type, digest.hexdigest(), bytes(prefix)

        url = self._presign("GET", key, 120)
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        try:
            with urlopen(Request(url, method="GET"), timeout=30) as response:
                content_type = response.headers.get("Content-Type", "")
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > max_bytes:
                        raise HTTPException(status_code=413, detail="OSS 文件超过已声明大小")
                    validate_text_chunk(block)
                    if len(prefix) < 32:
                        prefix.extend(block[: 32 - len(prefix)])
                    digest.update(block)
                validate_text_chunk(b"", final=True)
        except HTTPError as exc:
            if exc.code == 404:
                raise HTTPException(status_code=409, detail="OSS 中未找到已上传文件") from exc
            raise HTTPException(status_code=502, detail="对象存储校验失败") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=502, detail="无法连接对象存储校验文件") from exc
        return size, content_type, digest.hexdigest(), bytes(prefix)

    # 文本抽取仅读取已确认对象的有限字节数。
    def read_text_source(self, key: str, max_bytes: int, timeout: float) -> bytes:
        """Read a bounded extractable text object without exposing a signed URL to clients."""
        return self.read_object(key, max_bytes, timeout)

    # 图片校验复用受限媒体读取路径。
    def read_object(self, key: str, max_bytes: int, timeout: float) -> bytes:
        try:
            if self.mode == "mock":
                with self._local_path(key).open("rb") as stream:
                    return stream.read(max_bytes + 1)
            with urlopen(
                Request(self._presign("GET", key, 120), method="GET"), timeout=timeout
            ) as stream:
                return stream.read(max_bytes + 1)
        except HTTPError as exc:
            exc.close()
            raise HTTPException(
                status_code=409 if exc.code == 404 else 502, detail="无法读取来源文件"
            ) from None
        except FileNotFoundError:
            raise HTTPException(status_code=409, detail="来源文件不存在") from None
        except (URLError, OSError, IncompleteRead):
            raise HTTPException(status_code=502, detail="无法读取来源文件") from None

    def create_read_url(self, key: str, expires_in: int) -> str:
        if self.mode == "mock":
            token = self._mock_token("GET", key, "", expires_in)
            return f"{self.public_base_url}/api/mock-storage/download/{token}"
        return self._presign("GET", key, expires_in)

    # Worker 将已生成的成图写入后端分配的不可覆盖对象键。
    def write_generated_image(self, key: str, data: bytes, content_type: str) -> None:
        try:
            if self.mode == "mock":
                path = self._local_path(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(data)
                path.with_suffix(path.suffix + ".meta.json").write_text(
                    json.dumps({"content_type": content_type}), encoding="utf-8"
                )
                return
            request = Request(
                self._presign("PUT", key, 120, content_type),
                data=data,
                method="PUT",
                headers={"Content-Type": content_type, "If-None-Match": "*"},
            )
            with urlopen(request, timeout=30):
                pass
        except (URLError, OSError) as exc:
            raise HTTPException(status_code=502, detail="无法保存生成图片") from exc

    def delete_object(self, key: str) -> None:
        if self.mode == "mock":
            path = self._local_path(key)
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".meta.json").unlink(missing_ok=True)
            return
        url = self._presign("DELETE", key, 120)
        try:
            with urlopen(Request(url, method="DELETE"), timeout=30):
                pass
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=502, detail="对象存储删除失败") from exc


def get_object_storage_service(request: FastAPIRequest) -> ObjectStorageService:
    """按请求 Host 生成前端可访问的 Mock 上传/下载 URL。"""
    settings = get_settings()
    base_url = settings.object_storage_mock_public_base_url or str(request.base_url).rstrip("/")
    return ObjectStorageService(public_base_url=base_url)
