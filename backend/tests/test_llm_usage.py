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
