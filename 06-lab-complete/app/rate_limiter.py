"""
Rate limiting (tiêu chí 7) — sliding window trên Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây
  • Vượt giới hạn → HTTP 429 kèm Retry-After
  • State nằm trong Redis → mọi worker / instance dùng chung, còn nguyên sau khi restart agent
"""
import math
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r, redis_unavailable

WINDOW_SECONDS = 60


def check_rate_limit(key: str) -> dict:
    """`key` là định danh của API key (đã hash). Trả về thông tin quota còn lại."""
    limit = settings.rate_limit_per_minute
    now = time.time()
    rkey = f"ratelimit:{key}"
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"   # unique, kể cả 2 request cùng micro-giây

    try:
        # MULTI/EXEC: bỏ entry cũ, thêm request này, đếm — atomic giữa các worker
        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)
        pipe.zadd(rkey, {member: now})
        pipe.zcard(rkey)
        pipe.expire(rkey, WINDOW_SECONDS + 1)
        _, _, count, _ = pipe.execute()

        if count > limit:
            # Không tính request bị từ chối vào cửa sổ, nếu không client spam sẽ bị khoá mãi
            r.zrem(rkey, member)
            oldest = r.zrange(rkey, 0, 0, withscores=True)
            reset_in = (oldest[0][1] + WINDOW_SECONDS - now) if oldest else WINDOW_SECONDS
            retry_after = max(1, math.ceil(reset_in))
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": limit,
                    "window_seconds": WINDOW_SECONDS,
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
    except redis.RedisError as exc:
        raise redis_unavailable(exc) from exc

    return {"limit": limit, "remaining": max(0, limit - count)}
