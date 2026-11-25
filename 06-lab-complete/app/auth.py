"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401
"""
import hashlib
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key)."""
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Include header: X-API-Key: <your-key>",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    # Không có AGENT_API_KEY trên server → từ chối mọi key (an toàn hơn là mở cửa).
    # compare_digest: so sánh thời gian hằng, tránh timing attack.
    if not settings.agent_api_key or not hmac.compare_digest(api_key, settings.agent_api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key


def key_id(api_key: str) -> str:
    """Định danh ổn định cho một key, dùng làm Redis key — không lưu key thô vào Redis / log."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]
