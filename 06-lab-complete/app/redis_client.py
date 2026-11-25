"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • redis.from_url() không mở kết nối ngay → app vẫn khởi động được khi Redis chưa sẵn sàng
  • redis_healthy() dùng cho GET /ready: Redis không kết nối được → False
"""
import redis
from fastapi import HTTPException

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1.0,   # Redis chết thì /ready trả 503 nhanh, không treo
    socket_timeout=2.0,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False


def redis_unavailable(exc: Exception) -> HTTPException:
    """Lỗi Redis khi đang xử lý /ask → 503 rõ ràng thay vì 500 + stack trace."""
    return HTTPException(
        status_code=503,
        detail="State store (Redis) unavailable. Please retry later.",
        headers={"Retry-After": "5"},
    )
