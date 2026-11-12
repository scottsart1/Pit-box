from pathlib import Path
import pytest
from pitwall import secret_storage as storage


class Vault:
    def __init__(self):
        self.values = {}
        self.fail = False

    def get(self, name):
        return self.values.get(name)

    def put(self, name, value):
        if self.fail:
            raise OSError("vault unavailable")
        self.values[name] = value


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(storage, "_store", None)
    for name in storage.NAMES:
        monkeypatch.delenv(name, raising=False)
    yield
    for name in storage.NAMES:
        monkeypatch.delenv(name, raising=False)


def test_migrates_all_provider_aliases_and_multiline_without_plaintext_backup(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# keep this\nOPENAI_API_KEY="first\nsecond"\nexport MOONSHOT_API_KEY=moon\nCUSTOM_LLM_API_KEY=custom\nPITWALL_WEB_PORT=9010\n')
    vault = Vault()
    storage.initialize(vault, env)
    assert vault.get("OPENAI_API_KEY") == "first\nsecond"
    assert vault.get("MOONSHOT_API_KEY") == "moon"
    assert env.read_text() == '# keep this\nPITWALL_WEB_PORT=9010\n'
    assert list(tmp_path.iterdir()) == [env]
    assert storage.persist("KIMI_API_KEY", "new")
    assert vault.get("MOONSHOT_API_KEY") is None
    assert vault.get("KIMI_API_KEY") == "new"
    assert storage.persist("KIMI_API_KEY", None)
    assert vault.get("KIMI_API_KEY") is None


def test_failed_migration_keeps_original_and_does_not_enable_text_fallback(tmp_path):
    env = tmp_path / ".env"
    original = 'OPENAI_API_KEY=private-value\nPITWALL_WEB_PORT=9010\n'
    env.write_text(original)
    vault = Vault(); vault.fail = True
    with pytest.raises(OSError):
        storage.initialize(vault, env)
    assert env.read_text() == original
    assert not storage.active()


def test_empty_env_still_restores_vault_on_next_launch(tmp_path, monkeypatch):
    vault = Vault(); vault.values["OPENAI_API_KEY"] = "encrypted-value"
    storage.initialize(vault, tmp_path / ".env")
    assert storage.active()
    assert __import__('os').environ['OPENAI_API_KEY'] == 'encrypted-value'
    vault.fail = True
    with pytest.raises(OSError):
        storage.persist("OPENAI_API_KEY", "new")
    assert not (tmp_path / ".env").exists()


def test_desktop_storage_remains_unchanged():
    assert not storage.persist("OPENAI_API_KEY", "value")
