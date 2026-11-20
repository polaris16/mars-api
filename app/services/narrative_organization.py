"""[新增] 整理原始叙事，并核验整理稿引用的位置。"""

import re
from dataclasses import dataclass

from app.schemas.memory_processing import (
    MAX_DRAFT_PARAGRAPHS,
    AudioRange,
    OrganizedNarrative,
    TextRange,
    TranscriptSegment,
)
from app.services.text_extraction_model import ExtractionError, TextExtractionModel


ORGANIZATION_INSTRUCTIONS = """你负责整理用户口述或手写的回忆。source_text 是待整理的资料。
只去掉无意义的语气词、口语重复，修正明显的转写错误并按语义分段。
保持叙述者、事件、人物、时间、地点和不确定性。不得补写原文没有的事实。
只返回 JSON 对象 {"paragraphs":[{"text":"整理后的段落","source_quotes":["原文引句"]}]}。
每段至少包含一条原文中的逐字引句，合起来覆盖段落主要事实。
若原文重复出现同一句话，请引用一段更长且能唯一定位该处的原文。保持原叙述顺序。"""


@dataclass(frozen=True)
class PreparedParagraph:
    text: str
    source_ranges: tuple[TextRange, ...]


def validate_transcript_segments(
    source_text: str, segments: list[TranscriptSegment]
) -> None:
    """拒绝与原文不符或顺序倒退的转写区间。"""
    previous_end = 0
    previous_start_ms = 0
    for segment in segments:
        if not previous_end <= segment.start_offset < segment.end_offset <= len(source_text):
            raise ValueError("转写区间超出原文或与前一个区间重叠")
        if segment.start_ms < previous_start_ms:
            raise ValueError("转写的原声时间必须按起始时间排序")
        previous_end = segment.end_offset
        previous_start_ms = segment.start_ms


def audio_ranges_for_text(
    text_ranges: tuple[TextRange, ...], segments: list[TranscriptSegment]
) -> list[AudioRange]:
    """使用实际 ASR 片段取回原声区间，保留不连续的间隔。"""
    matches = [
        (segment.start_ms, segment.end_ms)
        for segment in segments
        if any(
            span.start_offset < segment.end_offset and segment.start_offset < span.end_offset
            for span in text_ranges
        )
    ]
    merged = []
    for start, end in sorted(matches):
        if merged and start <= merged[-1].end_ms:
            previous = merged.pop()
            start, end = previous.start_ms, max(previous.end_ms, end)
        merged.append(AudioRange(start_ms=start, end_ms=end))
    return merged


def _quote_range(source_text: str, quote: str) -> TextRange:
    start = source_text.find(quote)
    if not quote.strip() or start < 0:
        raise ExtractionError("ORGANIZATION_EVIDENCE_INVALID")
    if source_text.find(quote, start + 1) >= 0:
        raise ExtractionError("ORGANIZATION_EVIDENCE_AMBIGUOUS")
    return TextRange(start_offset=start, end_offset=start + len(quote))


def _mock_organization(source_text: str) -> list[PreparedParagraph]:
    # Mock 沿用原文。重复句按实际出现位置分别记录。
    paragraphs = []
    for match in re.finditer(r"[^\r\n]+", source_text):
        source = match.group()
        cleaned = source.strip()
        if not cleaned:
            continue
        start = match.start() + len(source) - len(source.lstrip())
        if len(paragraphs) == MAX_DRAFT_PARAGRAPHS - 1:
            cleaned = source_text[start:].rstrip()
        span = TextRange(start_offset=start, end_offset=start + len(cleaned))
        paragraphs.append(PreparedParagraph(cleaned, (span,)))
        if len(paragraphs) == MAX_DRAFT_PARAGRAPHS:
            break
    return paragraphs


def organize_narrative(
    model: TextExtractionModel, source_text: str, timeout: float
) -> list[PreparedParagraph]:
    """整理并核验输入，外部模型授权由任务调用方检查。"""
    if not source_text.strip() or "\x00" in source_text:
        raise ExtractionError("SOURCE_TEXT_INVALID")
    limit = model.settings.ai_text_max_input_chars
    if len(source_text) > limit:
        raise ExtractionError("SOURCE_TEXT_TOO_LONG")
    if not model.available:
        raise ExtractionError("ORGANIZATION_UNAVAILABLE")
    if not model.external:
        return _mock_organization(source_text)

    content = model.complete_json(ORGANIZATION_INSTRUCTIONS, {"source_text": source_text}, timeout)
    try:
        result = OrganizedNarrative.model_validate_json(content)
    except ValueError:
        raise ExtractionError("ORGANIZATION_OUTPUT_INVALID") from None
    paragraphs = []
    for paragraph in result.paragraphs:
        if not paragraph.text.strip() or "\x00" in paragraph.text:
            raise ExtractionError("ORGANIZATION_OUTPUT_INVALID")
        spans = tuple(
            dict.fromkeys(_quote_range(source_text, quote) for quote in paragraph.source_quotes)
        )
        paragraphs.append(PreparedParagraph(paragraph.text, spans))
    if sum(len(paragraph.text) for paragraph in paragraphs) > limit:
        raise ExtractionError("ORGANIZATION_OUTPUT_TOO_LONG")
    return paragraphs
