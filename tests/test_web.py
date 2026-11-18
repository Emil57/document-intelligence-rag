"""The web app's access rules: who can see and change what."""

from fastapi.testclient import TestClient

from conftest import FakeClient, reply, text
from document_intelligence_rag import config, spending, store, web


def sign_up(client: TestClient, username: str) -> TestClient:
    response = client.post("/api/signup", json={"username": username, "password": "password123"})
    assert response.status_code == 200, response.text
    return client


def second_browser() -> TestClient:
    """Another person, with their own cookies."""
    return TestClient(web.app)


def personal_library(client: TestClient) -> int:
    return client.get("/api/me").json()["libraries"][0]["id"]


def upload(client: TestClient, library_id: int, name: str, content: bytes):
    return client.post(f"/api/libraries/{library_id}/documents/upload", files={"file": (name, content)})


def test_api_needs_login(app_client):
    assert app_client.get("/api/me").status_code == 401


def test_login_with_wrong_password_fails(app_client):
    sign_up(app_client, "ana")
    response = second_browser().post("/api/login", json={"username": "ana", "password": "wrong-password"})
    assert response.status_code == 401


def test_outsiders_cannot_see_a_library(app_client):
    ana = sign_up(app_client, "ana")
    library_id = personal_library(ana)
    upload(ana, library_id, "secret.txt", b"top secret")

    ben = sign_up(second_browser(), "ben")
    assert ben.get(f"/api/libraries/{library_id}/documents").status_code == 404
    assert ben.post(f"/api/libraries/{library_id}/chat", json={"message": "hi"}).status_code == 404
    assert upload(ben, library_id, "x.txt", b"x").status_code == 404


def test_personal_libraries_cannot_be_shared(app_client):
    ana = sign_up(app_client, "ana")
    sign_up(second_browser(), "ben")
    response = ana.post(f"/api/libraries/{personal_library(ana)}/members", json={"username": "ben"})
    assert response.status_code == 400


def test_members_can_remove_only_their_own_documents(app_client):
    ana = sign_up(app_client, "ana")
    ben = sign_up(second_browser(), "ben")
    library_id = ana.post("/api/libraries", json={"name": "Team"}).json()["id"]
    assert ana.post(f"/api/libraries/{library_id}/members", json={"username": "ben"}).status_code == 200

    upload(ana, library_id, "plan.txt", b"the plan")
    upload(ben, library_id, "notes.txt", b"ben's notes")

    # Ben can read the library, but can't remove Ana's file.
    assert {d["source"] for d in ben.get(f"/api/libraries/{library_id}/documents").json()} == {"plan.txt", "notes.txt"}
    url = f"/api/libraries/{library_id}/documents"
    assert ben.delete(url, params={"source": "plan.txt"}).status_code == 403
    assert ben.delete(url, params={"source": "notes.txt"}).status_code == 200
    # The owner can remove anything.
    upload(ben, library_id, "more.txt", b"more")
    assert ana.delete(url, params={"source": "more.txt"}).status_code == 200


def test_only_the_owner_can_invite(app_client):
    ana = sign_up(app_client, "ana")
    ben = sign_up(second_browser(), "ben")
    sign_up(second_browser(), "cam")
    library_id = ana.post("/api/libraries", json={"name": "Team"}).json()["id"]
    ana.post(f"/api/libraries/{library_id}/members", json={"username": "ben"})
    response = ben.post(f"/api/libraries/{library_id}/members", json={"username": "cam"})
    assert response.status_code == 403


def test_rejected_upload_keeps_the_existing_file(app_client):
    ana = sign_up(app_client, "ana")
    library_id = personal_library(ana)
    assert upload(ana, library_id, "a.txt", b"first version").status_code == 200

    # Pretend a second upload with the same name passed the "already there"
    # check at the same moment as the first one.
    store.get_index(library_id).documents.pop("a.txt")
    assert upload(ana, library_id, "a.txt", b"second version").status_code == 400

    assert (config.FILES_DIR / str(library_id) / "a.txt").read_bytes() == b"first version"


def spent(username: str) -> float:
    with store.connect() as db:
        user_id = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
    return store.cost_since("", user_id)


