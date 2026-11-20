"""覆盖整理稿请求、原文及音频区间、模型回包校验。"""

import json

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.memory_processing import (
    CandidateResolutionCreate,
    DraftParagraphInput,
    DraftVersionCreate,
    TextRange,
    TranscriptSegment,
)
from app.services.draft_provenance import (
    ExtractionDraft,
    apply_owner_edit,
    create_model_paragraphs,
)
from app.services.narrative_organization import (
    audio_ranges_for_text,
    organize_narrative,
    validate_transcript_segments,
)
from app.services.text_extraction_model import ExtractionError, TextExtractionModel


def test_mock_organization_locates_repeated_paragraphs_by_unicode_offsets():
    settings = get_settings().model_copy(
        update={"ai_text_enabled": True, "ai_text_provider": "mock"}
    )
    source = "  我在上海工作。\r\n\n  我在上海工作。🌱\n"
    paragraphs = organize_narrative(TextExtractionModel(settings), source, timeout=1)
    assert [paragraph.text for paragraph in paragraphs] == ["我在上海工作。", "我在上海工作。🌱"]
    for paragraph in paragraphs:
        (location,) = paragraph.source_ranges
        assert source[location.start_offset : location.end_offset] == paragraph.text
    assert paragraphs[0].source_ranges[0].start_offset == 2
    assert paragraphs[1].source_ranges[0].start_offset > paragraphs[0].source_ranges[0].end_offset


def test_source_reference_keeps_separate_audio_intervals():
    segments = [
        TranscriptSegment(start_offset=0, end_offset=4, start_ms=0, end_ms=400),
        TranscriptSegment(start_offset=4, end_offset=7, start_ms=420, end_ms=700),
        TranscriptSegment(start_offset=7, end_offset=10, start_ms=900, end_ms=1600),
    ]
    validate_transcript_segments("今天下雨。后来放晴。", segments)
    ranges = audio_ranges_for_text(
        (TextRange(start_offset=0, end_offset=3), TextRange(start_offset=8, end_offset=10)),
        segments,
    )
    assert [(item.start_ms, item.end_ms) for item in ranges] == [(0, 400), (900, 1600)]

    with pytest.raises(ValueError, match="超出原文"):
        validate_transcript_segments("原文过短", segments)
    with pytest.raises(ValidationError):
        TranscriptSegment(start_offset=1, end_offset=3, start_ms=100, end_ms=99)
    with pytest.raises(ValidationError):
        TranscriptSegment(start_offset=3, end_offset=2, start_ms=1, end_ms=99)


@pytest.mark.parametrize(
    ("quotes", "error"),
    [
        (["没有出现在原文的引句"], "ORGANIZATION_EVIDENCE_INVALID"),
        (["那天去了工厂。"], "ORGANIZATION_EVIDENCE_AMBIGUOUS"),
        (["星期二，那天去了工厂。", "晚饭在车间吃。"], None),
    ],
)
def test_external_organization_checks_all_quotes(monkeypatch, quotes, error):
    settings = get_settings().model_copy(update={"ai_text_enabled": True})
    model = TextExtractionModel(settings)
    monkeypatch.setattr(TextExtractionModel, "available", property(lambda _: True))
    monkeypatch.setattr(TextExtractionModel, "external", property(lambda _: True))
    source = "星期一，那天去了工厂。星期二，那天去了工厂。晚饭在车间吃。"

    def complete(instructions, inputs, timeout):
        assert inputs == {"source_text": source}
        return json.dumps(
            {"paragraphs": [{"text": "星期二去工厂，晚饭在车间吃。", "source_quotes": quotes}]}
        )

    monkeypatch.setattr(model, "complete_json", complete)
    if error:
        with pytest.raises(ExtractionError, match=error):
            organize_narrative(model, source, timeout=1)
        return

    (paragraph,) = organize_narrative(model, source, timeout=1)
    assert [source[span.start_offset : span.end_offset] for span in paragraph.source_ranges] == quotes


def test_draft_and_candidate_requests_validate_version_and_action():
    text = {"text": "回忆正文"}
    draft = DraftVersionCreate(source_revision_id=1, base_version_id=None, paragraphs=[text])
    assert draft.paragraphs[0].id is None
    with pytest.raises(ValidationError):
        DraftVersionCreate(source_revision_id=True, base_version_id=None, paragraphs=[text])
    with pytest.raises(ValidationError):
        DraftVersionCreate(
            source_revision_id=1, base_version_id=None, paragraphs=[{"text": "\u0000"}]
        )
    with pytest.raises(ValidationError):
        CandidateResolutionCreate(expected_revision=1, action="reject_match", entity_id=None)
    request = CandidateResolutionCreate(
        expected_revision=1, action="create_new", canonical_name=" 陈师傅 "
    )
    assert request.canonical_name == "陈师傅"


def test_edit_carries_only_unchanged_source_mappings_and_keeps_paragraph_ids():
    settings = get_settings().model_copy(
        update={"ai_text_enabled": True, "ai_text_provider": "mock"}
    )
    prepared = organize_narrative(
        TextExtractionModel(settings), "我在上海读书。\n我后来到北京工作。", timeout=1
    )
    first, second = create_model_paragraphs(prepared)
    revised = apply_owner_edit(
        [first, second],
        [
            DraftParagraphInput(id=first.id, text=first.text),
            DraftParagraphInput(id=second.id, text="我后来到南京工作。"),
            DraftParagraphInput(text="那时我刚结婚。"),
        ],
    )
    assert revised[0] == first
    assert revised[1].id == second.id
    assert revised[1].source_ranges == () and revised[1].owner_edited
    assert revised[2].owner_edited

    draft = ExtractionDraft(tuple(revised))
    assert draft.text == "我在上海读书。\n\n我后来到南京工作。\n\n那时我刚结婚。"
    locations = draft.references(
        TextRange(start_offset=len(first.text) - 2, end_offset=len(first.text) + 4)
    )
    assert [(location.paragraph_id, location.text_range) for location in locations] == [
        (first.id, TextRange(start_offset=len(first.text) - 2, end_offset=len(first.text))),
        (second.id, TextRange(start_offset=0, end_offset=2)),
    ]
    with pytest.raises(ValueError, match="基准整理稿"):
        apply_owner_edit([first], [DraftParagraphInput(id=second.id, text=second.text)])
