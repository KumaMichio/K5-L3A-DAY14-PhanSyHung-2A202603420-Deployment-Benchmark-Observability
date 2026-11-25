# Day 14 Lab - Mission Answers

> Môi trường chạy: Windows 11 + Docker Desktop 29.3. Mọi lệnh Python chạy trong một container Linux
> (`python:3.12-slim`, `--network host`, mount Docker socket) để có `os.name == "posix"` như mục *Lưu ý* số 2
> của README yêu cầu (máy không có distro WSL2 Ubuntu). Lệnh `docker build` / `docker compose` chạy thẳng trên Docker Desktop.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found (`01-localhost-vs-production/develop/app.py`)

1. **Hardcode secret** — dòng 17–18: `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ key + mật khẩu DB, và muốn đổi key phải sửa code, build lại.
2. **Log ra secret** — dòng 34: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Log thường được gửi lên log aggregator, ai đọc log cũng thấy key (đã xác nhận trong log server ở 1.2).
3. **`print()` thay vì logging có cấu trúc** — dòng 33, 38: không có level, không có timestamp, không parse được; còn in nguyên câu trả lời (có thể chứa dữ liệu người dùng).
4. **Config cứng trong code** — dòng 21–22: `DEBUG = True`, `MAX_TOKENS = 500`; không đổi được giữa dev / staging / prod mà không sửa code.
5. **Không có health check** — không có `/health`, `/ready` (gọi `/health` trả **404**). Platform không biết khi nào app treo để restart, load balancer không biết khi nào nên route traffic.
6. **Bind `localhost`** — dòng 51: `host="localhost"`: trong container sẽ không nhận được kết nối từ bên ngoài (phải là `0.0.0.0`).
7. **Port cố định** — dòng 52: `port=8000`; Railway/Render inject `PORT` qua env var, app sẽ không lắng nghe đúng cổng.
8. **`reload=True` khi chạy thật** — dòng 53: tốn CPU (watch file), process supervisor phụ, không phải chế độ production.
9. **Không xử lý shutdown** — không có lifespan / SIGTERM handler: request đang chạy có thể bị cắt ngang khi platform tắt container.
10. **Không validate input** — `question: str` qua query string, không giới hạn độ dài, không có auth.

### Exercise 1.2: Chạy bản develop

```text
$ curl -s -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health
404

