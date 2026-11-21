"""验证真实 SQLite 就绪检查及失败时的 HTTP 行为。"""

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.config import Settings
from app.storage.database import Database
from app.storage.schema import TABLES


def test_health_creates_database_in_selected_directory(tmp_path):
    settings = Settings(data_dir=tmp_path / "isolated-data")
    assert not settings.database_path.exists()
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ready"
    assert settings.database_path.is_file()
    with Database(settings.database_path).connect(write=False) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert TABLES <= names


def test_health_reports_unavailable_storage(tmp_path):
    occupied = tmp_path / "file-instead-of-directory"
    occupied.write_text("existing file", encoding="utf-8")
    with TestClient(create_app(Settings(data_dir=occupied))) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["detail"] == "数据存储暂时不可用"


def test_health_rejects_unsupported_schema(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with Database(settings.database_path).connect() as connection:
        connection.execute("PRAGMA user_version = 99")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 503
