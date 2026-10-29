from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from google.genai import errors
from pydantic import SecretStr

from codepilot.config import Config, LLMConfig
from codepilot.llm.base import ProviderError
from codepilot.llm.gemini import GeminiProvider


@pytest.fixture
def client():
    with patch("codepilot.llm.gemini.genai.Client") as factory:
        client = factory.return_value
        client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(text=" hello "))
        client.aio.aclose = AsyncMock()
        yield client


async def test_success_and_configured_model(client):
    provider = GeminiProvider(
        Config(api_key=SecretStr("fixture"), llm=LLMConfig(model="gemini-test"))
    )
    assert await provider.generate([{"role": "user", "content": "Hi"}]) == "hello"
    assert client.aio.models.generate_content.call_args.kwargs["model"] == "gemini-test"
    await provider.close()
    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()


@pytest.mark.parametrize(
    "error,code",
    [
        (errors.APIError(401, {}), "AUTHENTICATION_ERROR"),
        (errors.APIError(403, {}), "AUTHENTICATION_ERROR"),
        (errors.APIError(429, {}), "RATE_LIMIT_ERROR"),
        (errors.APIError(404, {}), "MODEL_ERROR"),
        (errors.APIError(500, {}), "PROVIDER_ERROR"),
        (TimeoutError("secret"), "TIMEOUT_ERROR"),
        (RuntimeError("secret"), "PROVIDER_ERROR"),
    ],
)
async def test_errors(client, error, code, caplog):
    client.aio.models.generate_content.side_effect = error
    provider = GeminiProvider(
        Config(api_key=SecretStr("secret"), llm=LLMConfig(model="gemini-test"))
    )
    with pytest.raises(ProviderError, match=code) as caught:
        await provider.generate([])
    assert "secret" not in str(caught.value) + caplog.text
    assert client.aio.models.generate_content.await_count == 1


async def test_missing_key():
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        await GeminiProvider(Config()).generate([])


async def test_empty_response(client):
    client.aio.models.generate_content.return_value.text = None
    with pytest.raises(ProviderError, match="MODEL_ERROR"):
        await GeminiProvider(
            Config(api_key=SecretStr("fixture"), llm=LLMConfig(model="gemini-test"))
        ).generate([])


async def test_placeholder_model():
    with pytest.raises(ProviderError, match="Configure a supported model"):
        await GeminiProvider(Config(api_key=SecretStr("fixture"))).generate([])