# log server — secret bị in ra
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
```

Nó chạy, nhưng không production-ready: lộ key trong log, không health check, chỉ nghe `localhost:8000`.
(Ghi chú: chạy bằng Python Windows thì `/ask` trả **500** vì `print()` tiếng Việt ra console cp1252 bị `UnicodeEncodeError` — thêm một ví dụ "works on my machine".)

Bản production (`cp .env.example .env`, `python app.py`):

```text
$ curl -s -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}
$ curl -s http://localhost:8000/health
{"status":"ok","uptime_seconds":3.6,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T07:16:40.493329+00:00"}
$ curl -s http://localhost:8000/ready
{"ready":true}
# SIGTERM → log: "Shutting down / Waiting for application shutdown / Application shutdown complete"
```

### Exercise 1.3: Comparison table

| Feature | Develop | Production | Tại sao quan trọng? |
|---------|---------|------------|---------------------|
| Config | Hardcode trong code (`OPENAI_API_KEY`, `DEBUG`, port) | Đọc env var qua `config.py` (`os.getenv`), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy được ở mọi môi trường; secret không nằm trong git; đổi config không cần build lại |
| Secrets | Key + mật khẩu DB trong source, còn in ra log | Lấy từ env, không bao giờ log (chỉ log `question_length`) | Lộ key = mất tiền / mất dữ liệu; log thường được nhiều người đọc |
| Health check | Không có (`/health` → 404) | `/health` (liveness, uptime, version) + `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform tự restart container chết; LB chỉ gửi traffic tới instance đã sẵn sàng |
| Logging | `print()` thô | Logging JSON (`{"time":..,"level":..,"msg":..}`) | Parse/lọc được trong Loki/Datadog, có level và timestamp |
| Shutdown | Đột ngột (không lifespan) | `lifespan` + handler SIGTERM: tắt `is_ready`, chờ request xong rồi mới thoát | Không cắt ngang request khi deploy / scale down |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Container nhận được traffic từ ngoài; khớp port platform cấp |
| Reload / debug | `reload=True` luôn bật | `reload` chỉ khi `DEBUG=true` | Không tốn tài nguyên, không có hành vi dev trong prod |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được từ browser |

Một lỗi nhỏ tìm được ở bản production: `config.py` gọi `logging.warning(...)` lúc import (trước `logging.basicConfig` trong `app.py`), nên root logger đã có handler mặc định → `basicConfig` sau đó không có tác dụng, level vẫn là WARNING, các dòng log JSON `agent_request` mức INFO không hiện ra (log server chỉ có `WARNING:root:OPENAI_API_KEY not set`). Ngoài ra app không tự nạp `.env` (không dùng `python-dotenv`), nên `.env` chỉ có tác dụng khi được export / truyền qua `docker --env-file`.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11` — bản Python đầy đủ (Debian + build tools), rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`); mọi `COPY`/`RUN`/`CMD` sau đó tính từ thư mục này.
3. **Tại sao COPY requirements.txt trước?** Docker cache theo layer. `requirements.txt` ít khi đổi, nên layer `pip install` được cache lại; khi chỉ sửa `app.py` thì build lại chỉ chạy từ `COPY app.py` trở đi (vài giây) thay vì cài lại toàn bộ dependencies. Nếu `COPY . .` trước thì sửa 1 dòng code cũng làm mất cache của `pip install`.
4. **CMD vs ENTRYPOINT:** `ENTRYPOINT` là chương trình cố định của container; `CMD` là lệnh/đối số mặc định, bị thay thế khi chạy `docker run image <lệnh khác>`. Khi có cả hai, `CMD` thành đối số cho `ENTRYPOINT`. File này chỉ dùng `CMD ["python", "app.py"]` (exec form → `python` là PID 1, nhận được SIGTERM trực tiếp).

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -p 8000:8000 my-agent:develop
$ curl -s -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl -s http://localhost:8000/health
{"status":"ok","uptime_seconds":3.9,"container":true}
```

### Exercise 2.3: Multi-stage build và Image size comparison

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này có build tools, không dùng để deploy.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user không phải root `appuser`, chỉ `COPY --from=builder /root/.local` (site-packages đã cài) + `main.py` + `utils/`, có `HEALTHCHECK`, chạy `uvicorn --workers 2` bằng `USER appuser`.
- **Tại sao nhỏ hơn:** base `slim` thay vì bản full (~1 GB), và gcc / header / apt cache / pip cache nằm lại ở stage builder, không được copy vào image cuối.

Đo bằng `docker images my-agent` (Docker Desktop 29.3 hiển thị 2 cột):

```text
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   893ee0839f3a        240MB         57.4MB
my-agent:develop    460b2bf58042       1.67GB          426MB
```

- Develop: **1.67 GB** (disk usage; content/compressed 426 MB)
- Production: **240 MB** (disk usage; content/compressed 57.4 MB)
- Difference: **giảm ≈ 85.6 %** theo disk usage ((1670 − 240) / 1670); ≈ 86.5 % theo content size ((426.3 − 57.4) / 426.3, số `docker image inspect --format {{.Size}}`: 426 320 381 B vs 57 409 069 B).

### Exercise 2.4: Docker Compose stack (`02-docker/production/docker-compose.yml`)

```text
                 host :80 / :443
                        │
                ┌───────▼────────┐
   client ────▶ │  nginx (alpine) │  reverse proxy, rate limit 10 r/s, security headers
                └───────┬────────┘
                        │ http://agent_backend → agent:8000   (network "internal", bridge)
                ┌───────▼────────┐
                │ agent (FastAPI) │  build multi-stage, target runtime, không publish port
                └───┬─────────┬──┘
   REDIS_URL=redis://redis:6379/0  QDRANT_URL=http://qdrant:6333
          ┌─────────▼──┐   ┌──▼──────────────┐
          │ redis:7    │   │ qdrant v1.9.0   │
          │ volume     │   │ volume          │
          │ redis_data │   │ qdrant_data     │
          └────────────┘   └─────────────────┘
```

Services được start: `redis`, `qdrant` (chờ healthy) → `agent` (`depends_on: condition: service_healthy`) → `nginx`. Chúng nói chuyện qua network bridge `internal` bằng DNS tên service (`agent`, `redis`, `qdrant`); chỉ nginx publish port ra host.

```text
$ docker compose -p k5-day14-docker up -d --build
SERVICE   STATUS                    PORTS
agent     Up 8 seconds (healthy)    8000/tcp
nginx     Up 7 seconds              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    Up 19 seconds (healthy)   6333-6334/tcp
redis     Up 19 seconds (healthy)   6379/tcp

$ curl -s -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":6.7,"version":"2.0.0","timestamp":"2026-11-25T07:28:48.288839"}

$ curl -s -i http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

Header `Server: nginx` + các security header chứng tỏ request đi qua Nginx rồi mới tới agent.

