"""Ollama local adapter for the provider-independent CLU boundary."""

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
)
from office_robot_nlp.domain.conversation import ConversationMode


class OllamaAdapterError(RuntimeError):
    """Base error for the Ollama infrastructure boundary."""


class OllamaConnectionError(OllamaAdapterError):
    """Raised when the local Ollama server cannot be reached."""


class OllamaTimeoutError(OllamaConnectionError):
    """Raised when an Ollama request exceeds the configured timeout."""


class OllamaModelNotFoundError(OllamaConnectionError):
    """Raised when the configured model is not installed locally."""


class InvalidOllamaConfigurationError(OllamaAdapterError):
    """Raised when Ollama configuration is invalid."""


class MalformedOllamaOutputError(OllamaAdapterError):
    """Raised when Ollama returns no usable model output."""


class InvalidOllamaResultError(OllamaAdapterError):
    """Raised when model output fails the existing deterministic CLU decoder."""

    def __init__(self, errors: tuple[str, ...]):
        self.errors = errors
        super().__init__("Ollama output failed CLU validation: " + ", ".join(errors))


RequestFunction = Callable[[str, Mapping[str, str], bytes, float], object]
HealthRequestFunction = Callable[[str, float], object]


def _parsed_intent_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["DELIVER_DOCUMENT", "UNKNOWN"]},
            "destinationText": {"type": "string", "nullable": True},
            "documentText": {"type": "string", "nullable": True},
            "recipientText": {"type": "string", "nullable": True},
            "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"]},
            "constraints": {
                "type": "object",
                "properties": {
                    "ifBusy": {"type": "string", "enum": ["WAIT", "ASK_USER"], "nullable": True},
                    "ifClosed": {
                        "type": "string",
                        "enum": ["WAIT", "RETRY", "ASK_USER"],
                        "nullable": True,
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
            "intent", "destinationText", "documentText", "recipientText", "priority", "constraints", "missingFields",
        ],
    }


OLLAMA_CLU_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["TASK_COMMAND", "TASK_UPDATE", "CLARIFICATION_ANSWER", "ROBOT_DECISION", "SOCIAL", "UNKNOWN"],
        },
        "parsedIntent": _parsed_intent_schema(),
        "recipientText": {"type": "string", "nullable": True},
        "destinationText": {"type": "string", "nullable": True},
        "documentText": {"type": "string", "nullable": True},
        "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"], "nullable": True},
        "constraints": {"type": "object"},
        "field": {"type": "string", "enum": ["destination", "document"]},
        "value": {"type": "string"},
        "decision": {"type": "string", "enum": ["WAIT", "RETRY", "CANCEL"]},
        "reason": {"type": "string", "nullable": True},
    },
    "required": ["type"],
}


def _semantic_context(context: ConversationContext) -> dict[str, object]:
    payload: dict[str, object] = {"state": context.mode.value}
    if context.mode is ConversationMode.AWAITING_TASK_INFORMATION:
        payload["awaitingField"] = context.awaitingField
    elif context.mode is ConversationMode.AWAITING_ROBOT_DECISION:
        payload["reason"] = context.decisionReason.value if context.decisionReason else None
        payload["allowedDecisions"] = [decision.value for decision in context.allowedDecisions]
    return payload


def _system_prompt(context: ConversationContext) -> str:
    context_json = json.dumps(_semantic_context(context), ensure_ascii=False, sort_keys=True)
    return f"{CLU_SYSTEM_INSTRUCTIONS}\n\nConversation context JSON:\n{context_json}"


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
        body_text = error.read().decode("utf-8", errors="replace")
        message = "Ollama request failed"
        if body_text:
            try:
                error_payload = json.loads(body_text)
                if isinstance(error_payload, dict) and isinstance(error_payload.get("error"), str):
                    message = error_payload["error"]
            except ValueError:
                pass
        if "not found" in message.casefold() and "model" in message.casefold():
            raise OllamaModelNotFoundError(message) from error
        raise OllamaConnectionError(f"Ollama request failed with HTTP {error.code}.") from error
    except TimeoutError as error:
        raise OllamaTimeoutError("Ollama request timed out.") from error
    except (urllib.error.URLError, OSError) as error:
        raise OllamaConnectionError("Ollama server is not available.") from error


