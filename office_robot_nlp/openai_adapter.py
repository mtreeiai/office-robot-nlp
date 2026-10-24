"""OpenAI-backed adapter for the provider-independent CLU boundary.

The adapter uses the OpenAI HTTP API through the Python standard library so the
domain and application layers do not depend on an SDK.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from office_robot_nlp.clu import (
    CLU_SYSTEM_INSTRUCTIONS,
    ConversationContext,
    LanguageUnderstandingResult,
    decode_language_understanding_result,
    batch_semantic_context,
)
from office_robot_nlp.batch_schema import batch_properties
from office_robot_nlp.domain.conversation import ConversationMode


class LLMAdapterError(RuntimeError):
    """Base error for failures at the provider boundary."""


class MissingAPIKeyError(LLMAdapterError):
    """Raised when the provider credential is not configured."""


class InvalidConfigurationError(LLMAdapterError):
    """Raised when provider configuration is present but invalid."""


class ProviderRequestError(LLMAdapterError):
    """Raised when the provider request cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str | int | None = None,
        provider_message: str | None = None,
    ):
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_message = provider_message
        super().__init__(message)


class OpenAITimeoutError(ProviderRequestError):
    """Raised when an OpenAI request exceeds the configured timeout."""


class MalformedProviderOutputError(LLMAdapterError):
    """Raised when the provider response is not a usable chat response."""


class InvalidProviderResultError(LLMAdapterError):
    """Raised when structured output fails the existing CLU decoder."""

    def __init__(self, errors: tuple[str, ...]):
        self.errors = errors
        super().__init__("Provider output failed CLU validation: " + ", ".join(errors))


RequestFunction = Callable[[str, Mapping[str, str], bytes, float], object]


def _parsed_intent_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {"type": "string", "enum": ["DELIVER_DOCUMENT", "UNKNOWN"]},
            "destinationText": {"type": ["string", "null"]},
            "documentText": {"type": ["string", "null"]},
            "recipientText": {"type": ["string", "null"]},
            "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"]},
            "constraints": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ifBusy": {"type": ["string", "null"], "enum": ["WAIT", "ASK_USER", None]},
                    "ifClosed": {
                        "type": ["string", "null"],
                        "enum": ["WAIT", "RETRY", "ASK_USER", None],
                    },
                },
                "required": ["ifBusy", "ifClosed"],
            },
            "missingFields": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["destination", "document", "priority", "busy_policy"],
                },
            },
        },
        "required": [
            "intent",
            "destinationText",
            "documentText",
            "recipientText",
            "priority",
            "constraints",
            "missingFields",
        ],
    }


def _result_branch(type_name: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"type": {"type": "string", "enum": [type_name]}, **properties},
        "required": ["type", *required],
    }


OPENAI_CLU_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "office_robot_language_understanding",
    "strict": True,
    "schema": {
        "oneOf": [
            _result_branch("BATCH_COMMAND", batch_properties(json_schema=True),
                           ["tasks", "orderPolicy", "allocationStatus"]),
            _result_branch("BATCH_ORDER", {"orderPolicy": batch_properties()["orderPolicy"]}, ["orderPolicy"]),
            _result_branch("BATCH_CONFIRM", {}, []),
            _result_branch("CANCEL_PENDING", {}, []),
            _result_branch(
                "TASK_COMMAND",
                {"parsedIntent": _parsed_intent_schema()},
                ["parsedIntent"],
            ),
            _result_branch(
                "CLARIFICATION_ANSWER",
                {
                    "field": {"type": "string", "enum": ["destination", "document"]},
                    "value": {"type": "string"},
                    "itemIndex": {"type": ["integer", "null"]},
                },
                ["field", "value", "itemIndex"],
            ),
            _result_branch(
                "ROBOT_DECISION",
                {"decision": {"type": "string", "enum": ["WAIT", "RETRY", "CANCEL"]}},
                ["decision"],
            ),
            _result_branch(
                "UNKNOWN",
                {"reason": {"type": ["string", "null"]}},
                ["reason"],
            ),
            _result_branch(
                "SOCIAL",
                {"reason": {"type": ["string", "null"]}},
                ["reason"],
            ),
            _result_branch(
                "TASK_UPDATE",
                {
                    "recipientText": {"type": ["string", "null"]},
                    "destinationText": {"type": ["string", "null"]},
                    "documentText": {"type": ["string", "null"]},
                    "priority": {"type": ["string", "null"], "enum": ["LOW", "NORMAL", "HIGH", None]},
                    "constraints": {"type": ["object", "null"]},
                    "itemIndex": {"type": ["integer", "null"]},
                },
                ["recipientText", "destinationText", "documentText", "priority", "constraints", "itemIndex"],
            ),
        ]
    },
}


