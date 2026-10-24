"""Gemini Interactions API adapter for the provider-independent CLU boundary."""

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
from office_robot_nlp.batch_schema import (
    BATCH_TYPES, BUSY_POLICY_GUIDANCE, CLOSED_POLICY_GUIDANCE, TASK_PRIORITY_GUIDANCE, batch_properties,
)
from office_robot_nlp.domain.conversation import ConversationMode


class GeminiAdapterError(RuntimeError):
    """Base error for failures at the Gemini infrastructure boundary."""


class MissingGeminiAPIKeyError(GeminiAdapterError):
    """Raised when GEMINI_API_KEY is not configured."""


class InvalidGeminiConfigurationError(GeminiAdapterError):
    """Raised when Gemini configuration is present but invalid."""


class GeminiProviderRequestError(GeminiAdapterError):
    """Raised when Gemini cannot complete a provider request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_status: str | None = None,
        provider_code: str | int | None = None,
        provider_message: str | None = None,
    ):
        self.status_code = status_code
        self.provider_status = provider_status
        self.provider_code = provider_code
        self.provider_message = provider_message
        super().__init__(message)


class MalformedGeminiOutputError(GeminiAdapterError):
    """Raised when an Interactions response has no usable model text."""


class InvalidGeminiResultError(GeminiAdapterError):
    """Raised when model text fails the existing deterministic CLU decoder."""

    def __init__(self, errors: tuple[str, ...]):
        self.errors = errors
        super().__init__("Gemini output failed CLU validation: " + ", ".join(errors))


RequestFunction = Callable[[str, Mapping[str, str], bytes, float], object]


def _parsed_intent_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["DELIVER_DOCUMENT", "UNKNOWN"]},
            "destinationText": {"type": "string", "nullable": True},
            "documentText": {"type": "string", "nullable": True},
            "recipientText": {"type": "string", "nullable": True},
            "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"], "description": TASK_PRIORITY_GUIDANCE},
            "constraints": {
                "type": "object",
                "properties": {
                    "ifBusy": {
                        "type": "string",
                        "enum": ["WAIT", "ASK_USER"],
                        "nullable": True,
                        "description": BUSY_POLICY_GUIDANCE,
                    },
                    "ifClosed": {
                        "type": "string",
                        "enum": ["WAIT", "RETRY", "ASK_USER"],
                        "nullable": True,
                        "description": CLOSED_POLICY_GUIDANCE,
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


GEMINI_CLU_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "TASK_COMMAND",
                "CLARIFICATION_ANSWER",
                "ROBOT_DECISION",
                "UNKNOWN",
                "SOCIAL",
                "TASK_UPDATE",
                *BATCH_TYPES,
            ],
        },
        "parsedIntent": _parsed_intent_schema(),
        "recipientText": {"type": "string", "nullable": True},
        "destinationText": {"type": "string", "nullable": True},
        "documentText": {"type": "string", "nullable": True},
        "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"], "nullable": True},
        "constraints": {"type": "object"},
        "field": {"type": "string", "enum": ["destination", "document"]},
        "value": {"type": "string"},
        "itemIndex": {"type": "integer", "nullable": True},
        "decision": {"type": "string", "enum": ["WAIT", "RETRY", "CANCEL"]},
        "reason": {"type": "string", "nullable": True},
        **batch_properties(),
    },
    "required": ["type"],
}


def _semantic_context(context: ConversationContext) -> dict[str, object]:
    payload: dict[str, object] = {"state": context.mode.value}
    if context.mode is ConversationMode.AWAITING_TASK_INFORMATION:
        payload["awaitingField"] = context.awaitingField
    elif context.mode is ConversationMode.AWAITING_BATCH_INFORMATION:
        payload.update(batch_semantic_context(context))
    elif context.mode is ConversationMode.AWAITING_ROBOT_DECISION:
        payload["reason"] = context.decisionReason.value if context.decisionReason else None
        payload["allowedDecisions"] = [decision.value for decision in context.allowedDecisions]
    return payload


def _system_instruction(context: ConversationContext) -> str:
    context_json = json.dumps(_semantic_context(context), ensure_ascii=False, sort_keys=True)
    return f"{CLU_SYSTEM_INSTRUCTIONS}\n\nConversation context JSON:\n{context_json}"


def _provider_error_details(raw_body: bytes) -> tuple[str | None, str | int | None, str | None, str]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, None, None, "Gemini provider request failed"
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None, None, None, "Gemini provider request failed"
    status = error.get("status") if isinstance(error.get("status"), str) else None
    code = error.get("code") if isinstance(error.get("code"), (str, int)) else None
    provider_message = error.get("message") if isinstance(error.get("message"), str) else None
    details = ["Gemini provider request failed"]
    if status:
        details.append(status)
    if code is not None:
        details.append(f"code={code}")
    if provider_message:
        details.append(provider_message)
    return status, code, provider_message, "; ".join(details)


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
        status, code, provider_message, message = _provider_error_details(error.read())
        raise GeminiProviderRequestError(
            f"HTTP {error.code}: {message}",
            status_code=error.code,
            provider_status=status,
            provider_code=code,
            provider_message=provider_message,
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GeminiProviderRequestError("Gemini provider request failed") from error


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str = "gemini-3.8-flash"
    endpoint: str = "https://generativelanguage.googleapis.com/v1/interactions"
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise MissingGeminiAPIKeyError("GEMINI_API_KEY is required for the Gemini CLU adapter.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise InvalidGeminiConfigurationError("GEMINI_MODEL must be a non-empty string.")
        if not isinstance(self.endpoint, str) or not self.endpoint.strip():
            raise InvalidGeminiConfigurationError("Gemini endpoint must be a non-empty URL.")
        parsed_endpoint = urlsplit(self.endpoint)
        if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
            raise InvalidGeminiConfigurationError("Gemini endpoint must be an HTTP(S) URL.")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise InvalidGeminiConfigurationError("Gemini timeout must be a positive number.")
        if not isfinite(self.timeout) or self.timeout <= 0:
            raise InvalidGeminiConfigurationError("Gemini timeout must be a positive number.")

    @classmethod
    def from_environment(cls) -> "GeminiConfig":
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise MissingGeminiAPIKeyError(
                "GEMINI_API_KEY is required for the Gemini CLU adapter."
            )
        model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
        base_url = os.environ.get(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1"
        ).strip()
        if not model:
            raise InvalidGeminiConfigurationError("GEMINI_MODEL must be a non-empty string.")
        if not base_url:
            raise InvalidGeminiConfigurationError("GEMINI_BASE_URL must be a non-empty URL.")
        endpoint = base_url.rstrip("/") + "/interactions"
        try:
            timeout = float(os.environ.get("GEMINI_TIMEOUT", "30"))
        except ValueError as error:
            raise InvalidGeminiConfigurationError(
                "GEMINI_TIMEOUT must be a positive number."
            ) from error
        return cls(api_key=api_key, model=model, endpoint=endpoint, timeout=timeout)


class GeminiLanguageUnderstandingParser:
    """Concrete Gemini Interactions API implementation of the CLU parser."""

    def __init__(
        self,
        config: GeminiConfig | None = None,
        request: RequestFunction = _http_request,
    ):
        self._config = config or GeminiConfig.from_environment()
        self._request = request

    def parse(
        self,
        text: str,
        context: ConversationContext,
    ) -> LanguageUnderstandingResult:
        if not isinstance(text, str) or not text.strip():
            raise MalformedGeminiOutputError("User text must be a non-empty string.")
        body = json.dumps(
            {
                "model": self._config.model,
                "system_instruction": _system_instruction(context),
                "input": text,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": GEMINI_CLU_RESPONSE_SCHEMA,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "x-goog-api-key": self._config.api_key,
            "Content-Type": "application/json",
        }
        try:
            raw_response = self._request(
                self._config.endpoint, headers, body, self._config.timeout
            )
        except GeminiAdapterError:
            raise
        except Exception as error:
            raise GeminiProviderRequestError("Gemini provider request failed") from error
        content = self._extract_output_text(self._decode_response(raw_response))
        decoded = decode_language_understanding_result(content, context)
        if decoded.result is None:
            raise InvalidGeminiResultError(decoded.errors or ("INVALID_CLU_OUTPUT",))
        return decoded.result

    @staticmethod
    def _decode_response(raw_response: object) -> object:
        if isinstance(raw_response, bytes):
            try:
                return json.loads(raw_response.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise MalformedGeminiOutputError("Gemini response was not valid JSON.") from error
        if isinstance(raw_response, str):
            try:
                return json.loads(raw_response)
            except ValueError as error:
                raise MalformedGeminiOutputError("Gemini response was not valid JSON.") from error
        if isinstance(raw_response, dict):
            return raw_response
        raise MalformedGeminiOutputError("Gemini response must be a JSON object.")

    @staticmethod
    def _extract_output_text(response: object) -> str:
        if not isinstance(response, dict):
            raise MalformedGeminiOutputError("Gemini response must be a JSON object.")
        if response.get("status") in {"failed", "cancelled", "incomplete"}:
            raise MalformedGeminiOutputError("Gemini interaction did not complete.")
        steps = response.get("steps")
        if not isinstance(steps, list):
            raise MalformedGeminiOutputError("Gemini response did not contain steps.")
        text_blocks: list[str] = []
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            content = step.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    text_blocks.append(block["text"])
        if not text_blocks:
            raise MalformedGeminiOutputError("Gemini response contained no model output text.")
        return "".join(text_blocks)


__all__ = [
    "GeminiAdapterError",
    "GeminiConfig",
    "GeminiLanguageUnderstandingParser",
    "GeminiProviderRequestError",
    "GEMINI_CLU_RESPONSE_SCHEMA",
    "InvalidGeminiConfigurationError",
    "InvalidGeminiResultError",
    "MalformedGeminiOutputError",
    "MissingGeminiAPIKeyError",
]