def _http_health_request(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise OllamaConnectionError(f"Ollama health check failed with HTTP {error.code}.") from error
    except TimeoutError as error:
        raise OllamaTimeoutError("Ollama health check timed out.") from error
    except (urllib.error.URLError, OSError) as error:
        raise OllamaConnectionError("Ollama server is not available.") from error


@dataclass(frozen=True)
class OllamaConfig:
    model: str
    base_url: str = "http://localhost:11434"
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise InvalidOllamaConfigurationError(
                "OLLAMA_MODEL must name an installed local model."
            )
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise InvalidOllamaConfigurationError("OLLAMA_BASE_URL must be a non-empty URL.")
        parsed_url = urlsplit(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise InvalidOllamaConfigurationError("OLLAMA_BASE_URL must be an HTTP(S) URL.")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise InvalidOllamaConfigurationError("OLLAMA_TIMEOUT must be a positive number.")
        if not isfinite(self.timeout) or self.timeout <= 0:
            raise InvalidOllamaConfigurationError("OLLAMA_TIMEOUT must be a positive number.")

    @property
    def chat_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/api/chat"

    @property
    def tags_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/api/tags"

    @classmethod
    def from_environment(cls) -> "OllamaConfig":
        return cls(
            model=os.environ.get("OLLAMA_MODEL", "").strip(),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip(),
            timeout=_timeout_from_environment(),
        )


def _timeout_from_environment() -> float:
    try:
        return float(os.environ.get("OLLAMA_TIMEOUT", "30"))
    except ValueError as error:
        raise InvalidOllamaConfigurationError("OLLAMA_TIMEOUT must be a positive number.") from error


def check_ollama_health(
    config: OllamaConfig,
    request: HealthRequestFunction = _http_health_request,
) -> None:
    """Verify that Ollama is reachable and the configured model is installed."""
    try:
        raw_response = request(config.tags_endpoint, config.timeout)
    except OllamaAdapterError:
        raise
    except Exception as error:
        raise OllamaConnectionError("Ollama server is not available.") from error
    try:
        payload = json.loads(raw_response.decode("utf-8") if isinstance(raw_response, bytes) else raw_response)
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        raise MalformedOllamaOutputError("Ollama health response was not valid JSON.") from error
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise MalformedOllamaOutputError("Ollama health response did not contain models.")
    installed = {
        item.get("name") or item.get("model")
        for item in models
        if isinstance(item, dict) and isinstance(item.get("name") or item.get("model"), str)
    }
    if config.model not in installed and f"{config.model}:latest" not in installed:
        raise OllamaModelNotFoundError(f"Ollama model '{config.model}' is not installed.")


class OllamaLanguageUnderstandingParser:
    """Stateless Ollama /api/chat implementation of the CLU parser."""

    def __init__(
        self,
        config: OllamaConfig | None = None,
        request: RequestFunction = _http_request,
    ):
        self._config = config or OllamaConfig.from_environment()
        self._request = request

    def parse(
        self,
        text: str,
        context: ConversationContext,
    ) -> LanguageUnderstandingResult:
        if not isinstance(text, str) or not text.strip():
            raise MalformedOllamaOutputError("User text must be a non-empty string.")
        body = json.dumps(
            {
                "model": self._config.model,
                "stream": False,
                "temperature": 0,
                "format": OLLAMA_CLU_RESPONSE_SCHEMA,
                "messages": [
                    {"role": "system", "content": _system_prompt(context)},
                    {"role": "user", "content": text},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            raw_response = self._request(
                self._config.chat_endpoint,
                {"Content-Type": "application/json"},
                body,
                self._config.timeout,
            )
        except OllamaAdapterError:
            raise
        except TimeoutError as error:
            raise OllamaTimeoutError("Ollama request timed out.") from error
        except Exception as error:
            raise OllamaConnectionError("Ollama server is not available.") from error
        content = self._extract_content(self._decode_response(raw_response))
        decoded = decode_language_understanding_result(content, context)
        if decoded.result is None:
            raise InvalidOllamaResultError(decoded.errors or ("INVALID_CLU_OUTPUT",))
        return decoded.result

    @staticmethod
    def _decode_response(raw_response: object) -> object:
        if isinstance(raw_response, bytes):
            try:
                return json.loads(raw_response.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise MalformedOllamaOutputError("Ollama response was not valid JSON.") from error
        if isinstance(raw_response, str):
            try:
                return json.loads(raw_response)
            except ValueError as error:
                raise MalformedOllamaOutputError("Ollama response was not valid JSON.") from error
        if isinstance(raw_response, dict):
            return raw_response
        raise MalformedOllamaOutputError("Ollama response must be a JSON object.")

    @staticmethod
    def _extract_content(response: object) -> str:
        if not isinstance(response, dict):
            raise MalformedOllamaOutputError("Ollama response must be a JSON object.")
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise MalformedOllamaOutputError("Ollama response did not contain message content.")
        if not message["content"].strip():
            raise MalformedOllamaOutputError("Ollama response contained empty model content.")
        return message["content"]


__all__ = [
    "InvalidOllamaConfigurationError",
    "InvalidOllamaResultError",
    "MalformedOllamaOutputError",
    "OllamaAdapterError",
    "OllamaConfig",
    "OllamaConnectionError",
    "OllamaLanguageUnderstandingParser",
    "OllamaModelNotFoundError",
    "OLLAMA_CLU_RESPONSE_SCHEMA",
    "OllamaTimeoutError",
    "check_ollama_health",
]