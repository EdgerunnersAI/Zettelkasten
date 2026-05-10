from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from website.api import chat_routes, sandbox_routes
from website.app import create_app
from website.features.rag_pipeline.types import AnswerTurn, Citation, QueryClass, SourceType


class _FakeSessions:
    def __init__(self):
        self._session_id = uuid4()
        self.session = {
            "id": str(self._session_id),
            "user_id": str(uuid4()),
            "sandbox_id": None,
            "title": "New conversation",
            "quality_mode": "fast",
            "message_count": 0,
            "last_scope_filter": {},
            "last_message_at": None,
            "created_at": None,
            "updated_at": None,
        }
        self.messages = []

    async def create_session(self, **kwargs):
        self.session["sandbox_id"] = str(kwargs.get("sandbox_id")) if kwargs.get("sandbox_id") else None
        self.session["quality_mode"] = kwargs.get("quality_mode", "fast")
        self.session["last_scope_filter"] = kwargs.get("initial_scope_filter") or {}
        return self._session_id

    async def get_session(self, session_id, user_id):
        del user_id
        return self.session if str(session_id) == self.session["id"] else None

    async def list_sessions(self, user_id, sandbox_id=None, limit=50):
        del user_id, limit
        if sandbox_id and self.session["sandbox_id"] != str(sandbox_id):
            return []
        return [self.session]

    async def list_messages(self, session_id, user_id, limit=100):
        del session_id, user_id, limit
        return list(self.messages)

    async def delete_session(self, session_id, user_id):
        del user_id
        return str(session_id) == self.session["id"]

    async def update_session(self, session_id, user_id, **kwargs):
        del user_id
        if str(session_id) != self.session["id"]:
            return None
        if "last_scope_filter" in kwargs and kwargs["last_scope_filter"] is not None:
            self.session["last_scope_filter"] = kwargs["last_scope_filter"]
        if "quality_mode" in kwargs and kwargs["quality_mode"] is not None:
            self.session["quality_mode"] = kwargs["quality_mode"]
        return self.session

    async def auto_title_session(self, session_id, user_id, first_query):
        del session_id, user_id
        self.session["title"] = first_query[:60]
        return self.session["title"]