def test_the_person_asking_pays_for_the_question(app_client, monkeypatch):
    ana = sign_up(app_client, "ana")
    ben = sign_up(second_browser(), "ben")
    library_id = ana.post("/api/libraries", json={"name": "Team"}).json()["id"]
    ana.post(f"/api/libraries/{library_id}/members", json={"username": "ben"})

    monkeypatch.setattr(web, "client", FakeClient(reply("end_turn", text("Hello!"))))
    assert ben.post(f"/api/libraries/{library_id}/chat", json={"message": "hi"}).status_code == 200

    assert spent("ben") > 0
    assert spent("ana") == 0


def test_image_descriptions_are_paid_by_the_uploader(app_client, monkeypatch):
    ana = sign_up(app_client, "ana")
    monkeypatch.setattr(web, "client", FakeClient(reply("end_turn", text("A photo of a cat."))))
    response = upload(ana, personal_library(ana), "cat.png", b"not really a png")
    assert response.status_code == 200, response.text
    assert spent("ana") > 0


def spend(username: str, dollars: float, when: str | None = None) -> None:
    """Pretend the user already spent this much (today, unless `when` is given)."""
    with store.connect() as db:
        user_id = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
        db.execute(
            """INSERT INTO usage (user_id, kind, model, input_tokens, output_tokens, cost_usd, created_at)
               VALUES (?, 'chat', 'claude-haiku-5-5', 0, 0, ?, ?)""",
            (user_id, dollars, when or store.now()),
        )


def test_users_over_their_daily_limit_are_stopped(app_client, monkeypatch):
    monkeypatch.setattr(config, "USER_DAILY_LIMIT_USD", 0.50)
    ana = sign_up(app_client, "ana")
    library_id = personal_library(ana)
    spend("ana", 0.50)

    fake = FakeClient(reply("end_turn", text("Hello!")))
    monkeypatch.setattr(web, "client", fake)
    response = ana.post(f"/api/libraries/{library_id}/chat", json={"message": "hi"})
    assert response.status_code == 429
    assert "today's limit" in response.json()["detail"]
    assert upload(ana, library_id, "cat.png", b"png").status_code == 429
    assert fake.requests == []  # Claude was never called

    # Free uploads still work.
    assert upload(ana, library_id, "notes.txt", b"notes").status_code == 200


def test_the_limit_is_per_user(app_client, monkeypatch):
    monkeypatch.setattr(config, "USER_DAILY_LIMIT_USD", 0.50)
    sign_up(app_client, "ana")
    ben = sign_up(second_browser(), "ben")
    spend("ana", 0.50)
    monkeypatch.setattr(web, "client", FakeClient(reply("end_turn", text("Hello!"))))
    assert ben.post(f"/api/libraries/{personal_library(ben)}/chat", json={"message": "hi"}).status_code == 200


def test_yesterdays_spending_doesnt_count(app_client, monkeypatch):
    monkeypatch.setattr(config, "USER_DAILY_LIMIT_USD", 0.50)
    ana = sign_up(app_client, "ana")
    spend("ana", 5.00, when="2000-01-01T12:00:00+00:00")
    assert spending.spent_today(1) == 0
    monkeypatch.setattr(web, "client", FakeClient(reply("end_turn", text("Hello!"))))
    assert ana.post(f"/api/libraries/{personal_library(ana)}/chat", json={"message": "hi"}).status_code == 200


def test_everyone_stops_when_the_app_limit_is_reached(app_client, monkeypatch):
    monkeypatch.setattr(config, "USER_DAILY_LIMIT_USD", 0.50)
    monkeypatch.setattr(config, "APP_DAILY_LIMIT_USD", 1.00)
    ana = sign_up(app_client, "ana")
    sign_up(second_browser(), "ben")
    sign_up(second_browser(), "cam")
    spend("ben", 0.50)
    spend("cam", 0.50)

    # Ana hasn't spent anything, but the app as a whole is at its limit.
    response = ana.post(f"/api/libraries/{personal_library(ana)}/chat", json={"message": "hi"})
    assert response.status_code == 429
    assert "app has reached" in response.json()["detail"]