---

## Part 3: Cloud Deployment

_(điền sau CP6)_

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu?** Hàm dependency `verify_api_key()` (dòng 39–54) đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)`; `/ask` gắn `Depends(verify_api_key)`.
- **Sai key thì sao?** Thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key`.
- **Rotate key thế nào?** Key đọc từ env `AGENT_API_KEY` → đặt giá trị mới trên platform rồi restart/redeploy, không sửa code. Muốn rotate không downtime thì cho phép tạm thời 2 key (cũ + mới) trong một khoảng chuyển tiếp.

Chạy với `AGENT_API_KEY=secret-key-123 python app.py`:

```text
### Không có key
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

### Sai key (X-API-Key: wrong-key)
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

### Đúng key (X-API-Key: secret-key-123)
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT (`04-api-gateway/production/`)

Flow: `POST /auth/token` với username/password → `authenticate_user()` kiểm tra → `create_token()` ký HS256 payload `{sub, role, iat, exp=+60 phút}` bằng `JWT_SECRET` → client gửi `Authorization: Bearer <token>` → `verify_token()` decode + kiểm chữ ký + hạn → trả `{username, role}` cho endpoint. Server không cần tra DB mỗi request (stateless).

```text
$ curl -s -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
{"access_token":"<redacted>","token_type":"bearer","expires_in_minutes":60,"hint":"<redacted>"}

### /ask không có token
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

### /ask có Bearer token
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"question":"Explain JWT","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}
```

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Algorithm:** Sliding Window (log) — mỗi user một `deque` timestamp, bỏ các timestamp cũ hơn 60 s, đủ `max_requests` thì từ chối.
- **Limit:** user thường 10 request / 60 s (`rate_limiter_user`), admin 100 request / 60 s (`rate_limiter_admin`).
- **Bypass cho admin:** không bypass hẳn, mà chọn limiter theo `role` trong JWT: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user` (tài khoản `teacher` có role admin).
- Hạn chế: state nằm trong RAM của process → chạy nhiều instance thì mỗi instance đếm riêng (Part 6 chuyển sang Redis).

Vòng lặp 20 request với token của `student` (request đầu tiên ở 4.2 đã dùng 1 lượt):

```text
req 1 -> 200 {"requests_remaining":8,...}
req 2 -> 200 {"requests_remaining":7,...}
...
req 9 -> 200 {"requests_remaining":0,...}
req 10 -> 429 {"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}
...
req 20 -> 429 {"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}

$ curl -s -i ... (lần thứ 22)
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795591837
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

### Exercise 4.4: Cost guard

**Bản mẫu trong `cost_guard.py`:** mỗi request ước lượng token (`len(words) * 2`), quy ra USD theo giá GPT-4o-mini ($0.15 / 1M input, $0.60 / 1M output). `check_budget()` chạy **trước** khi gọi LLM: vượt budget global $10/ngày → 503, vượt budget user $1/ngày → **402 Payment Required**, ≥ 80 % → log warning. `record_usage()` cộng dồn **sau** khi gọi LLM. Reset theo ngày bằng cách so `record.day` với ngày hôm nay. Sau 10 request: `/me/usage` → `{"requests":10,"input_tokens":40,"output_tokens":300,"cost_usd":0.000186,"budget_usd":1.0,...}`.

(Lỗi nhỏ: response `/ask` đặt tên `budget_remaining_usd` nhưng giá trị là `usage.total_cost_usd`, tức số tiền **đã dùng**.)

**Cách tôi làm cho Part 6 (Redis, $10/tháng/user):**

```python
def check_budget(user_id: str, estimated_cost: float) -> bool:
    key = f"budget:{user_id}:{datetime.now(timezone.utc):%Y-%m}"   # key theo tháng → tự "reset" đầu tháng
    current = float(r.get(key) or 0)
    if current + estimated_cost > MONTHLY_BUDGET_USD:              # 10.0
        return False                                               # caller trả 402
    r.incrbyfloat(key, estimated_cost)                             # atomic, mọi instance dùng chung
    r.expire(key, 32 * 24 * 3600)                                  # dọn key cũ
    return True
