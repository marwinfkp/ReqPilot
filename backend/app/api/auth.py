"""Login / logout / current user."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models import User
from app.schemas import LoginRequest
from app.security import (ROLE_PERMISSIONS, SESSION_COOKIE, audit, create_session, delete_session,
                          get_current_user, rate_limit, read_token, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_dict(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role,
            "is_active": user.is_active, "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set()))}


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 60))])
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        audit(db, "auth.login_failed", details={"email": body.email[:200]}, request=request)
        raise HTTPException(401, "Invalid email or password")
    token = create_session(db, user)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                        max_age=config.SESSION_HOURS * 3600, path="/")
    audit(db, "auth.login", user, request=request)
    # The token is only sent as an HttpOnly cookie, never in the JSON body (XSS protection).
    return {"user": user_dict(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    token = read_token(request)
    if token:
        delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    audit(db, "auth.logout", user, request=request)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_dict(user)
