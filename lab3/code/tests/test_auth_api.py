"""通过真实 HTTP 适配层验证密码、会话与输入边界。"""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.api.application import create_app

PASSWORD = "temporary-course-passphrase-2026"


def test_registration_does_not_expose_password_and_is_case_insensitive(api):
    body = {"username": "Alice", "name": "同名可以", "password": PASSWORD}
    response = api.client.post("/auth/register", json=body)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["username"] == "alice"
    assert set(response.json()) == {"id", "username", "name", "created_at"}
    assert PASSWORD not in response.text
    body["username"] = "alice"
    assert api.client.post("/auth/register", json=body).status_code == 409
    with api.app.state.store.database.connect(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        stored = connection.execute("SELECT password_hash FROM accounts").fetchone()[0]
        assert stored.startswith("$argon2id$") and stored != PASSWORD


def test_validation_error_never_echoes_password_or_raw_request(api):
    password = "secret-short"
    response = api.client.post("/auth/register", json={
        "username": "alice", "name": "用户", "password": password,
    })
    assert response.status_code == 422
    assert password not in response.text
    assert all("input" not in error for error in response.json()["detail"])
    response = api.client.post("/auth/register", json={
        "username": "alice", "name": "用户", "password": PASSWORD, "role": "owner",
    })
    assert response.status_code == 422 and PASSWORD not in response.text


def test_wrong_and_unknown_credentials_have_same_response(api, signup):
    signup("alice")
    wrong = api.client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    unknown = api.client.post("/auth/login", json={"username": "unknown", "password": "wrong"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "用户名或密码错误"}
    assert wrong.headers["www-authenticate"] == "Bearer"


def test_token_is_stored_as_digest_and_survives_restart_until_logout(api, signup):
    actor = signup("alice")
    with api.app.state.store.database.connect(write=False) as connection:
        digest = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert len(digest) == 64 and digest != actor.token
    restarted = create_app(api.settings, clock=lambda: api.clock.now)
    with TestClient(restarted) as client:
        profile = client.get("/auth/me", headers=actor.headers)
        assert profile.status_code == 200 and profile.headers["cache-control"] == "no-store"
        assert api.client.post("/auth/logout", headers=actor.headers).status_code == 204
        assert client.get("/auth/me", headers=actor.headers).status_code == 401
        assert client.get("/ledgers", headers=actor.headers).status_code == 401


def test_expired_session_cannot_read_or_create_ledgers(api, signup):
    actor = signup("alice")
    api.clock.now += timedelta(hours=8, seconds=1)
    assert api.client.get("/auth/me", headers=actor.headers).status_code == 401
    assert api.client.post("/ledgers", headers=actor.headers, json={"name": "不应创建"}).status_code == 401
    with api.app.state.store.database.connect(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM ledgers").fetchone()[0] == 0


def test_missing_token_and_query_parameter_identity_are_rejected(api):
    response = api.client.get("/ledgers", params={"user_id": "owner", "role": "owner"})
    assert response.status_code == 401
    assert api.client.get("/auth/me", headers={"Authorization": "Bearer fake"}).status_code == 401
    assert api.client.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_failed_login_counter_commits_and_lock_expires(api, signup, login):
    signup("alice")
    for _ in range(4):
        response = api.client.post("/auth/login", json={"username": "alice", "password": "bad"})
        assert response.status_code == 401
    response = api.client.post("/auth/login", json={"username": "alice", "password": "bad"})
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    assert api.client.post("/auth/login", json={
        "username": "alice", "password": PASSWORD,
    }).status_code == 429
    with api.app.state.store.transaction(write=False) as repositories:
        assert repositories.accounts.find("alice").failed_attempts == 5
    api.clock.now += timedelta(seconds=61)
    actor = login("alice")
    assert api.client.get("/auth/me", headers=actor.headers).status_code == 200
    with api.app.state.store.transaction(write=False) as repositories:
        account = repositories.accounts.find("alice")
        assert account.failed_attempts == 0 and account.locked_until is None


def test_logout_revokes_only_current_session(api, signup, login):
    first = signup("alice")
    second = login("alice")
    assert first.token != second.token
    assert api.client.post("/auth/logout", headers=first.headers).status_code == 204
    assert api.client.get("/auth/me", headers=first.headers).status_code == 401
    assert api.client.get("/auth/me", headers=second.headers).status_code == 200