```

State ở Redis nên mọi instance thấy cùng một số dư; key chứa `YYYY-MM` nên sang tháng mới tự dùng key mới. Rate limit bảo vệ **tốc độ** (chống spam / DoS, 10 req/phút), cost guard bảo vệ **tiền** (một user gọi chậm nhưng đều đặn cả tháng vẫn bị chặn khi hết $10) — cần cả hai vì mỗi cái chặn một kiểu lạm dụng mà cái kia không chặn được.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- `/health` (liveness): process còn sống → 200, kèm uptime, version, kiểm tra RAM bằng `psutil` (`degraded` khi > 90 %). Không check dependency bên ngoài — nếu Redis chết mà `/health` fail thì platform sẽ restart vô ích.
- `/ready` (readiness): 503 khi `_is_ready=False` (đang khởi động / đang shutdown / dependency chưa sẵn sàng), 200 kèm `in_flight_requests` khi sẵn sàng. LB dùng nó để quyết định có route traffic vào instance không.

```text
$ curl -s http://localhost:8000/health
{"status":"ok","uptime_seconds":3.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T07:29:59.369555+00:00","checks":{"memory":{"status":"ok","used_percent":17.0}}}
$ curl -s http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

Middleware `track_requests` đếm `_in_flight_requests`; khi nhận SIGTERM uvicorn ngừng nhận kết nối mới, lifespan đặt `_is_ready=False` rồi chờ request đang chạy xong (tối đa 30 s, `timeout_graceful_shutdown=30`).

Test: gửi `POST /ask?question=Long task` (mock LLM mất ~3 s) rồi `kill -TERM` ngay 50 ms sau:

```text
INFO:     Shutting down
INFO:     127.0.0.1:46478 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
INFO 🔄 Graceful shutdown initiated...
INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [174]
INFO Received signal 15 — uvicorn will handle graceful shutdown

# phía client:
{"answer":"Đây là câu trả lời từ AI agent (mock)..."}  -> HTTP 200 in 3.019661s
```

Request đang chạy vẫn hoàn thành với 200, sau đó process mới thoát. (Dòng "Received signal 15" in cuối cùng vì uvicorn cài handler riêng lúc `serve()` và chỉ gọi lại handler cũ của app sau khi shutdown xong.)

### Exercise 5.3: Stateless design (`production/app.py`)

Session / history lưu ở Redis với key `session:{session_id}` (`setex`, TTL 3600 s, giữ tối đa 20 message), không phải dict trong RAM. Nếu lưu trong RAM: request 1 vào instance A ghi history ở RAM của A, request 2 vào instance B không thấy gì → mất hội thoại; restart/scale down cũng mất hết. Với Redis thì instance nào cũng đọc được cùng một state, có thể thêm/bớt/kill instance tuỳ ý. (File vẫn có fallback in-memory khi không kết nối được Redis — chỉ để chạy demo, response sẽ báo `"storage": "in-memory"`.)

### Exercise 5.4: Load balancing

```text
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 10 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 10 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 11 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 10 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 22 seconds (healthy)   6379/tcp

# 10 request POST http://localhost:8080/chat
req 'Request 1' served_by=instance-5942bb storage=redis
req 'Request 2' served_by=instance-6255b5 storage=redis
req 'Request 3' served_by=instance-d67df4 storage=redis
req 'Request 4' served_by=instance-5942bb storage=redis
...
req 'Request 10' served_by=instance-5942bb storage=redis

# docker compose -p k5-day14-scaling logs agent | grep "POST /chat" (sau cả 5.4 và 5.5)
      5 agent-1
      5 agent-2
      5 agent-3
```

Nginx round-robin đều giữa 3 instance (DNS `agent` của Compose trả về 3 IP). Thử thêm: `docker stop k5-day14-scaling-agent-2` rồi gửi tiếp 6 request → cả 6 đều **200**, luân phiên giữa 2 instance còn lại (`proxy_next_upstream error timeout http_503`) — traffic tự chuyển sang instance còn sống.

### Exercise 5.5: Test stateless (`python test_stateless.py`)

```text
Session ID: f31835db-996c-4e4b-a44f-bed1a193943b

Request 1: [instance-6255b5]  Q: What is Docker?
Request 2: [instance-d67df4]  Q: Why do we need containers?
Request 3: [instance-5942bb]  Q: What is Kubernetes?
Request 4: [instance-6255b5]  Q: How does load balancing work?
Request 5: [instance-d67df4]  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-5942bb', 'instance-6255b5', 'instance-d67df4'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ... (đủ 5 cặp user/assistant)
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu trong cùng một session được 3 instance khác nhau phục vụ, nhưng history đọc lại có đủ 10 message theo đúng thứ tự — vì mỗi instance đọc/ghi `session:<id>` trong Redis chung chứ không giữ trong RAM. Script này không tự tắt instance; phần kill instance tôi làm tay ở 5.4 ở trên.

---

## Part 6: Final Project

_(điền sau CP5)_

---

## AI log

_(điền ở cuối)_
