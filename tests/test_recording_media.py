"""PostgreSQL 集成测试：覆盖 Mock 上传及录音素材业务闭环。

测试会保留数据库数据和 .local-media 文件，供测试后检查。
"""

import base64
import hashlib
import json
import os
import struct
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.main import app
from app.models import Topic

DEMO_TOPICS = [
    (
        "ui_demo_technology_discovery",
        "科技发现",
        "cpu",
        "科技数码",
        10,
        1200,
        "聊聊最近惊艳你的前沿科技或数码产品。",
        "最近有什么新科技让您觉得特别有意思？",
    ),
    (
        "ui_demo_relax_and_vent",
        "解压吐槽",
        "smile",
        "生活",
        20,
        856,
        "把积压的烦恼一吐为快，听听大家的生活经验。",
        "最近有什么烦心事，想找个人聊聊？",
    ),
    (
        "ui_demo_deep_reading",
        "深度阅读",
        "book-open",
        "阅读",
        30,
        640,
        "分享一本曾触动你心灵或改变观念的书。",
        "有没有一本书让您至今记得很深？",
    ),
    (
        "ui_demo_life_moments",
        "生活碎片",
        "sun",
        "生活",
        40,
        1500,
        "记录街角日落、热气腾腾的咖啡等日常瞬间。",
        "最近生活里有没有一个小瞬间让您觉得很舒服？",
    ),
    (
        "ui_demo_favorite_song",
        "单曲循环",
        "headphones",
        "音乐",
        50,
        2100,
        "分享最近反复播放的旋律与背后的故事。",
        "有没有一首歌，您最近总想再听一遍？",
    ),
    (
        "ui_demo_work_memories",
        "工作回忆",
        "briefcase",
        "职场",
        60,
        930,
        "聊聊职场蜕变、团队共事或那些难忘的瞬间。",
        "您工作经历中最难忘的一位同事或一件事是什么？",
    ),
]


def seed_ui_topics() -> dict[str, int]:
    """幂等写入 UI 演示话题；讨论人数使用截图中的虚拟展示数值。"""
    rows = [
        {
            "code": code,
            "title": title,
            "icon_key": icon,
            "category": category,
            "sort_order": sort_order,
            "participant_count": participant_count,
            "summary": summary,
            "prompt": prompt,
            "is_general": True,
            "is_enabled": True,
            "is_del": False,
            "life_stage": None,
        }
        for code, title, icon, category, sort_order, participant_count, summary, prompt in DEMO_TOPICS
    ]
    statement = insert(Topic).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="t_topic_code_key",
        set_={
            key: getattr(statement.excluded, key)
            for key in (
                "title",
                "icon_key",
                "category",
                "sort_order",
                "participant_count",
                "summary",
                "prompt",
                "is_general",
                "is_enabled",
                "is_del",
            )
        },
    )
    with SessionLocal.begin() as session:
        session.execute(statement)
        topics = session.scalars(
            select(Topic).where(Topic.code.in_([row["code"] for row in rows]))
        ).all()
        return {topic.code: topic.id for topic in topics}


def pcm_wav(sample: int) -> bytes:
    data = bytes([sample])
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def upload_asset(
    client,
    headers,
    *,
    purpose,
    media_type,
    filename,
    content_type,
    content,
    session_id=None,
    fragment_id=None,
):
    request_body = {
        "purpose": purpose,
        "media_type": media_type,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "session_id": session_id,
        "fragment_id": fragment_id,
    }
    auth = client.post(
        "/api/media-assets/upload-authorizations", headers=headers, json=request_body
    )
    assert auth.status_code in (200, 201), auth.text
    result = auth.json()
    if not result["reused"]:
        put = client.put(result["upload_url"], content=content, headers=result["required_headers"])
        assert put.status_code == 200, put.text
    complete = client.post(
        f"/api/media-assets/{result['asset_id']}/complete",
        headers=headers,
        json={
            "duration_ms": 1 if media_type == "audio" else None,
            "sample_rate_hz": 8000 if media_type == "audio" else None,
        },
    )
    assert complete.status_code == 200, complete.text
    return complete.json()