class _FakeSandboxes:
    def __init__(self):
        self.sandbox_id = str(uuid4())
        self.rows = [
            {
                "id": self.sandbox_id,
                "name": "Transformer notes",
                "description": "Focused sandbox",
                "icon": "stack",
                "color": "#14b8a6",
                "default_quality": "fast",
                "member_count": 2,
                "last_used_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]
        # v2 shape (Phase 8.0 H9): rag.list_kasten_zettels returns flat
        # rows — workspace_zettel_id + canonical_zettel_id + title +
        # source_type + user_tags + ai_summary + added_at — no nested
        # ``kg_nodes`` embed.
        self.members = [
            {
                "workspace_zettel_id": "node-1",
                "canonical_zettel_id": "canonical-1",
                "title": "Attention Is All You Need",
                "source_type": "web",
                "user_tags": ["transformers"],
                "ai_summary": "Transformer primer",
                "added_at": None,
            }
        ]

    async def list_sandboxes(self, user_id, limit=50):
        del user_id, limit
        return list(self.rows)

    async def get_sandbox(self, sandbox_id, user_id):
        del user_id
        return self.rows[0] if str(sandbox_id) == self.sandbox_id else None

    async def create_sandbox(self, *, user_id, name, description=None, icon=None, color=None, default_quality="fast"):
        del user_id
        row = {
            "id": str(uuid4()),
            "name": name,
            "description": description or "",
            "icon": icon or "stack",
            "color": color or "#14b8a6",
            "default_quality": default_quality,
            "member_count": 0,
            "last_used_at": None,
            "created_at": None,
            "updated_at": None,
        }
        self.rows.append(row)
        return row

    async def update_sandbox(self, sandbox_id, user_id, **kwargs):
        del user_id
        if str(sandbox_id) != self.sandbox_id:
            return None
        self.rows[0].update({key: value for key, value in kwargs.items() if value is not None})
        return self.rows[0]

    async def delete_sandbox(self, sandbox_id, user_id):
        del user_id
        return str(sandbox_id) == self.sandbox_id

    async def list_members(self, sandbox_id, user_id, limit=500):
        del user_id, limit
        return list(self.members) if str(sandbox_id) == self.sandbox_id else []

    async def add_members(self, **kwargs):
        del kwargs
        return 1

    async def remove_member(self, sandbox_id, user_id, node_id):
        del user_id
        return str(sandbox_id) == self.sandbox_id and node_id == "node-1"

    async def remove_members(self, sandbox_id, user_id, node_ids):
        del user_id
        if str(sandbox_id) != self.sandbox_id:
            return 0
        removed = 0
        keep = []
        for member in self.members:
            # v2 shape uses workspace_zettel_id; fall back to v1 node_id for safety.
            mid = member.get("workspace_zettel_id") or member.get("node_id")
            if mid in node_ids:
                removed += 1
            else:
                keep.append(member)
        self.members = keep
        self.rows[0]["member_count"] = len(self.members)
        return removed

    async def touch_sandbox(self, sandbox_id, user_id):
        del sandbox_id, user_id
        return None


class _FakeRepo:
    class _Node:
        def __init__(self, node_id: str):
            self.id = node_id
            self.name = "Note " + node_id
            self.source_type = "web"
            self.summary = "Summary for " + node_id
            self.tags = ["retrieval"]
            self.url = "https://example.com/" + node_id
            self.node_date = "2026-04-12"

    def search_nodes(self, user_id, **kwargs):
        del user_id, kwargs
        return [self._Node("node-1"), self._Node("node-2")]


class _FakeOrchestrator:
    async def answer(self, *, query, user_id):
        del query, user_id
        return AnswerTurn(
            content="Grounded answer [node-1]",
            citations=[
                Citation(
                    id="node-1",
                    node_id="node-1",
                    title="Attention Is All You Need",
                    source_type=SourceType.WEB,
                    url="https://example.com/node-1",
                    snippet="Transformer primer",
                    rerank_score=0.92,
                )
            ],
            query_class=QueryClass.LOOKUP,
            critic_verdict="supported",
            llm_model="gemini-2.5-flash",
            retrieved_node_ids=["node-1"],
            retrieved_chunk_ids=[],
        )

    async def answer_stream(self, *, query, user_id):
        del query, user_id
        yield {"type": "status", "stage": "retrieving"}
        yield {"type": "citations", "citations": [{"node_id": "node-1", "title": "Attention Is All You Need"}]}
        yield {"type": "token", "content": "Grounded "}
        yield {"type": "token", "content": "answer"}
        yield {
            "type": "done",
            "turn": AnswerTurn(
                content="Grounded answer",
                citations=[],
                query_class=QueryClass.LOOKUP,
                critic_verdict="supported",
                llm_model="gemini-2.5-flash",
                retrieved_node_ids=["node-1"],
                retrieved_chunk_ids=[],
            ).model_dump(),
        }


class _FakeRuntime:
    def __init__(self):
        self.kg_user_id = uuid4()
        self.sessions = _FakeSessions()
        self.sandboxes = _FakeSandboxes()
        self.repo = _FakeRepo()
        self.orchestrator = _FakeOrchestrator()


def _client_with_runtime(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setattr(chat_routes, "get_rag_runtime", lambda user_sub: runtime)
    monkeypatch.setattr(sandbox_routes, "get_rag_runtime", lambda user_sub: runtime)

    app = create_app()
    user = {"sub": "user-1", "email": "user@example.com"}
    app.dependency_overrides[chat_routes.get_current_user] = lambda: user
    app.dependency_overrides[sandbox_routes.get_current_user] = lambda: user
    return TestClient(app), runtime


def test_sandbox_routes_smoke(monkeypatch):
    client, runtime = _client_with_runtime(monkeypatch)

    list_response = client.get("/api/rag/sandboxes")
    assert list_response.status_code == 200
    assert list_response.json()["sandboxes"][0]["name"] == "Transformer notes"

    create_response = client.post(
        "/api/rag/sandboxes",
        json={"name": "Eval notes", "description": "Testing sandbox", "default_quality": "high"},
    )
    assert create_response.status_code == 200
    assert create_response.json()["sandbox"]["name"] == "Eval notes"

    nodes_response = client.get("/api/rag/nodes")
    assert nodes_response.status_code == 200
    assert len(nodes_response.json()["nodes"]) == 2
    assert runtime.repo.search_nodes is not None

    members_response = client.get(f"/api/rag/sandboxes/{runtime.sandboxes.sandbox_id}/members")
    assert members_response.status_code == 200
    assert members_response.json()["members"][0]["node"]["name"] == "Attention Is All You Need"

    bulk_remove = client.request(
        "DELETE",
        f"/api/rag/sandboxes/{runtime.sandboxes.sandbox_id}/members",
        json={"tags": ["transformers"], "tag_mode": "all"},
    )
    assert bulk_remove.status_code == 200
    assert bulk_remove.json()["removed_count"] == 1
    assert bulk_remove.json()["members"] == []


def test_chat_routes_nonstream_and_stream(monkeypatch):
    client, runtime = _client_with_runtime(monkeypatch)

    create_session = client.post(
        "/api/rag/sessions",
        json={"title": "New conversation", "quality": "fast", "scope_filter": {}},
    )
    assert create_session.status_code == 200
    session_id = create_session.json()["session"]["id"]
    assert session_id == runtime.sessions.session["id"]

    nonstream = client.post(
        f"/api/rag/sessions/{session_id}/messages",
        json={"content": "What did I save about transformers?", "quality": "fast", "scope_filter": {}, "stream": False},
    )
    assert nonstream.status_code == 200
    assert nonstream.json()["turn"]["content"].startswith("Grounded answer")

    with client.stream(
        "POST",
        f"/api/rag/sessions/{session_id}/messages",
        json={"content": "Stream the answer", "quality": "fast", "scope_filter": {}, "stream": True},
    ) as response:
        assert response.status_code == 200
        body = "".join(chunk for chunk in response.iter_text())

    assert "event: status" in body
    assert '"type": "token"' in body
    assert '"type": "done"' in body


def test_user_rag_and_kastens_pages_smoke():
    client = TestClient(create_app())

    rag_response = client.get("/home/rag")
    assert rag_response.status_code == 200
    assert 'id="rag-status"' in rag_response.text
    assert 'aria-live="polite"' in rag_response.text

    kastens_response = client.get("/home/kastens")
    assert kastens_response.status_code == 200
    # /home/kastens was rebuilt as a View All page (shared header + stat row + card grid).
    # Smoke-test anchors reflect the new layout.
    assert 'id="kastens-grid"' in kastens_response.text
    assert 'id="create-kasten-btn"' in kastens_response.text
    assert 'aria-live="polite"' in kastens_response.text

