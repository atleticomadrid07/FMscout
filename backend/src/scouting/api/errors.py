"""One error shape for every failure, mirroring PitchAPI's: {"error": {"code", "message"}}.

Clients branch on `code`, never on `message`.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from scouting.api.schemas import ErrorResponse

# HTTP status for routes FastAPI itself rejects (unknown path, wrong method).
_HTTP_CODES: Final = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def not_found(code: str, message: str) -> ApiError:
    return ApiError(404, code, message)


def _body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'][1:]) or 'request'}: {err['msg']}"
            for err in exc.errors()
        )
        return JSONResponse(_body("BAD_REQUEST", problems), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)


# For route declarations: documents the error envelope in OpenAPI.
ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    400: {"model": ErrorResponse, "description": "Bad request (BAD_REQUEST)"},
    404: {"model": ErrorResponse, "description": "Not found"},
    503: {"model": ErrorResponse, "description": "Database not ready (SCHEMA_OUT_OF_DATE)"},
}
# Routes that need a scope's season stats (slice 3b on) also answer 409.
SCOPED_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **ERROR_RESPONSES,
    409: {"model": ErrorResponse, "description": "Season not aggregated (SCOPE_NOT_AGGREGATED)"},
}
