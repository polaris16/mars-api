"""[新增] 管理整理稿段落与提取正文之间的位置对应。"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.schemas.memory_processing import DraftParagraphInput, TextRange
from app.services.narrative_organization import PreparedParagraph


@dataclass(frozen=True)
class DraftParagraph:
    id: UUID
    text: str
    source_ranges: tuple[TextRange, ...]
    owner_edited: bool = False


@dataclass(frozen=True)
class ParagraphReference:
    paragraph_id: UUID
    text_range: TextRange


def create_model_paragraphs(prepared: Sequence[PreparedParagraph]) -> list[DraftParagraph]:
    """给模型整理稿分配稳定的段落 ID。"""
    return [
        DraftParagraph(id=uuid4(), text=paragraph.text, source_ranges=paragraph.source_ranges)
        for paragraph in prepared
    ]


def apply_owner_edit(
    previous: Sequence[DraftParagraph], submitted: Sequence[DraftParagraphInput]
) -> list[DraftParagraph]:
    """沿用未改动的原文引用，使用人工来源标记新增或改写的段落。"""
    by_id = {paragraph.id: paragraph for paragraph in previous}
    result = []
    for paragraph in submitted:
        if paragraph.id is None:
            result.append(DraftParagraph(uuid4(), paragraph.text, (), owner_edited=True))
            continue
        original = by_id.get(paragraph.id)
        if original is None:
            raise ValueError("段落不属于提交的基准整理稿")
        if original.text == paragraph.text:
            result.append(original)
        else:
            result.append(DraftParagraph(original.id, paragraph.text, (), owner_edited=True))
    return result


@dataclass(frozen=True)
class ExtractionDraft:
    """以与模型输入一致的段间分隔符计算段内偏移。"""

    paragraphs: tuple[DraftParagraph, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(paragraph.text for paragraph in self.paragraphs)

    def references(self, span: TextRange) -> list[ParagraphReference]:
        if span.end_offset > len(self.text):
            raise ValueError("元素引用超出整理稿正文")
        references = []
        paragraph_start = 0
        for paragraph in self.paragraphs:
            paragraph_end = paragraph_start + len(paragraph.text)
            start = max(paragraph_start, span.start_offset)
            end = min(paragraph_end, span.end_offset)
            if start < end:
                references.append(
                    ParagraphReference(
                        paragraph_id=paragraph.id,
                        text_range=TextRange(
                            start_offset=start - paragraph_start, end_offset=end - paragraph_start
                        ),
                    )
                )
            paragraph_start = paragraph_end + 2
            if paragraph_start >= span.end_offset:
                break
        return references
