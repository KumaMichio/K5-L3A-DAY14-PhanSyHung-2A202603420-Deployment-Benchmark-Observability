"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis — tổng của cả service
  • Hai ngưỡng: DAILY_BUDGET_USD (key theo ngày) và MONTHLY_BUDGET_USD (key theo tháng)
  • Tổng chi phí đã dùng chạm một trong hai ngưỡng → HTTP 402
  • Key chứa ngày/tháng (UTC) nên sang ngày/tháng mới tự "reset"; có TTL để Redis tự dọn
"""
from datetime import datetime, timezone

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r, redis_unavailable

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAY_TTL = 2 * 24 * 3600
MONTH_TTL = 32 * 24 * 3600


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """Ước lượng thô ~4 ký tự / token (mock LLM không trả usage thật)."""
    return max(1, len(text) // 4)


def get_spend() -> dict:
    day_key, month_key = _keys()
    day, month = r.mget(day_key, month_key)
    return {"daily_usd": float(day or 0), "monthly_usd": float(month or 0)}


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    try:
        spend = get_spend()
    except redis.RedisError as exc:
        raise redis_unavailable(exc) from exc

    if spend["daily_usd"] >= settings.daily_budget_usd:
        period, used, budget = "daily", spend["daily_usd"], settings.daily_budget_usd
    elif spend["monthly_usd"] >= settings.monthly_budget_usd:
        period, used, budget = "monthly", spend["monthly_usd"], settings.monthly_budget_usd
    else:
        return
    raise HTTPException(
        status_code=402,
        detail={
            "error": f"{period.capitalize()} budget exceeded",
            "used_usd": round(used, 6),
            "budget_usd": budget,
            "resets": "00:00 UTC" if period == "daily" else "1st of next month (UTC)",
        },
    )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    day_key, month_key = _keys()
    try:
        pipe = r.pipeline(transaction=True)
        pipe.incrbyfloat(day_key, cost)        # atomic giữa các worker / instance
        pipe.expire(day_key, DAY_TTL)
        pipe.incrbyfloat(month_key, cost)
        pipe.expire(month_key, MONTH_TTL)
        pipe.execute()
    except redis.RedisError as exc:
        raise redis_unavailable(exc) from exc
    return cost