def _http_request(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        provider_code = None
        provider_message = None
        try:
            payload = json.loads(error.read().decode("utf-8"))
            details = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(details, dict):
                provider_code = details.get("code") or details.get("type")
                provider_message = details.get("message")
        except (UnicodeDecodeError, ValueError):
            pass
        raise ProviderRequestError(
            f"HTTP {error.code}: OpenAI provider request failed",
            status_code=error.code,
            provider_code=provider_code,
            provider_message=provider_message,
        ) from error
    except TimeoutError as error:
        raise OpenAITimeoutError("OpenAI request timed out.") from error
    except (urllib.error.URLError, OSError) as error:
        raise ProviderRequestError("OpenAI request failed") from error


def _semantic_context(context: ConversationContext) -> dict[str, object]:
    """Send only the context needed to classify the current utterance."""
    payload: dict[str, object] = {"state": context.mode.value}
    if context.mode is ConversationMode.AWAITING_TASK_INFORMATION:
        payload["awaitingField"] = context.awaitingField
    elif context.mode is ConversationMode.AWAITING_BATCH_INFORMATION:
        payload.update(batch_semantic_context(context))
    elif context.mode is ConversationMode.AWAITING_ROBOT_DECISION:
        payload["reason"] = context.decisionReason.value if context.decisionReason else None
        payload["allowedDecisions"] = [decision.value for decision in context.allowedDecisions]
    return payload


def _system_message(context: ConversationContext) -> str:
    context_json = json.dumps(_semantic_context(context), ensure_ascii=False, sort_keys=True)
    return f"{CLU_SYSTEM_INSTRUCTIONS}\n\nConversation context JSON:\n{context_json}"


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str = "gpt-4o-mini"
    endpoint: str = "https://api.openai.com/v1/chat/completions"
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_key.strip():
            raise MissingAPIKeyError("OPENAI_API_KEY is required to use the OpenAI CLU adapter.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise InvalidConfigurationError("OPENAI_MODEL must be a non-empty string.")
        if not isinstance(self.endpoint, str) or not self.endpoint.strip():
            raise InvalidConfigurationError("OpenAI endpoint must be a non-empty URL.")
        parsed_endpoint = urlsplit(self.endpoint)
        if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
            raise InvalidConfigurationError("OpenAI endpoint must be an HTTP(S) URL.")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise InvalidConfigurationError("OpenAI timeout must be a positive number.")
        if not isfinite(self.timeout) or self.timeout <= 0:
            raise InvalidConfigurationError("OpenAI timeout must be a positive number.")

    @classmethod
    def from_environment(cls) -> "OpenAIConfig":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise MissingAPIKeyError(
                "OPENAI_API_KEY is required to use the OpenAI CLU adapter."
            )
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        if not model:
            raise InvalidConfigurationError("OPENAI_MODEL must be a non-empty string.")
        if not base_url:
            raise InvalidConfigurationError("OPENAI_BASE_URL must be a non-empty URL.")
        endpoint = base_url.rstrip("/") + "/chat/completions"
        try:
            timeout = float(os.environ.get("OPENAI_TIMEOUT", "30"))
        except ValueError as error:
            raise InvalidConfigurationError("OPENAI_TIMEOUT must be a positive number.") from error
        return cls(api_key=api_key, model=model, endpoint=endpoint, timeout=timeout)


class RealLLMLanguageUnderstandingParser:
    """Concrete OpenAI adapter implementing the existing CLU parser boundary."""

    def __init__(
        self,
        config: OpenAIConfig | None = None,
        request: RequestFunction = _http_request,
    ):
        self._config = config or OpenAIConfig.from_environment()
        self._request = request

    def parse(
        self,
        text: str,
        context: ConversationContext,
    ) -> LanguageUnderstandingResult:
        if not isinstance(text, str) or not text.strip():
            raise MalformedProviderOutputError("User text must be a non-empty string.")

        body = json.dumps(
            {
                "model": self._config.model,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": OPENAI_CLU_RESPONSE_SCHEMA,
                },
                "messages": [
                    {"role": "system", "content": _system_message(context)},
                    {"role": "user", "content": text},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            raw_response = self._request(
                self._config.endpoint, headers, body, self._config.timeout
            )
        except LLMAdapterError:
            raise
        except Exception as error:
            raise ProviderRequestError("OpenAI request failed") from error

        provider_payload = self._decode_response(raw_response)
        content = self._extract_content(provider_payload)
        decoded = decode_language_understanding_result(content, context)
        if decoded.result is None:
            raise InvalidProviderResultError(decoded.errors or ("INVALID_CLU_OUTPUT",))
        return decoded.result

    @staticmethod
    def _decode_response(raw_response: object) -> object:
        if isinstance(raw_response, bytes):
            try:
                return json.loads(raw_response.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise MalformedProviderOutputError("Provider response was not valid JSON.") from error
        if isinstance(raw_response, str):
            try:
                return json.loads(raw_response)
            except ValueError as error:
                raise MalformedProviderOutputError("Provider response was not valid JSON.") from error
        if isinstance(raw_response, dict):
            return raw_response
        raise MalformedProviderOutputError("Provider response must be JSON.")

    @staticmethod
    def _extract_content(provider_payload: object) -> str:
        if not isinstance(provider_payload, dict):
            raise MalformedProviderOutputError("Provider response must be a JSON object.")
        choices = provider_payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise MalformedProviderOutputError("Provider response did not contain choices.")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise MalformedProviderOutputError("Provider response did not contain message content.")
        return message["content"]


__all__ = [
    "InvalidProviderResultError",
    "InvalidConfigurationError",
    "LLMAdapterError",
    "MalformedProviderOutputError",
    "MissingAPIKeyError",
    "OpenAIConfig",
    "OpenAITimeoutError",
    "OPENAI_CLU_RESPONSE_SCHEMA",
    "ProviderRequestError",
    "RealLLMLanguageUnderstandingParser",
]
