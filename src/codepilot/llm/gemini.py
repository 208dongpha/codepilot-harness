import asyncio
import logging

from google import genai
from google.genai import errors, types

from codepilot.config import Config
from codepilot.llm.base import Message, ModelTurn, ProviderError, ToolCall

logger = logging.getLogger("codepilot")


class GeminiProvider:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client: genai.Client | None = None

    async def generate(self, messages: list[Message]) -> str:
        turn = await self.generate_turn(messages, [])
        if not turn.text:
            raise ProviderError("MODEL_ERROR: Provider returned no text.")
        return turn.text

    async def generate_turn(
        self, messages: list[Message], tools: list[dict[str, object]]
    ) -> ModelTurn:
        self._validate_configuration()
        try:
            client = self._client()
            contents = self._contents(messages)
            declarations = self._declarations(tools)
            config = types.GenerateContentConfig(
                temperature=self.config.llm.temperature,
                max_output_tokens=self.config.agent.reserved_output_tokens,
                tools=[types.Tool(function_declarations=declarations)] if declarations else None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            async with asyncio.timeout(self.config.llm.timeout_seconds):
                response = await client.aio.models.generate_content(
                    model=self.config.llm.model,
                    contents=contents,
                    config=config,
                )
            calls: list[ToolCall] = []
            for candidate in response.candidates or []:
                for index, part in enumerate(candidate.content.parts or []):
                    if part.function_call is None:
                        continue
                    call = part.function_call
                    calls.append(
                        ToolCall(
                            id=call.id or f"call-{index}",
                            name=call.name or "",
                            arguments=dict(call.args or {}),
                            thought_signature=part.thought_signature,
                        )
                    )
            text = response.text.strip() if response.text and response.text.strip() else None
            if not text and not calls:
                raise ProviderError("MODEL_ERROR: Provider returned no content.")
            return ModelTurn(text=text, tool_calls=calls)
        except ProviderError:
            raise
        except errors.APIError as exc:
            self._raise_api_error(exc)
        except TimeoutError:
            logger.warning("gemini_error TIMEOUT_ERROR")
            raise ProviderError("TIMEOUT_ERROR") from None
        except Exception:
            logger.error("gemini_error PROVIDER_ERROR")
            raise ProviderError("PROVIDER_ERROR") from None

    async def count_tokens(
        self, messages: list[Message], tools: list[dict[str, object]]
    ) -> int | None:
        self._validate_configuration()
        try:
            declarations = self._declarations(tools)
            config = types.CountTokensConfig(
                tools=[types.Tool(function_declarations=declarations)] if declarations else None
            )
            async with asyncio.timeout(self.config.llm.timeout_seconds):
                response = await self._client().aio.models.count_tokens(
                    model=self.config.llm.model,
                    contents=self._contents(messages),
                    config=config,
                )
            return response.total_tokens
        except Exception:
            logger.warning("gemini_token_count UNAVAILABLE")
            return None

    def _validate_configuration(self) -> None:
        if not self.config.api_key:
            raise ProviderError("AUTHENTICATION_ERROR: Set GEMINI_API_KEY in environment or .env.")
        if self.config.llm.model == "YOUR_SUPPORTED_GEMINI_MODEL_ID":
            raise ProviderError("MODEL_ERROR: Configure a supported model in codepilot.toml.")

    def _client(self) -> genai.Client:
        if self.client is None:
            self.client = genai.Client(
                api_key=self.config.api_key.get_secret_value() if self.config.api_key else "",
                http_options=types.HttpOptions(
                    timeout=int(self.config.llm.timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self.client

    def _contents(self, messages: list[Message]) -> list[types.Content]:
        contents = []
        for message in messages:
            if message["role"] == "tool":
                parts = [
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=message.get("call_id"),
                            name=message.get("name"),
                            response={"observation": message.get("content", "")},
                        )
                    )
                ]
                contents.append(types.Content(role="tool", parts=parts))
            elif message.get("tool_calls"):
                parts = [
                    types.Part(
                        function_call=types.FunctionCall(
                            id=call.id, name=call.name, args=call.arguments
                        ),
                        thought_signature=call.thought_signature,
                    )
                    for call in message["tool_calls"]
                ]
                contents.append(types.Content(role="model", parts=parts))
            else:
                contents.append(
                    types.Content(
                        role="model" if message["role"] == "assistant" else "user",
                        parts=[types.Part(text=message.get("content", ""))],
                    )
                )
        return contents

    def _declarations(
        self, declarations: list[dict[str, object]]
    ) -> list[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name=str(declaration["name"]),
                description=str(declaration.get("description", "")),
                parameters_json_schema=declaration.get("parameters"),
            )
            for declaration in declarations
        ]

    def _raise_api_error(self, exc: errors.APIError) -> None:
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

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aio.aclose()
            self.client.close()
