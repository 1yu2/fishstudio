def test_system_environment_page_excludes_model_configuration():
    from app.routers.settings import ENV_GROUPS

    keys = {item["key"] for group in ENV_GROUPS for item in group["keys"]}
    forbidden_prefixes = ("OPENAI_", "VOLCANO_", "MINIMAX_", "LOCAL_H3_", "DASHSCOPE_", "TENCENT_AI3D_")
    assert not any(key.startswith(forbidden_prefixes) for key in keys)


def test_write_only_credential_never_returns_value(tmp_path, monkeypatch):
    from app.services import model_credential_service as credentials

    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=value\nLOCAL_H3_API_KEY=old\n", encoding="utf-8")
    monkeypatch.setattr(credentials, "ENV_FILE", env_file)

    result = credentials.write_env_credential("env:LOCAL_H3_API_KEY", "new-secret")

    assert result is None
    assert "LOCAL_H3_API_KEY=new-secret" in env_file.read_text(encoding="utf-8")


def test_write_only_credential_rejects_secret_manager_reference():
    import pytest

    from app.services.model_credential_service import write_env_credential

    with pytest.raises(ValueError, match="env:"):
        write_env_credential("secret:production/h3", "value")
