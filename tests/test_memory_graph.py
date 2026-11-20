"""[新增] 通过素材、核验和查询入口验证图谱读取与版本生命周期。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import MemoryElement, MemoryGraphEvent, MemoryGraphEventElement
from app.schemas.memory_extraction import EntityMention, StructuredElement
from app.services import memory_validation_service
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.text_extraction_model import TextExtractionModel
from test_memory_extraction import (
    OUTSIDER, OWNER, REVIEW_QUESTIONS, SESSION, VALIDATION_JOBS, VIEWER,
    _append_review_quote, _open_versioned_text, _revise_last_paragraph, _run_review,
    _seed_review_case, extraction_api,
)
from test_user_relationships import confirm_relationship

GRAPH = "/api/memory-graph"


def _timeline(client, headers=OWNER, **params):
    reply = client.get(f"{GRAPH}/timeline", headers=headers, params=params)
    assert reply.status_code == 200, reply.text
    return reply.json()


def _answer(client, action, **values):
    (question,) = client.get(REVIEW_QUESTIONS, headers=OWNER).json()["items"]
    reply = client.post(f"{REVIEW_QUESTIONS}/{question['id']}/answers", headers=OWNER, json={
        "expected_revision": question["revision"], "action": action, **values,
    })
    assert reply.status_code == 202, reply.text
    return reply.json()


def _choose_label(client, label):
    (question,) = client.get(REVIEW_QUESTIONS, headers=OWNER).json()["items"]
    option = next(item["id"] for item in question["options"] if item["label"] == label)
    return _answer(client, "confirm", selected_option_id=option)


def test_graph_queries_page_nodes_relationships_and_quotations(extraction_api, monkeypatch):
    client, factory = extraction_api
    first = "1983年我跟陈师傅在武汉学车床。"
    second = "1985年以后我搬进棉纺厂宿舍。"

    def extract(_model, content, _timeout, **_kwargs):
        if content == first:
            return [StructuredElement(
                element_type="event", source_quote=content, summary=content,
                time_start="1983-01-01", time_end="1983-12-31",
                entity_mentions=[
                    EntityMention(entity_type="person", mention_text="陈师傅", relation_type="participant",
                                  identity_quote="陈师傅在武汉学车床"),
                    EntityMention(entity_type="place", mention_text="武汉", relation_type="location",
                                  identity_quote="在武汉学车床"),
                ],
            )]
        return [StructuredElement(element_type="event", source_quote=content, summary=content)]

    monkeypatch.setattr(TextExtractionModel, "extract_structured", extract)
    _open_versioned_text(client, factory, first, narrator_user_id=1)
    _append_review_quote(client, factory, 11, second)
    assert _timeline(client)["total"] == 0
    _run_review(factory)
    whole = _timeline(client)
    assert whole["view_scope"] == "memoir" and whole["session_id"] is None
    left, right = whole["items"]
    assert left["time"]["start_date"] == "1983-01-01"
    assert right["time"] == {
        "start_date": "1986-01-01", "end_date": None, "exact_at": None,
        "precision": "open_range", "origin": "source",
    }
    assert _timeline(client, page=2, page_size=1)["items"] == [right]
    assert _timeline(client, from_date="1990-01-01")["items"] == [right]
    assert client.get(f"{GRAPH}/timeline", headers=OWNER, params={
        "from_date": "2000-01-01", "to_date": "1999-01-01",
    }).status_code == 422
    people = client.get(f"{GRAPH}/entities", headers=OWNER, params={"entity_type": "person", "keyword": "陈师傅"})
    assert people.status_code == 200, people.text
    (person,) = people.json()["items"]
    assert person["related_event_count"] == 1
    assert _timeline(client, entity_node_id=person["node_id"])["items"] == [left]
    detail = client.get(f"{GRAPH}/nodes/{left['node_id']}", headers=OWNER)
    assert detail.status_code == 200, detail.text
    assert detail.json()["node"]["places"][0]["display_name"] == "武汉"
    entity = client.get(f"{GRAPH}/nodes/{person['node_id']}", headers=OWNER).json()["node"]
    assert entity["aliases"] == ["陈师傅"]
    neighborhood = client.get(f"{GRAPH}/neighborhood", headers=OWNER, params={"node_id": left["node_id"], "depth": 1})
    assert neighborhood.status_code == 200, neighborhood.text
    assert {edge["relation_type"] for edge in neighborhood.json()["edges"]} == {"before", "participant", "location"}
    before = {"from_node_id": left["node_id"], "to_node_id": right["node_id"], "relation_type": "before"}
    evidence = client.get(f"{GRAPH}/evidence", headers=OWNER, params=before)
    assert evidence.status_code == 200, evidence.text
    assert {item["excerpt"] for item in evidence.json()["items"]} == {first, second}
    assert all(item["draft_references"][0]["source_references"] for item in evidence.json()["items"])
    node_proofs = client.get(f"{GRAPH}/evidence", headers=OWNER, params={"node_id": left["node_id"], "page_size": 1})
    assert node_proofs.json()["total"] == left["evidence_count"]
    assert len(node_proofs.json()["items"]) == 1
    assert client.get(f"{GRAPH}/evidence", headers=OWNER, params={"node_id": left["node_id"], **before}).status_code == 422
    assert client.get(f"{GRAPH}/timeline").status_code == 401


def test_graph_adopts_owner_correction_and_keeps_shared_session_local(extraction_api, monkeypatch):
    client, factory = extraction_api
    _seed_review_case(client, factory, monkeypatch, context="我搬家了。")
    _run_review(factory)
    _answer(client, "confirm", selected_option_id="same-event")
    _run_review(factory)
    correction = _answer(client, "correct", answer_text="搬家发生在1984年。")
    _run_review(factory)
    _choose_label(client, "1984 年")
    _run_review(factory)
    (event,) = _timeline(client)["items"]
    assert event["time"]["start_date"] == "1984-01-01"
    assert event["time"]["origin"] == "review_answer"
    evidence = client.get(f"{GRAPH}/evidence", headers=OWNER, params={"node_id": event["node_id"]}).json()
    assert correction["answer_id"] in {item["answer_id"] for item in evidence["items"]}
    assert any("1983年" in (item["excerpt"] or "") for item in evidence["items"])

    confirm_relationship(client, OWNER, VIEWER, user_id=2, kind="family", code="sibling_of")
    assert client.patch(SESSION, headers=OWNER, json={"visibility": "public"}).status_code == 200
    shared = _timeline(client, VIEWER, session_id=11)
    assert shared["view_scope"] == "session"
    (local,) = shared["items"]
    assert local["node_id"].startswith("element:") and local["time"]["start_date"] == "1983-01-01"
    assert local["time"]["origin"] == "source"
    shared_proofs = client.get(f"{GRAPH}/evidence", headers=VIEWER, params={"session_id": 11, "node_id": local["node_id"]}).json()
    assert all(item["answer_id"] is None and item["session_id"] == 11 for item in shared_proofs["items"])
    assert client.get(f"{GRAPH}/nodes/{event['node_id']}", headers=VIEWER, params={"session_id": 11}).status_code == 404
    assert client.get(f"{GRAPH}/timeline", headers=OUTSIDER, params={"session_id": 11}).status_code == 404
    assert client.get(f"{GRAPH}/timeline", headers=VIEWER, params={"session_id": 12}).status_code == 404
    assert client.patch(SESSION, headers=OWNER, json={"visibility": "secret"}).status_code == 200
    assert client.get(f"{GRAPH}/nodes/{local['node_id']}", headers=VIEWER, params={"session_id": 11}).status_code == 404


def test_graph_reuses_event_id_and_filters_superseded_sources_before_worker(extraction_api, monkeypatch):
    client, factory = extraction_api
    quote = "1983年我开始在棉纺厂学修机器。"

    def extract(*_args, **_kwargs):
        return [StructuredElement(element_type="event", source_quote=quote, summary=quote)]

    monkeypatch.setattr(TextExtractionModel, "extract_structured", extract)
    path, draft = _open_versioned_text(client, factory, f"{quote}\n后来我报了夜校。", narrator_user_id=1)
    _run_review(factory)
    (original,) = _timeline(client)["items"]
    revised = _revise_last_paragraph(client, path, draft, "后来我去了夜校读书。")
    assert _timeline(client)["total"] == 0
    with factory() as db:
        assert MemoryExtractionService().run(db)
    _run_review(factory)
    (current,) = _timeline(client)["items"]
    assert current["node_id"] == original["node_id"]
    proof = client.get(f"{GRAPH}/evidence", headers=OWNER, params={"node_id": current["node_id"]}).json()
    assert {item["draft_version_id"] for item in proof["items"]} == {revised["id"]}
    assert client.post(VALIDATION_JOBS, headers=OWNER, json={"action": "rebuild"}).status_code == 202
    _run_review(factory)
    with factory() as db:
        assert len(list(db.scalars(select(MemoryGraphEvent)))) == 1
        assert [row.status for row in db.scalars(select(MemoryGraphEventElement).order_by(MemoryGraphEventElement.id))] == [
            "superseded", "active",
        ]
    assert client.delete(path, headers=OWNER).status_code == 200
    assert client.get(f"{GRAPH}/nodes/{current['node_id']}", headers=OWNER).status_code == 404


def test_graph_merges_and_restores_stable_anchors_on_split(extraction_api, monkeypatch):
    client, factory = extraction_api
    first = "我和姐姐把父亲留下的缝纫机搬进棉纺厂宿舍。"
    second = first.replace("姐姐", "姐姐一起")
    _seed_review_case(client, factory, monkeypatch, context=(first, second), years=(1983, 1983))
    _run_review(factory)
    initial = _timeline(client)["items"]
    assert len(initial) == 2
    fragment = _append_review_quote(client, factory, 12, f"1985年{first}")
    assert _timeline(client)["total"] == 0
    _run_review(factory)
    _answer(client, "confirm", selected_option_id="same-event")
    _run_review(factory)
    _choose_label(client, "1983 年")
    _run_review(factory)
    (merged,) = _timeline(client)["items"]
    assert merged["node_id"] == initial[0]["node_id"]
    with factory() as db:
        members = list(db.scalars(select(MemoryGraphEventElement).where(MemoryGraphEventElement.status == "active")))
        assert len(members) == 2 and {item.membership_basis for item in members} == {"confirmed_same_event"}
    assert client.delete(f"/api/recording-sessions/12/fragments/{fragment}", headers=OWNER).status_code == 200
    assert _timeline(client)["total"] == 0
    _run_review(factory)
    assert [item["node_id"] for item in _timeline(client)["items"]] == [item["node_id"] for item in initial]


def test_graph_lease_expiry_rolls_back_publication_and_verification(extraction_api, monkeypatch):
    client, factory = extraction_api
    _seed_review_case(client, factory, monkeypatch, context="我搬进棉纺厂宿舍。", years=(1983,))
    publish = memory_validation_service.publish_graph

    def expire_at_commit(db, job, records):
        publish(db, job, records)
        job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    monkeypatch.setattr(memory_validation_service, "publish_graph", expire_at_commit)
    _run_review(factory)
    assert _timeline(client)["total"] == 0
    with factory() as db:
        assert db.scalar(select(MemoryGraphEvent.id)) is None
        assert db.scalar(select(MemoryGraphEventElement.id)) is None
        assert db.scalar(select(MemoryElement.verification_status)) == "pending"


def test_session_graph_keeps_same_named_mentions_and_undated_events_separate(extraction_api, monkeypatch):
    client, factory = extraction_api
    first = "1983年我在纺织厂跟陈师傅学修机器。"
    second = "后来我到夜校听陈师傅讲国画。"

    def extract(_model, content, _timeout, **_kwargs):
        return [StructuredElement(
            element_type="event", source_quote=content, summary=content,
            entity_mentions=[EntityMention(
                entity_type="person", mention_text="陈师傅", relation_type="participant",
                identity_quote=content,
            )],
        )]

    monkeypatch.setattr(TextExtractionModel, "extract_structured", extract)
    _open_versioned_text(client, factory, first, narrator_user_id=1)
    _append_review_quote(client, factory, 11, second)
    entities = client.get(f"{GRAPH}/entities", headers=OWNER, params={"session_id": 11})
    assert entities.status_code == 200, entities.text
    people = entities.json()["items"]
    assert len(people) == 2 and people[0]["node_id"] != people[1]["node_id"]
    assert {node["display_name"] for node in people} == {"陈师傅"}
    assert all(node["node_id"].startswith("mention:") and node["related_event_count"] == 1 for node in people)
    known, unknown = _timeline(client, session_id=11)["items"]
    assert unknown["time"]["precision"] == "unknown"
    assert _timeline(client, session_id=11, from_date="1980-01-01")["items"] == [known]
    assert _timeline(client, session_id=11, from_date="1980-01-01", include_undated=True)["items"] == [known, unknown]
