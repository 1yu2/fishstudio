"""create_llm 在数据库模型目录（slug 命名）和旧环境变量目录（<provider>.default）下都能选到对话模型。"""

import pytest

from app.llm import factory
from app.model_runtime.core import ChatDeployment, ModelRuntime


def _runtime(*names: str) -> ModelRuntime:
    runtime = ModelRuntime()
    for name in names:
        runtime.upsert_chat(
            ChatDeployment(name=name, provider="p", model=name, factory=lambda n=name: f"model:{n}")
        )
    return runtime


@pytest.fixture
def use_runtime(monkeypatch):
    monkeypatch.delenv("LLM_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    def install(runtime: ModelRuntime) -> None:
        monkeypatch.setattr(factory, "get_model_runtime", lambda: runtime)

    return install


def test_catalog_slug_names_are_routed_instead_of_pinning_the_legacy_name(use_runtime):
    use_runtime(_runtime("volcano-chat"))
    assert factory.create_llm(purpose="planner") == "model:volcano-chat"


def test_legacy_env_catalog_still_pins_the_provider_default(use_runtime):
    use_runtime(_runtime("volcano.default", "siliconflow.default"))
    assert factory.create_llm(purpose="planner") == "model:volcano.default"


def test_explicit_deployment_env_stays_strict(use_runtime, monkeypatch):
    use_runtime(_runtime("volcano-chat"))
    monkeypatch.setenv("LLM_DEPLOYMENT_NAME", "missing")
    with pytest.raises(ValueError, match="missing"):
        factory.create_llm()
