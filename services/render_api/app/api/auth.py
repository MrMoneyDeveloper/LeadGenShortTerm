import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

bearer = HTTPBearer(auto_error=False)


def check(credentials, expected):
    token = expected.get_secret_value()
    if not token:
        raise HTTPException(503, "API token not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, token):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})


def dashboard(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    check(credentials, settings().dashboard_api_token)


def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    check(credentials, settings().processor_trigger_token)
