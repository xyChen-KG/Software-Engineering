"""验证可配置部署地址及明确的配置错误。"""

import pytest

from app.config import Settings


@pytest.mark.parametrize("port", ["abc", "0", "65536"])
def test_invalid_server_port_is_rejected(monkeypatch, port):
    monkeypatch.setenv("LEDGER_API_PORT", port)
    with pytest.raises(ValueError, match="LEDGER_API_PORT"):
        Settings.from_env()


@pytest.mark.parametrize("url", ["file:///tmp/db", "http://", "http://host:0", "http://host/api"])
def test_invalid_client_address_is_rejected(monkeypatch, url):
    monkeypatch.setenv("LEDGER_API_URL", url)
    with pytest.raises(ValueError, match="LEDGER_API_URL"):
        Settings.from_env()


def test_environment_controls_client_and_database(monkeypatch, tmp_path):
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEDGER_API_PORT", "8123")
    monkeypatch.setenv("LEDGER_API_URL", "http://127.0.0.1:8123/")
    settings = Settings.from_env()
    assert settings.database_path == tmp_path / "ledger.sqlite3"
    assert settings.api_port == 8123
    assert settings.api_url == "http://127.0.0.1:8123"
