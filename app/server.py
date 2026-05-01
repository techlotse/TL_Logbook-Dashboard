from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from logbook_parser import parse_pdf_to_summary


APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SESSION_ROOT = DATA_DIR / "sessions"
COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "tl_logbook_session")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(24 * 60 * 60)))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(80 * 1024 * 1024)))

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{24,96}$")


@dataclass
class SessionState:
    session_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "idle"
    step: str = "Waiting"
    progress: int = 0
    message: str = "Upload a FOCA logbook PDF to start."
    source_filename: str = ""
    summary: dict[str, Any] | None = None
    job_token: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)


app = FastAPI(title="TL-Logbook-Dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_sessions: dict[str, SessionState] = {}
_sessions_lock = threading.RLock()


def now() -> float:
    return time.time()


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def valid_session_id(value: str | None) -> bool:
    return bool(value and SESSION_ID_RE.fullmatch(value))


def session_dir(session_id: str) -> Path:
    if not valid_session_id(session_id):
        raise ValueError("Invalid session id")
    return SESSION_ROOT / session_id


def summary_path(session_id: str) -> Path:
    return session_dir(session_id) / "summary.json"


def empty_dashboard_summary() -> dict[str, Any]:
    return {
        "meta": {
            "is_empty": True,
            "owner": "",
            "source_filename": "",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "flight_count": 0,
            "first_date": "",
            "last_date": "",
        },
        "totals": {
            "flights": 0,
            "landings": 0,
            "total_minutes": 0,
            "pic_minutes": 0,
            "dual_minutes": 0,
            "copi_minutes": 0,
            "instructor_minutes": 0,
            "xc_minutes": 0,
            "pic_xc_minutes": 0,
            "xc_distance_nm": 0.0,
            "pic_xc_distance_nm": 0.0,
            "unique_airports": 0,
            "unique_routes": 0,
        },
        "aircraft_types": [],
        "registrations": [],
        "pic_names": [],
        "monthly": [],
        "yearly": [],
        "airports": [],
        "routes": [],
        "recent_flights": [],
        "unresolved_airports": [],
    }


def set_state(state: SessionState, **updates: Any) -> None:
    with state.lock:
        for key, value in updates.items():
            setattr(state, key, value)
        state.updated_at = now()


def load_persisted_summary(session_id: str) -> dict[str, Any] | None:
    path = summary_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_or_create_session(session_id: str) -> SessionState:
    with _sessions_lock:
        state = _sessions.get(session_id)
        if state:
            state.updated_at = now()
            return state

        persisted = load_persisted_summary(session_id)
        state = SessionState(session_id=session_id)
        if persisted:
            state.status = "ready"
            state.step = "Ready"
            state.progress = 100
            state.message = "Logbook loaded for this browser session."
            state.source_filename = persisted.get("meta", {}).get("source_filename", "")
            state.summary = persisted
        _sessions[session_id] = state
        return state


def status_payload(state: SessionState) -> dict[str, Any]:
    with state.lock:
        return {
            "status": state.status,
            "step": state.step,
            "progress": state.progress,
            "message": state.message,
            "source_filename": state.source_filename,
            "updated_at": int(state.updated_at),
            "has_logbook": state.summary is not None,
        }


def cleanup_expired_sessions() -> None:
    cutoff = now() - SESSION_TTL_SECONDS
    with _sessions_lock:
        for session_id, state in list(_sessions.items()):
            if state.updated_at < cutoff and state.status != "processing":
                _sessions.pop(session_id, None)

    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    for path in SESSION_ROOT.iterdir():
        if not path.is_dir() or not valid_session_id(path.name):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path)
        except Exception:
            pass


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    session_id = request.cookies.get(COOKIE_NAME)
    should_set_cookie = False
    if not valid_session_id(session_id):
        session_id = new_session_id()
        should_set_cookie = True

    request.state.session = get_or_create_session(session_id)
    response = await call_next(request)

    if should_set_cookie:
        response.set_cookie(
            COOKIE_NAME,
            session_id,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
        )
    return response


