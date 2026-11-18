"""The web app's access rules: who can see and change what."""

from fastapi.testclient import TestClient

from document_intelligence_rag import config, store, web


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
