"""HTTP transport only. Run with: python -m office_robot_nlp.http_api."""

import ipaddress
import os
from dataclasses import dataclass, field
from secrets import compare_digest

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from office_robot_nlp.api_dto import (
    BatchSnapshot, CreateSessionRequest, ErrorResponse, HealthResponse, MessageRequest,
    MessageResponse, SessionResponse, TaskSnapshot,
)
from office_robot_nlp.session_bridge import BridgeError, NlpSessionBridge
from office_robot_nlp.gemini_adapter import GeminiConfig
from office_robot_nlp.ollama_adapter import OllamaConfig
from office_robot_nlp.openai_adapter import OpenAIConfig
from office_robot_nlp.semantic_map import load_semantic_registry


def running_on_render() -> bool:
    return os.environ.get("RENDER", "").casefold() == "true"


@dataclass(frozen=True)
class ApiSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    access_token: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if not self.host.strip() or not 1 <= self.port <= 65535:
            raise ValueError("API_HOST and API_PORT must specify a valid bind address and port.")
        try:
            local = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            local = self.host.casefold() == "localhost"
        if self.access_token is not None and (not self.access_token.strip() or any(c.isspace() for c in self.access_token)):
            raise ValueError("API_ACCESS_TOKEN must be nonempty and contain no whitespace.")
        if not local and self.access_token is None:
            raise ValueError("Non-loopback API_HOST requires API_ACCESS_TOKEN.")

    @classmethod
    def from_environment(cls):
        render = running_on_render()
        port_variable = "PORT" if render else "API_PORT"
        try:
            port = int(os.environ.get(port_variable, "" if render else "8000"))
        except ValueError:
            raise ValueError(f"{port_variable} must be an integer between 1 and 65535.") from None
        if not 1 <= port <= 65535:
            raise ValueError(f"{port_variable} must be an integer between 1 and 65535.")
        host = os.environ.get("API_HOST", "0.0.0.0" if render else "127.0.0.1")
        if render and host != "0.0.0.0":
            raise ValueError("Render requires API_HOST=0.0.0.0 and API_ACCESS_TOKEN.")
        return cls(host, port, os.environ.get("API_ACCESS_TOKEN"))


def validate_render_environment() -> None:
    """Fail early on invalid cloud configuration, using local validation only."""
    provider = os.environ.get("CLU_PROVIDER", "gemini").casefold()
    configurations = {"gemini": GeminiConfig, "openai": OpenAIConfig, "ollama": OllamaConfig}
    if provider not in configurations:
        raise ValueError("CLU_PROVIDER must be gemini, openai or ollama.")
    try:
        configurations[provider].from_environment()
    except (RuntimeError, ValueError):
        names = ("GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL and GEMINI_TIMEOUT" if provider == "gemini"
                 else f"the {provider.upper()}_* environment variables")
        raise ValueError(f"Invalid {provider} configuration; check {names}.") from None
    nlg = os.environ.get("NLG_ENABLED", "false").casefold()
    if nlg not in {"true", "false"}:
        raise ValueError("NLG_ENABLED must be true or false.")
    if nlg == "true":
        if os.environ.get("NLG_PROVIDER", "gemini").casefold() != "gemini":
            raise ValueError("NLG_PROVIDER must be gemini when NLG_ENABLED=true.")
        try:
            GeminiConfig.from_environment()
        except (RuntimeError, ValueError):
            raise ValueError("Invalid Gemini NLG configuration; check GEMINI_API_KEY and GEMINI_MODEL.") from None
    try:
        load_semantic_registry()
    except (OSError, ValueError):
        raise ValueError("Invalid semantic map configuration; check ROOM_REGISTRY_PATH and ROOM_ALIASES_PATH.") from None


_ERROR_STATUS = {
    "SESSION_NOT_FOUND": 404, "TASK_NOT_FOUND": 404, "BATCH_NOT_FOUND": 404,
    "MESSAGE_ID_CONFLICT": 409, "PROVIDER_UNAVAILABLE": 503, "INVALID_MESSAGE": 422,
}


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_api(bridge: NlpSessionBridge | None = None, settings: ApiSettings | None = None) -> FastAPI:
    if running_on_render():
        # Uvicorn --factory invokes this path too. Explicit local settings cannot
        # bypass Render's mandatory public binding/token/configuration checks.
        environment_settings = ApiSettings.from_environment()
        if settings is not None and settings != environment_settings:
            raise ValueError("Render API settings must match the validated environment.")
        settings = environment_settings
        validate_render_environment()
    settings = settings or ApiSettings.from_environment()
    bridge = bridge or NlpSessionBridge()
    api = FastAPI(title="Office Robot NLP", version="1.0", debug=False,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @api.middleware("http")
    async def boundary(request: Request, call_next):
        if settings.access_token is not None:
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {settings.access_token}"
            if not compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                return error_response(401, "UNAUTHORIZED", "A valid bearer token is required.")
        try:
            return await call_next(request)
        except Exception:
            # Do not forward an exception to Uvicorn's traceback logger: provider
            # errors and validation exceptions may contain secrets/user content.
            return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")

    @api.exception_handler(BridgeError)
    async def bridge_error(request: Request, error: BridgeError):
        return error_response(_ERROR_STATUS.get(error.code, 500), error.code, error.message)

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return error_response(422, "INVALID_REQUEST", "Request payload is invalid.")

    @api.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response(error.status_code, "HTTP_ERROR", "HTTP request could not be handled.")

    errors = {code: {"model": ErrorResponse} for code in (401, 404, 409, 422, 500, 503)}

    # Sync routes run blocking providers in the framework worker pool. Session
    # serialization belongs to the bridge, not the async event loop or routes.
    @api.post("/v1/sessions", status_code=201, response_model=SessionResponse, responses=errors)
    def create_session(body: CreateSessionRequest):
        return bridge.create_session()

    @api.post("/v1/sessions/{sessionId}/messages", response_model=MessageResponse, responses=errors)
    def message(sessionId: str, body: MessageRequest):
        return bridge.handle_user_message(sessionId, body.messageId, body.text)

    @api.get("/v1/tasks/{taskId}", response_model=TaskSnapshot, responses=errors)
    def get_task(taskId: str):
        return bridge.get_task(taskId)

    @api.get("/v1/batches/{batchId}", response_model=BatchSnapshot, responses=errors)
    def get_batch(batchId: str):
        return bridge.get_batch(batchId)

    @api.get("/health", response_model=HealthResponse, responses=errors)
    def health():
        return HealthResponse()

    return api


def main():
    import uvicorn

    try:
        settings = ApiSettings.from_environment()
        api = create_api(settings=settings)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    # Exactly one process owns all sessions. No reload or multi-worker mode.
    uvicorn.run(api, host=settings.host, port=settings.port,
                workers=1, reload=False, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
