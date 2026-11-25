# Deployment Information

## Public URL

https://day14-agent-3odr.onrender.com

(Gói free của Render ngủ sau ~15 phút không có request; request đầu tiên sau đó mất ~50 giây để container khởi động lại.)

## Platform

**Render** (Blueprint) — deploy chính agent Part 6 trong `06-lab-complete/`.

| Resource | Loại | Ghi chú |
|---|---|---|
| `day14-agent` | Web Service, runtime Docker, plan free, region Singapore | Build từ `06-lab-complete/Dockerfile` (multi-stage, user non-root), health check `GET /health` |
| `day14-redis` | Key Value (Redis-compatible), plan free | Chỉ nhận kết nối nội bộ (`ipAllowList: []`), `maxmemoryPolicy: noeviction` |

Blueprint: [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml) (Blueprint Path = `06-lab-complete/render.yaml` vì file không nằm ở gốc repo).

## Test Commands

Đặt biến (key lấy ở Render Dashboard → `day14-agent` → Environment, **không** ghi vào repo):

```bash
export BASE=https://day14-agent-3odr.onrender.com
export AGENT_API_KEY=<key từ Render>
```

### 4 lệnh tự kiểm (CP6) — chỉ in mã HTTP

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy thật (2026-11-25 08:52 UTC):

```text
200
200
401
200
```

### Health / readiness

```bash
curl https://day14-agent-3odr.onrender.com/health
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":405.4,"timestamp":"2026-11-25T08:47:10.987500+00:00"}
curl https://day14-agent-3odr.onrender.com/ready
# {"ready":true,"redis":"ok","in_flight":1}      ← Redis trên Render đã kết nối
```

### API test (with authentication)

```bash
curl -X POST https://day14-agent-3odr.onrender.com/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.",
#  "usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05,"rate_limit_remaining":8}}
```

### Rate limit (10 request / phút / key, state trong Redis)

```text
$ for i in $(seq 1 12); do curl ... -H "X-API-Key: $AGENT_API_KEY" "$BASE/ask"; done   # 2 request đã dùng ngay trước đó
200 200 200 200 200 200 200 200 429 429 429 429
```

Sai key → `401 {"detail":"Invalid API key."}`.

### Script chấm với URL public

```text
$ python check_production_ready.py --static-only --public-url https://day14-agent-3odr.onrender.com
✅ 12. Public URL ready (Railway / Render config)
     ✅ Public URL: GET https://day14-agent-3odr.onrender.com/health → 200
```

## Environment Variables Set

Đặt trên Render (qua Blueprint), **không có giá trị secret nào trong code hay git**:

| Biến | Giá trị / nguồn |
|---|---|
| `PORT` | Render tự cấp (10000); `CMD` trong Dockerfile lắng nghe `${PORT:-8000}` |
| `ENVIRONMENT` | `production` (bắt buộc có `AGENT_API_KEY`, tắt `/docs`) |
| `AGENT_API_KEY` | `generateValue: true` — Render tự sinh, xem trong Dashboard |
| `REDIS_URL` | `fromService` → connection string nội bộ của `day14-redis` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `LOG_LEVEL` | không đặt → mặc định `INFO` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
