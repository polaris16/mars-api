"""[新增] 已确认音频的 Mock 和兼容 ASR 适配器。"""

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from uuid import uuid4

from app.core.config import Settings
from app.schemas.memory_processing import MAX_SOURCE_CHARS, TranscriptSegment
from app.services.text_extraction_model import ExtractionError, NoRedirect


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptSegment, ...] = ()


def _align_segments(text: str, segments: object) -> tuple[TranscriptSegment, ...]:
    """只采用顺序上可逐字对齐的供应商分段时间，不从正文估算时长。"""
    if not isinstance(segments, list):
        return ()
    cursor = last_start_ms = 0
    aligned = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            break
        quote = segment["text"].strip()
        if not quote:
            continue
        # 转写全文和分段之间可忽略空白，避免错配后续重复的引句。
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        offset = cursor
        if not text.startswith(quote, offset):
            break
        cursor += len(quote)
        start, end = segment.get("start"), segment.get("end")
        if (
            type(start) not in {float, int} or type(end) not in {float, int}
            or not 0 <= start < end <= (2**31 - 1) / 1000
        ):
            continue
        start_ms, end_ms = round(start * 1000), round(end * 1000)
        if start_ms < last_start_ms or start_ms >= end_ms:
            continue
        aligned.append(TranscriptSegment(
            start_offset=offset, end_offset=cursor, start_ms=start_ms, end_ms=end_ms,
        ))
        last_start_ms = start_ms
    return tuple(aligned)


class AudioTranscriptionModel:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def external(self) -> bool:
        return self.settings.ai_asr_provider != "mock"

    @property
    def available(self) -> bool:
        if not self.settings.ai_asr_enabled:
            return False
        if not self.external:
            return True
        try:
            url = urlsplit(self.settings.ai_asr_base_url)
        except ValueError:
            return False
        return (
            self.settings.ai_asr_provider == "openai-compatible"
            and url.scheme == "https" and bool(url.hostname)
            and not (url.username or url.password or url.query or url.fragment)
            and bool(self.settings.ai_asr_model and self.settings.ai_asr_api_key.get_secret_value())
        )

    def transcribe(self, audio: bytes, content_type: str, timeout: float) -> TranscriptionResult:
        if not self.available:
            raise ExtractionError("ASR_UNAVAILABLE")
        if not self.external:
            text = "我参加工作以后，跟着陈师傅学手艺。"
            segments = _align_segments(text, [{"text": text, "start": 0, "end": 2}])
            return TranscriptionResult(text, segments if self.settings.ai_asr_timestamps_enabled else ())

        boundary = uuid4().hex
        # 使用服务端构造的文件名，用户上传的原始文件名不进入 multipart 头。
        extensions = {
            "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
            "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a",
            "audio/webm": "webm", "audio/ogg": "ogg", "audio/flac": "flac",
        }
        suffix = extensions.get(content_type)
        if suffix is None:
            raise ExtractionError("ASR_FILE_TYPE_UNSUPPORTED")
        fields = {"model": self.settings.ai_asr_model}
        if self.settings.ai_asr_timestamps_enabled:
            fields.update({"response_format": "verbose_json", "timestamp_granularities[]": "segment"})
        parts = "".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            for name, value in fields.items()
        )
        body = (
            parts + f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="recording.{suffix}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'
        ).encode() + audio + f"\r\n--{boundary}--\r\n".encode()
        request = Request(
            f"{self.settings.ai_asr_base_url.rstrip('/')}/audio/transcriptions",
            data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.ai_asr_api_key.get_secret_value()}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with build_opener(NoRedirect).open(request, timeout=timeout) as response:
                raw = response.read(512 * 1024 + 1)
        except HTTPError as exc:
            exc.close()
            retryable = exc.code in {408, 429} or exc.code >= 500
            raise ExtractionError("ASR_REQUEST_FAILED", retryable=retryable) from None
        except (URLError, OSError):
            raise ExtractionError("ASR_UNREACHABLE", retryable=True) from None
        if len(raw) > 512 * 1024:
            raise ExtractionError("ASR_OUTPUT_INVALID")
        try:
            payload = json.loads(raw)
            result = payload["text"]
        except (ValueError, TypeError, KeyError):
            raise ExtractionError("ASR_OUTPUT_INVALID") from None
        if (
            not isinstance(result, str) or not result.strip()
            or "\x00" in result or len(result) > MAX_SOURCE_CHARS
        ):
            raise ExtractionError("ASR_OUTPUT_INVALID")
        segments = (
            _align_segments(result, payload.get("segments"))
            if self.settings.ai_asr_timestamps_enabled else ()
        )
        return TranscriptionResult(result, segments)