@app.on_event("startup")
def startup() -> None:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_expired_sessions()


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/legal", response_class=HTMLResponse)
def legal() -> str:
    return (STATIC_DIR / "legal.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status(request: Request) -> dict[str, Any]:
    return status_payload(request.state.session)


@app.get("/api/dashboard")
def dashboard(request: Request) -> dict[str, Any]:
    state: SessionState = request.state.session
    with state.lock:
        state.updated_at = now()
        if state.summary:
            return state.summary
    return empty_dashboard_summary()


def process_upload(session_id: str, token: str, pdf_path: Path, filename: str) -> None:
    state = get_or_create_session(session_id)
    try:
        set_state(state, status="processing", step="Parsing", progress=35, message="Reading FOCA tables and flight remarks.")
        summary = parse_pdf_to_summary(pdf_path, source_filename=filename)

        set_state(state, status="processing", step="Building analytics", progress=82, message="Calculating routes, PIC XC, aircraft, and registrations.")
        target_dir = session_dir(session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        final_pdf = target_dir / "logbook.pdf"
        pdf_path.replace(final_pdf)
        summary_path(session_id).write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

        with state.lock:
            if state.job_token != token:
                return
            state.summary = summary
            state.source_filename = filename
            state.status = "ready"
            state.step = "Ready"
            state.progress = 100
            state.message = "Logbook processed for this browser session."
            state.updated_at = now()
    except Exception as exc:
        try:
            if pdf_path.exists():
                pdf_path.unlink()
        except Exception:
            pass
        with state.lock:
            if state.job_token == token:
                state.status = "error"
                state.step = "Error"
                state.progress = 100
                state.message = str(exc)
                state.updated_at = now()


@app.post("/api/upload")
async def upload_logbook(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    state: SessionState = request.state.session
    filename = Path(file.filename or "logbook.pdf").name
    content_type = (file.content_type or "").lower()
    if not (filename.lower().endswith(".pdf") or content_type in {"application/pdf", "application/octet-stream"}):
        raise HTTPException(status_code=400, detail="Upload a PDF file.")

    with state.lock:
        if state.status == "processing":
            raise HTTPException(status_code=409, detail="This session is already processing an upload.")
        token = secrets.token_hex(16)
        state.job_token = token
        state.status = "uploading"
        state.step = "Uploading"
        state.progress = 8
        state.message = "Receiving PDF."
        state.updated_at = now()

    target_dir = session_dir(state.session_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = target_dir / f"{token}.uploading.pdf"

    total = 0
    first_chunk = b""
    try:
        with tmp_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                if not first_chunk:
                    first_chunk = chunk[:1024]
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="PDF is larger than the configured upload limit.")
                handle.write(chunk)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        finally:
            raise

    if total < 512 or b"%PDF" not in first_chunk:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="The uploaded file does not look like a valid PDF.")

    set_state(state, status="processing", step="Queued", progress=20, message="Starting parser.", source_filename=filename)
    thread = threading.Thread(target=process_upload, args=(state.session_id, token, tmp_path, filename), daemon=True)
    thread.start()

    return JSONResponse({"status": "ok", "message": "Upload received.", "bytes": total})


@app.post("/api/reset")
def reset_session(request: Request) -> dict[str, Any]:
    state: SessionState = request.state.session
    with state.lock:
        if state.status == "processing":
            raise HTTPException(status_code=409, detail="Wait for the current upload to finish before clearing the session.")
        state.summary = None
        state.source_filename = ""
        state.status = "idle"
        state.step = "Waiting"
        state.progress = 0
        state.message = "Upload a FOCA logbook PDF to start."
        state.job_token = ""
        state.updated_at = now()

    path = session_dir(state.session_id)
    if path.exists():
        shutil.rmtree(path)
    return {"status": "ok"}
