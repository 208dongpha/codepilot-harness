import asyncio
import logging

from google import genai
from google.genai import errors, types

from codepilot.config import Config
from codepilot.llm.base import Message, ProviderError

logger = logging.getLogger("codepilot")


class GeminiProvider:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client: genai.Client | None = None

    async def generate(self, messages: list[Message]) -> str:
        if not self.config.api_key:
            raise ProviderError("AUTHENTICATION_ERROR: Set GEMINI_API_KEY in environment or .env.")
        if self.config.llm.model == "YOUR_SUPPORTED_GEMINI_MODEL_ID":
            raise ProviderError("MODEL_ERROR: Configure a supported model in codepilot.toml.")
        try:
            if self.client is None:
                self.client = genai.Client(
                    api_key=self.config.api_key.get_secret_value(),
                    http_options=types.HttpOptions(
                        timeout=int(self.config.llm.timeout_seconds * 1000),
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                )
            contents = [
                types.Content(
                    role="model" if m["role"] == "assistant" else "user",
                    parts=[types.Part(text=m["content"])],
                )
                for m in messages
            ]
            async with asyncio.timeout(self.config.llm.timeout_seconds):
                response = await self.client.aio.models.generate_content(
                    model=self.config.llm.model,
                    contents=contents,
                    config=types.GenerateContentConfig(temperature=self.config.llm.temperature),
                )
            if not response.text or not response.text.strip():
                raise ProviderError("MODEL_ERROR: Provider returned no text.")
            return response.text.strip()
        except ProviderError:
            raise
        except errors.APIError as exc:
            code = {
                401: "AUTHENTICATION_ERROR",
                403: "AUTHENTICATION_ERROR",
                429: "RATE_LIMIT_ERROR",
                400: "MODEL_ERROR",
                404: "MODEL_ERROR",
                408: "TIMEOUT_ERROR",
                504: "TIMEOUT_ERROR",
            }.get(exc.code, "PROVIDER_ERROR")
            logger.warning("gemini_error %s", code)
            raise ProviderError(code) from None
        except TimeoutError:
            logger.warning("gemini_error TIMEOUT_ERROR")
            raise ProviderError("TIMEOUT_ERROR") from None
        except Exception:
            logger.error("gemini_error PROVIDER_ERROR")
            raise ProviderError("PROVIDER_ERROR") from None

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aio.aclose()
            self.client.close()
