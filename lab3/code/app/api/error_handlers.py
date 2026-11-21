"""统一异常响应，并移除校验错误中的原始请求值，避免回显密码。"""

import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import (
    AuthenticationError, ConflictError, DuplicateEntryError, NotFoundError,
    PermissionDeniedError, RateLimitError,
    SchemaVersionError, ValidationError,
)

LOGGER = logging.getLogger(__name__)


def install_error_handlers(application: FastAPI) -> None:
    """可预期错误给出稳定状态码，底层错误不暴露 SQL 或凭据。"""
    statuses = {AuthenticationError: 401, PermissionDeniedError: 403, NotFoundError: 404,
                ConflictError: 409, DuplicateEntryError: 409, ValidationError: 422,
                RateLimitError: 429}

    async def domain_error(_request: Request, error: Exception) -> JSONResponse:
        headers = {}
        if isinstance(error, AuthenticationError):
            headers["WWW-Authenticate"] = "Bearer"
        if isinstance(error, RateLimitError):
            headers["Retry-After"] = str(error.retry_after)
        content = {"detail": str(error)}
        if isinstance(error, DuplicateEntryError):
            content["code"] = "possible_duplicate"
        return JSONResponse(status_code=statuses[type(error)], content=content, headers=headers)

    for error_type in statuses:
        application.add_exception_handler(error_type, domain_error)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError):
        safe_errors = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                       for item in error.errors()]
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    async def storage_error(_request: Request, _error: Exception):
        LOGGER.error("业务存储暂时不可用")
        return JSONResponse(status_code=503, content={"detail": "数据存储暂时不可用"})

    for error_type in (sqlite3.Error, SchemaVersionError, OSError):
        application.add_exception_handler(error_type, storage_error)
