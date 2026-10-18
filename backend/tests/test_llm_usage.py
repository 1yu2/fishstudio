"""LLM Token 用量归一化与人民币计价。"""

from types import SimpleNamespace

from app.services.llm_usage_service import calculate_llm_cost, extract_llm_usage


def test_extract_usage_metadata_from_generation_message():
    message = SimpleNamespace(
        usage_metadata={
            "input_tokens": 1200,
            "output_tokens": 300,
            "input_token_details": {"cache_read": 200},
            "output_token_details": {"reasoning": 50},
        },
        response_metadata={},
    )
    response = SimpleNamespace(
        llm_output=None,
        generations=[[SimpleNamespace(message=message)]],
    )
    assert extract_llm_usage(response) == {
        "input_tokens": 1200,
        "cached_input_tokens": 200,
        "output_tokens": 300,
        "reasoning_tokens": 50,
    }


def test_extract_openai_token_usage():
    response = SimpleNamespace(
        llm_output={
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 10},
                "completion_tokens_details": {"reasoning_tokens": 5},
            }
        },
        generations=[],
    )
    usage = extract_llm_usage(response)
    assert usage is not None
    assert usage["input_tokens"] == 100
    assert usage["cached_input_tokens"] == 10
    assert usage["reasoning_tokens"] == 5


def test_missing_usage_is_explicit():
    response = SimpleNamespace(llm_output={}, generations=[])
    assert extract_llm_usage(response) is None


def test_calculate_cny_cost_with_cached_tokens(monkeypatch):
    monkeypatch.setenv("LLM_INPUT_PRICE_CNY_PER_MILLION", "10")
    monkeypatch.setenv("LLM_CACHED_INPUT_PRICE_CNY_PER_MILLION", "2")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_CNY_PER_MILLION", "20")
    cost, snapshot = calculate_llm_cost(
        {
            "input_tokens": 1_000_000,
            "cached_input_tokens": 200_000,
            "output_tokens": 100_000,
            "reasoning_tokens": 0,
        }
    )
    assert str(cost) == "10.400000"
    assert snapshot["cached_input_price_cny_per_million"] == "2"


def test_missing_price_does_not_become_zero(monkeypatch):
    monkeypatch.delenv("LLM_INPUT_PRICE_CNY_PER_MILLION", raising=False)
    monkeypatch.delenv("LLM_OUTPUT_PRICE_CNY_PER_MILLION", raising=False)
    cost, _ = calculate_llm_cost(
        {
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 10,
            "reasoning_tokens": 0,
        }
    )
    assert cost is None


def test_deployment_pricing_takes_precedence_over_environment(monkeypatch):
    from decimal import Decimal

    from app.services.llm_usage_service import calculate_llm_cost

    monkeypatch.setenv("LLM_INPUT_PRICE_CNY_PER_MILLION", "100")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_CNY_PER_MILLION", "100")
    usage = {
        "input_tokens": 1_000_000,
        "cached_input_tokens": 400_000,
        "output_tokens": 500_000,
        "reasoning_tokens": 0,
    }

    cost, snapshot = calculate_llm_cost(
        usage,
        {
            "input_per_million_tokens": "2",
            "cached_input_per_million_tokens": "0.5",
            "output_per_million_tokens": "8",
        },
    )
    # 600k 未缓存 × 2 + 400k 缓存 × 0.5 + 500k 输出 × 8
    assert cost == Decimal("5.400000")
    assert snapshot["price_source"] == "deployment"

    fallback, snapshot = calculate_llm_cost(usage, {"input_per_million_tokens": "2"})
    assert fallback == Decimal("150.000000")  # 价格不完整 → 回退环境变量
    assert snapshot["price_source"] == "environment"


def test_find_chat_deployment_matches_driver_and_model(monkeypatch):
    from types import SimpleNamespace

    from app.services import llm_usage_service

    rows = [
        SimpleNamespace(
            slug="deepseek-v3", driver="openai_compatible", model="deepseek-chat", modality="chat"
        ),
        SimpleNamespace(slug="kimi", driver="openai_compatible", model="kimi-k2", modality="chat"),
    ]
    snapshot = SimpleNamespace(list=lambda modality=None: rows)
    monkeypatch.setattr("app.model_runtime.catalog.get_catalog", lambda: SimpleNamespace(snapshot=snapshot))

    assert llm_usage_service.find_chat_deployment("openai_compatible", "kimi-k2").slug == "kimi"
    assert llm_usage_service.find_chat_deployment("openai_compatible", "unknown") is None
