"""账号接口及 Bearer 令牌提取；授权本身仍由业务服务执行。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.schemas import LoginRequest, ProfileResponse, RegisterRequest, TokenResponse
from app.errors import AuthenticationError
from app.services.auth import AuthService

BEARER = HTTPBearer(auto_error=False, bearerFormat="opaque", description="登录响应中的 access_token")


def bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
) -> str:
    """不接受请求体或查询参数提供的身份信息。"""
    if credentials is None:
        raise AuthenticationError("请先登录并提供 Bearer 会话令牌")
    return credentials.credentials


SessionToken = Annotated[str, Depends(bearer_token)]


def auth_router(auth: AuthService) -> APIRouter:
    """将一个应用实例的认证服务绑定到路由。"""
    router = APIRouter(prefix="/auth", tags=["账号与登录"])

    @router.post("/register", response_model=ProfileResponse, status_code=201)
    def register(body: RegisterRequest) -> ProfileResponse:
        return ProfileResponse.from_profile(auth.register(
            body.username, body.password.get_secret_value(), body.name,
        ))

    @router.post("/login", response_model=TokenResponse)
    def login(body: LoginRequest) -> TokenResponse:
        result = auth.login(body.username, body.password.get_secret_value())
        return TokenResponse(access_token=result.access_token, expires_at=result.expires_at,
                             user=ProfileResponse.from_profile(result.profile))

    @router.get("/me", response_model=ProfileResponse)
    def me(token: SessionToken) -> ProfileResponse:
        return ProfileResponse.from_profile(auth.profile(token))

    @router.post("/logout", status_code=204)
    def logout(token: SessionToken) -> Response:
        auth.logout(token)
        return Response(status_code=204)

    return router
