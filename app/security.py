import os
import hmac
from fastapi import Header, HTTPException, status, Request
from typing import Optional

VALID_ROLES = ["Viewer", "User", "Admin"]

def resolve_role(request: Request) -> str:
    role = request.headers.get("X-User-Role") or request.query_params.get("role") or "User"
    if role not in VALID_ROLES:
        role = "User"
    if role == "Admin":
        admin_token = os.getenv("ADMIN_TOKEN")
        if admin_token and not hmac.compare_digest(
            request.headers.get("X-Admin-Token", ""), admin_token
        ):
            role = "User"
    return role

def get_current_role(request: Request) -> str:
    return resolve_role(request)

def require_role(min_role: str):
    role_weights = {"Viewer": 1, "User": 2, "Admin": 3}
    def role_checker(request: Request):
        role = resolve_role(request)
        if role_weights.get(role, 1) < role_weights.get(min_role, 2):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role level: '{min_role}'. Your current active role: '{role}'."
            )
        return role
    return role_checker