def test_recording_media_real_database_business_flow(monkeypatch):
    """按话题→登录→会话→上传→片段→附件→读取/更新/软删除顺序验证。"""
    if os.getenv("RUN_RECORDING_MEDIA_INTEGRATION") != "1":
        pytest.skip("设置 RUN_RECORDING_MEDIA_INTEGRATION=1 才会写入配置的 PostgreSQL 数据库")
    # 集成流程固定使用本地 Mock 文件存储，避免测试触发真实 OSS。
    monkeypatch.setattr(get_settings(), "object_storage_mode", "mock")
    topic_ids = seed_ui_topics()
    assert len(topic_ids) == len(DEMO_TOPICS)
    with SessionLocal() as session:
        assert session.get_bind().dialect.name == "postgresql"

    phone = f"test-{uuid.uuid4().hex[:12]}"
    title_prefix = f"录音接口联调-{uuid.uuid4().hex[:8]}"
    with TestClient(app) as client:
        assert client.get("/api/recording-sessions").status_code == 401
        login = client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        assert login.status_code == 200, login.text
        user_id = login.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        recommended = client.get(
            "/api/topics/recommended", headers=headers, params={"page": 1, "page_size": 20}
        )
        assert recommended.status_code == 200, recommended.text
        assert {item["title"] for item in recommended.json()["items"]} >= {
            row[1] for row in DEMO_TOPICS
        }

        created_session = client.post(
            "/api/recording-sessions",
            headers=headers,
            json={
                "topic_id": topic_ids["ui_demo_technology_discovery"],
                "title": f"{title_prefix}-主会话",
            },
        )
        assert created_session.status_code == 201, created_session.text
        session_id = created_session.json()["id"]

        wav_one = pcm_wav(128)
        audio_one = upload_asset(
            client,
            headers,
            purpose="recording",
            media_type="audio",
            filename="第一段口述.wav",
            content_type="audio/wav",
            content=wav_one,
            session_id=session_id,
        )
        assert audio_one["upload_status"] == "uploaded"
        assert audio_one["size_bytes"] == len(wav_one)
        played_audio = client.get(audio_one["download_url"])
        assert played_audio.status_code == 200
        assert played_audio.content == wav_one

        created_fragment = client.post(
            f"/api/recording-sessions/{session_id}/fragments",
            headers=headers,
            json={"audio_asset_id": audio_one["id"], "transcript_raw": "今天想聊聊以前的工作。"},
        )
        assert created_fragment.status_code == 201, created_fragment.text
        fragment_id = created_fragment.json()["id"]
        assert created_fragment.json()["sequence_no"] == 1

        updated_fragment = client.patch(
            f"/api/recording-sessions/{session_id}/fragments/{fragment_id}",
            headers=headers,
            json={"transcript_edited": "今天我想聊聊以前的工作经历。"},
        )
        assert updated_fragment.status_code == 200, updated_fragment.text
        assert updated_fragment.json()["transcript_raw"] == "今天想聊聊以前的工作。"
        assert updated_fragment.json()["transcript_edited"] == "今天我想聊聊以前的工作经历。"

        duplicate = client.post(
            "/api/media-assets/upload-authorizations",
            headers=headers,
            json={
                "purpose": "recording",
                "media_type": "audio",
                "filename": "重复录音.wav",
                "content_type": "audio/wav",
                "size_bytes": len(wav_one),
                "sha256": hashlib.sha256(wav_one).hexdigest(),
                "session_id": session_id,
            },
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["reused"] is True
        assert duplicate.json()["asset_id"] == audio_one["id"]

        image_one_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWQAAAABJRU5ErkJggg=="
        )
        image_two_bytes = b"\x89PNG\r\n\x1a\nMock UI test image two"
        image_one = upload_asset(
            client,
            headers,
            purpose="attachment",
            media_type="image",
            filename="老照片.png",
            content_type="image/png",
            content=image_one_bytes,
            fragment_id=fragment_id,
        )
        image_two = upload_asset(
            client,
            headers,
            purpose="attachment",
            media_type="image",
            filename="旧车票.png",
            content_type="image/png",
            content=image_two_bytes,
            fragment_id=fragment_id,
        )
        created_attachments = client.post(
            f"/api/memory-fragments/{fragment_id}/attachments",
            headers=headers,
            json={
                "items": [
                    {"asset_id": image_one["id"], "caption": "老照片", "sort_order": 1},
                    {"asset_id": image_two["id"], "caption": "旧车票", "sort_order": 2},
                ]
            },
        )
        assert created_attachments.status_code == 201, created_attachments.text
        attachment_one_id, attachment_two_id = [item["id"] for item in created_attachments.json()]
        assert (
            client.delete(f"/api/media-assets/{image_one['id']}", headers=headers).status_code
            == 409
        )

        attachments = client.get(
            f"/api/memory-fragments/{fragment_id}/attachments", headers=headers
        )
        assert attachments.status_code == 200
        assert len(attachments.json()) == 2
        played_image = client.get(attachments.json()[0]["asset"]["download_url"])
        assert played_image.status_code == 200

        updated_attachment = client.patch(
            f"/api/memory-fragments/{fragment_id}/attachments/{attachment_one_id}",
            headers=headers,
            json={"caption": "家里的老照片", "sort_order": 0},
        )
        assert updated_attachment.status_code == 200, updated_attachment.text
        assert updated_attachment.json()["caption"] == "家里的老照片"

        removed_attachment = client.delete(
            f"/api/memory-fragments/{fragment_id}/attachments/{attachment_two_id}",
            headers=headers,
        )
        assert removed_attachment.status_code == 200
        assert (
            client.delete(f"/api/media-assets/{image_two['id']}", headers=headers).status_code
            == 200
        )
        assert (
            client.get(f"/api/media-assets/{image_one['id']}", headers=headers).status_code == 200
        )

        fragments = client.get(f"/api/recording-sessions/{session_id}/fragments", headers=headers)
        assert fragments.status_code == 200
        assert fragments.json()["total"] == 1
        assert (
            client.get(
                f"/api/recording-sessions/{session_id}/fragments/{fragment_id}", headers=headers
            ).status_code
            == 200
        )
        media_page = client.get(
            "/api/media-assets", headers=headers, params={"page": 1, "page_size": 100}
        )
        assert media_page.status_code == 200
        assert {row["id"] for row in media_page.json()["items"]} >= {
            audio_one["id"],
            image_one["id"],
        }

        session_list = client.get("/api/recording-sessions", headers=headers)
        assert session_list.status_code == 200
        assert any(row["id"] == session_id for row in session_list.json()["items"])
        detail = client.get(f"/api/recording-sessions/{session_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["fragments"][0]["attachments"][0]["caption"] == "家里的老照片"
        updated_session = client.patch(
            f"/api/recording-sessions/{session_id}",
            headers=headers,
            json={"title": f"{title_prefix}-已更新", "is_favorite": True},
        )
        assert updated_session.status_code == 200, updated_session.text
        assert updated_session.json()["is_favorite"] is True

        # 辅助记录覆盖软删除路径，主会话及主片段保留供后续检查。
        auxiliary_session = client.post(
            "/api/recording-sessions",
            headers=headers,
            json={"title": f"{title_prefix}-待删除会话"},
        )
        assert auxiliary_session.status_code == 201, auxiliary_session.text
        auxiliary_session_id = auxiliary_session.json()["id"]
        direct_text = client.post(
            f"/api/recording-sessions/{auxiliary_session_id}/fragments",
            headers=headers,
            json={"source_type": "text", "content_text": "A directly entered memory."},
        )
        assert direct_text.status_code == 201, direct_text.text
        assert direct_text.json()["source_type"] == "text"
        assert direct_text.json()["content_text"] == "A directly entered memory."
        assert direct_text.json()["contributor_id"] > 0

        text_bytes = "A memory imported from a text file.".encode("utf-8")
        text_asset = upload_asset(
            client,
            headers,
            purpose="source",
            media_type="text",
            filename="memory.txt",
            content_type="text/plain; charset=utf-8",
            content=text_bytes,
            session_id=auxiliary_session_id,
        )
        imported_text = client.post(
            f"/api/recording-sessions/{auxiliary_session_id}/fragments",
            headers=headers,
            json={"source_type": "text", "source_asset_id": text_asset["id"]},
        )
        assert imported_text.status_code == 201, imported_text.text
        assert imported_text.json()["source_asset"]["id"] == text_asset["id"]

        assert (
            client.delete(
                f"/api/recording-sessions/{auxiliary_session_id}/fragments/{direct_text.json()['id']}",
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.delete(
                f"/api/recording-sessions/{auxiliary_session_id}/fragments/{imported_text.json()['id']}",
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.delete(f"/api/media-assets/{text_asset['id']}", headers=headers).status_code
            == 200
        )

        wav_two = pcm_wav(129)
        audio_two = upload_asset(
            client,
            headers,
            purpose="recording",
            media_type="audio",
            filename="第二段口述.wav",
            content_type="audio/wav",
            content=wav_two,
            session_id=session_id,
        )
        auxiliary_fragment = client.post(
            f"/api/recording-sessions/{session_id}/fragments",
            headers=headers,
            json={"audio_asset_id": audio_two["id"], "transcript_raw": "这段用于验证软删除。"},
        )
        assert auxiliary_fragment.status_code == 201, auxiliary_fragment.text
        auxiliary_fragment_id = auxiliary_fragment.json()["id"]
        assert auxiliary_fragment.json()["sequence_no"] == 2
        assert (
            client.delete(
                f"/api/recording-sessions/{session_id}/fragments/{auxiliary_fragment_id}",
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.delete(f"/api/media-assets/{audio_two['id']}", headers=headers).status_code
            == 200
        )
        assert (
            client.delete(
                f"/api/recording-sessions/{auxiliary_session_id}", headers=headers
            ).status_code
            == 200
        )

    print(
        json.dumps(
            {
                "seeded_topic_ids": topic_ids,
                "test_user_id": user_id,
                "active_session_id": session_id,
                "deleted_session_id": auxiliary_session_id,
                "active_fragment_id": fragment_id,
                "soft_deleted_fragment_id": auxiliary_fragment_id,
                "active_asset_ids": [audio_one["id"], image_one["id"]],
                "soft_deleted_asset_ids": [image_two["id"], audio_two["id"]],
            },
            ensure_ascii=False,
        )
    )
