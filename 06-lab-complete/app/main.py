"""
Production AI Agent — Part 6 Final Project

  GET  /health  liveness  — luôn 200 khi process còn sống (không phụ thuộc Redis)
  GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không (hoặc đang shutdown)
  POST /ask     X-API-Key → rate limit (Redis) → cost guard (Redis) → mock LLM

Chạy: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import key_id, verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r as redis_conn, redis_healthy
from utils.mock_llm import ask as llm_ask


# ──────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng = 1 JSON object
# ──────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            entry.update(extra)
        if record.exc_info:
            # stack trace vẫn ghi vào log (cho dev debug) nhưng KHÔNG trả cho client
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    return logging.getLogger("agent")


logger = _setup_logging()


def log(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ──────────────────────────────────────────────────────────
# Lifecycle + graceful shutdown (tiêu chí 11)
# ──────────────────────────────────────────────────────────
START_TIME = time.time()
_state = {"ready": False, "in_flight": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: KHÔNG bắt buộc Redis — container phải chạy được cả khi Redis chưa lên
    log("startup", version=settings.app_version, environment=settings.environment,
        port=settings.port, redis_ok=redis_healthy(),
        rate_limit_per_minute=settings.rate_limit_per_minute)
    _state["ready"] = True
    yield
    # Shutdown: uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong
    # → mới chạy tới đây. Không tự đăng ký handler SIGTERM để khỏi chặn mất handler của uvicorn.
    _state["ready"] = False
    log("shutdown", in_flight=_state["in_flight"], uptime_s=round(time.time() - START_TIME, 1))
    try:
        redis_conn.close()
    except Exception:  # noqa: BLE001 — đang tắt, lỗi đóng kết nối không quan trọng
        pass
    log("shutdown_complete")


app = FastAPI(
    title="Production AI Agent",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None,
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    """Log mỗi request dạng JSON + request_id; thêm security headers."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    _state["in_flight"] += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    finally:
        _state["in_flight"] -= 1
        if request.url.path not in ("/health", "/ready"):   # probe gọi liên tục, không làm rác log
            log("request", method=request.method, path=request.url.path, status=status,
                ms=round((time.perf_counter() - started) * 1000, 1), request_id=request_id)


# ──────────────────────────────────────────────────────────
# Error handling: mã lỗi rõ ràng, không lộ stack trace
# ──────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]) or "body", "msg": e.get("msg")}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "Invalid request", "details": errors})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc,
                 extra={"fields": {"path": request.url.path, "error": type(exc).__name__}})
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ──────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"app": "Production AI Agent", "version": settings.app_version,
            "endpoints": ["GET /health", "GET /ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: chỉ trả lời 'process còn sống'. Không ping Redis — Redis chết thì restart agent cũng vô ích."""
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def ready():
    """Readiness: LB chỉ route traffic vào khi Redis kết nối được và app không đang shutdown."""
    if not _state["ready"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "starting or shutting down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True, "redis": "ok", "in_flight": _state["in_flight"]}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    kid = key_id(api_key)
    quota = check_rate_limit(kid)          # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                         # 402 khi hết DAILY/MONTHLY budget

    started = time.perf_counter()
    answer = llm_ask(body.question)
    llm_ms = round((time.perf_counter() - started) * 1000, 1)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log("llm_call", key_id=kid, user_id=body.user_id, question_chars=len(body.question),
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 8), llm_ms=llm_ms)

    return {
        "question": body.question,
        "answer": answer,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "rate_limit_remaining": quota["remaining"],
        },
    }
