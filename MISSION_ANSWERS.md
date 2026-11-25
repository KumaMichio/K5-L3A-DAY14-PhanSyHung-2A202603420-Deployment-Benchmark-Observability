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

### Exercise 3.2: Render deployment (nền tảng chính)

Theo CP6, tôi deploy **chính agent Part 6** (`06-lab-complete/`) chứ không deploy app mẫu `03-cloud-deployment/`.

- **URL:** https://day14-agent-3odr.onrender.com
- **Cách deploy:** Render Dashboard → New → Blueprint → repo này, Blueprint Path `06-lab-complete/render.yaml`. Blueprint tạo 2 resource: web service `day14-agent` (Docker) + Key Value `day14-redis`; `REDIS_URL` nối tự động bằng `fromService`, `AGENT_API_KEY` do Render sinh (`generateValue: true`).
- **4 lệnh tự kiểm CP6** (chạy 2026-11-25 08:52 UTC):

```text
GET  /health          -> 200
GET  /ready           -> 200     {"ready":true,"redis":"ok",...}  ← Redis cloud đã kết nối
POST /ask (no key)    -> 401
POST /ask (with key)  -> 200
burst 12 request (2 đã dùng): 200 ×8 rồi 429 ×4   ← rate limit 10/phút chạy bằng Redis trên cloud
```

- **Screenshot:** [dashboard](screenshots/dashboard.png) · [service running](screenshots/running.png) · [test](screenshots/test.png). Chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md).

### Nền tảng còn lại: so sánh `railway.toml` với `render.yaml`

Tôi viết cả hai file trong `06-lab-complete/` (cả hai đều được script chấm kiểm ✅).

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Phạm vi | Cấu hình **1 service** (build + deploy) | **Blueprint** mô tả cả hạ tầng: web service **và** Redis (`type: keyvalue`), nối với nhau | Railway thêm Redis bằng dashboard/CLI (`railway add`) rồi tham chiếu biến `${{Redis.REDIS_URL}}`; Render cho khai báo cả stack dạng Infrastructure-as-Code trong một file |
| Build | `[build] builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `dockerfilePath`, `dockerContext` | Render cần biết thư mục context vì file nằm trong thư mục con `06-lab-complete/`; Railway chạy `railway up` ngay trong thư mục đó nên context là `.` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` | Cùng ý nghĩa: platform chỉ chuyển traffic sang bản deploy mới khi `/health` trả 200 (zero-downtime deploy) |
| Restart / scale | `restartPolicyType = "ON_FAILURE"`, `numReplicas` | `plan`, `region` (scale chỉnh trong dashboard; free plan = 1 instance) | Railway tính tiền theo tài nguyên dùng, cho chỉnh replicas trong file; Render gắn với plan |
| Biến môi trường / secret | **Không** ghi trong file — đặt bằng `railway variables set AGENT_API_KEY=...` | Khai báo **trong** file nhưng secret không có giá trị: `generateValue: true` hoặc `sync: false`; `REDIS_URL` qua `fromService` | File nằm trong git nên tuyệt đối không chứa secret; Render có cơ chế tự sinh / hỏi khi tạo Blueprint, Railway tách hẳn ra CLI/dashboard |
| PORT | Railway inject `PORT` → `CMD ... --port ${PORT:-8000}` | Render inject `PORT=10000` → cùng `CMD` | Cùng một Dockerfile chạy được cả hai nơi nhờ đọc `PORT` từ env (12-factor) |

### Exercise 3.3: Cloud Run CI/CD pipeline (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` là pipeline Cloud Build chạy tuần tự 4 bước, bước sau `waitFor` bước trước:
1. **test** — trong image `python:3.11-slim`: `pip install -r requirements.txt pytest` rồi `pytest tests/`; test fail thì dừng cả pipeline, không build.
2. **build** — `docker build` image gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tái sử dụng layer.
3. **push** — `docker push --all-tags` lên Container Registry.
4. **deploy** — `gcloud run deploy ai-agent --image ...:$COMMIT_SHA` (tag theo commit → rollback được), region `asia-southeast1`, 512Mi / 1 CPU, `min-instances=1` (tránh cold start), `max-instances=10`, secret `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`), không nằm trong code.

`service.yaml` là cấu hình Knative tương đương (khai báo thay cho cờ CLI): autoscale 1–10 instance, `containerConcurrency: 80`, `livenessProbe` → `/health`, `startupProbe` → `/ready`, và `AGENT_API_KEY` / `OPENAI_API_KEY` lấy bằng `secretKeyRef`.

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

**Cách tôi làm trong Part 6 (`06-lab-complete/app/cost_guard.py`, state trong Redis):**

```python
def _keys():
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"   # key theo ngày/tháng → tự "reset"

def check_budget():                      # gọi TRƯỚC khi gọi LLM
    day, month = r.mget(*_keys())
    if float(day or 0) >= settings.daily_budget_usd or float(month or 0) >= settings.monthly_budget_usd:
        raise HTTPException(402, ...)    # Payment Required

def record_cost(input_tokens, output_tokens):   # gọi SAU khi gọi LLM
    cost = input_tokens/1000*0.00015 + output_tokens/1000*0.0006
    pipe = r.pipeline(transaction=True)         # INCRBYFLOAT atomic giữa các worker / instance
    pipe.incrbyfloat(day_key, cost); pipe.expire(day_key, 2*86400)
    pipe.incrbyfloat(month_key, cost); pipe.expire(month_key, 32*86400)
    pipe.execute()
```

Budget tính trên **tổng chi phí của cả service** (README Part 6: "Tổng chi phí vượt `DAILY_BUDGET_USD` / `MONTHLY_BUDGET_USD` → 402"), hai ngưỡng ngày ($1) và tháng ($10) đọc từ env. State ở Redis nên mọi worker/instance thấy cùng một số; key chứa ngày/tháng UTC nên sang kỳ mới tự dùng key mới, TTL để Redis tự dọn. Redis lỗi → 503 rõ ràng chứ không cho gọi LLM "miễn phí". Muốn giới hạn theo từng user chỉ cần thêm định danh key vào tên key (`cost:daily:{key_id}:{date}`). Rate limit bảo vệ **tốc độ** (chống spam / DoS, 10 req/phút), cost guard bảo vệ **tiền** (một user gọi chậm nhưng đều đặn cả tháng vẫn bị chặn khi hết $10) — cần cả hai vì mỗi cái chặn một kiểu lạm dụng mà cái kia không chặn được.

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

Code trong `06-lab-complete/` (sửa hết TODO, làm ngay trong thư mục đó). Chạy `python check_production_ready.py` bản **đầy đủ** (có Docker, không `--skip-docker`) trong Linux container (`os.name == "posix"`, nên bài test SIGTERM chạy thật):

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết từ cùng lần chạy: image **63 MB**, build 43 s; `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)`; `state nằm trong Redis — restart agent vẫn bị 429`; `402 ở request thứ 1`; `15 dòng JSON`; graceful shutdown `exit code 0`; `docker stop` `exit code 0, 1.0s`. Chạy lại lần cuối kèm URL public (`python check_production_ready.py --public-url https://day14-agent-3odr.onrender.com`): `✅ Public URL: GET https://day14-agent-3odr.onrender.com/health → 200` → **`Result: 12/12 tiêu chí đạt · 44/44 checks passed — PASS 12/12`**. (Lưu ý: gói free của Render ngủ sau ~15 phút; nếu gọi lúc đang ngủ, request đầu mất ~50 s và check URL public có thể timeout 15 s — gọi `/health` một lần để đánh thức rồi chạy lại.)

### Thiết kế

| Tiêu chí | Cách làm |
|---|---|
| Config (`app/config.py`) | Dataclass đọc mọi giá trị bằng `os.getenv`; `validate()` fail-fast nếu `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY` |
| Auth (`app/auth.py`) | `APIKeyHeader("X-API-Key")`; thiếu **hoặc** sai → 401 (so sánh bằng `hmac.compare_digest`); không có key trên server → từ chối tất cả. Redis chỉ lưu SHA-256 rút gọn của key, không lưu key thô |
| Rate limit (`app/rate_limiter.py`) | Sliding window bằng Redis sorted set trong 1 transaction `ZREMRANGEBYSCORE → ZADD → ZCARD → EXPIRE`; vượt → xoá entry vừa thêm (để client spam không tự khoá mình mãi), trả 429 + `Retry-After` tính từ entry cũ nhất |
| Cost guard (`app/cost_guard.py`) | Như mục 4.4: ngưỡng ngày + tháng, `INCRBYFLOAT` atomic, 402 |
| `/health` vs `/ready` | `/health` không chạm Redis (Redis chết mà restart agent thì vô ích) → luôn 200. `/ready` ping Redis (timeout 1 s) → 503 khi mất Redis hoặc đang shutdown, để LB ngừng gửi traffic |
| Lỗi | Validation → 422 `{"error":"Invalid request","details":[...]}`; Redis lỗi giữa chừng → 503 + `Retry-After`; lỗi khác → 500 `{"error":"Internal server error"}`. Stack trace chỉ ghi vào log JSON, không trả cho client |
| Log | `JsonFormatter` gắn vào root logger: mỗi dòng 1 JSON (`ts`, `lvl`, `event`, `path`, `status`, `ms`, `request_id`, token/cost); không log key hay nội dung câu hỏi (chỉ độ dài) |
| Graceful shutdown | Dùng `lifespan` của FastAPI và **không** tự đăng ký handler SIGTERM → uvicorn ngừng nhận kết nối mới, chờ request đang chạy xong, rồi chạy phần shutdown (đặt not-ready, đóng Redis) → exit 0 |
| Dockerfile | 2 stage (`builder` tạo venv `/opt/venv`, `runtime` `python:3.11-slim` chỉ copy venv + `app/` + `utils/`), user `app` non-root, `HEALTHCHECK` theo `$PORT`, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"` → uvicorn là PID 1 |
| Compose | `agent` (build `.`, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis: service_healthy`, `.env` tuỳ chọn) + `redis:7-alpine` có healthcheck và volume AOF |

Kiểm tay thêm bằng `docker compose up` (agent + Redis): câu hỏi rỗng / thiếu field / > 2000 ký tự → 422; 10 request thì request thứ 11 → 429; `docker compose stop redis` → `/health` 200, `/ready` 503 `{"reason":"redis unavailable"}`, `/ask` 503 `State store (Redis) unavailable`.

---

## AI log

Toàn bộ lab được làm cùng **Claude Code** (Anthropic, model Claude Opus 5.5) chạy trong terminal trên máy tôi, ở chế độ agent: AI đọc đề, chạy lệnh, viết code và tài liệu; tôi giao việc, làm các bước cần tài khoản (Render) và duyệt kết quả. Mọi con số trong bài đều lấy từ lệnh đã chạy thật trên máy này (output được dán nguyên văn), không có số ước lượng.

| Phần | Tôi yêu cầu | AI đã làm | Tôi / AI kiểm tra thế nào |
|---|---|---|---|
| Môi trường | Làm theo đề | Phát hiện máy không có distro WSL2 nên `check_production_ready.py` sẽ không test được SIGTERM; dựng container Linux `python:3.12-slim` (`--network host`, mount Docker socket) để chạy mọi lệnh Python với `os.name == "posix"` | `python -c "import os; print(os.name)"` → `posix`; checker chạy đủ phần graceful shutdown |
| Part 1, 2, 4, 5 | Chạy các exercise và trả lời câu hỏi | Chạy develop/production app, build image, compose stack, test curl 401/403/200/429, SIGTERM khi đang có request, scale 3 instance; viết câu trả lời và bảng so sánh | Output dán nguyên văn; kích thước image lấy từ `docker images` / `docker image inspect`. AI chỉ ra thêm 2 lỗi nhỏ trong code mẫu (logging của Part 1 bị nuốt mức INFO; `budget_remaining_usd` thực ra là số đã dùng) |
| Part 6 | Hoàn thành các TODO | Viết `config.py`, `auth.py`, `redis_client.py`, `rate_limiter.py` (sliding window Redis ZSET), `cost_guard.py` (Redis INCRBYFLOAT ngày + tháng), `main.py` (JSON log, lifespan, error handler), Dockerfile multi-stage, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | `check_production_ready.py` bản đầy đủ → PASS 12/12 (43/43). Không sửa script chấm. Test tay thêm: 422 cho input sai, 429 ở request thứ 11, tắt Redis → `/health` 200, `/ready` 503. Sửa compose để `AGENT_API_KEY` được truyền từ shell/.env sau khi test tay thấy key không vào container |
| Part 3 | Deploy Render | Viết Blueprint, commit + push; tôi tạo Blueprint trên Render Dashboard và lưu URL + key vào `06-lab-complete/.env.render` (gitignored) | AI chạy 4 lệnh CP6 chỉ in mã HTTP (200/200/401/200) và kiểm tra key không xuất hiện trong output hay trong repo (`git grep` → không có) |
| Part 7 Bước 1 | Load test, eval, sửa KB, CI | Thêm metric `agent_question_length_chars`; chạy 7 load test + 3 eval; thêm 2 entry KB (JWT, HPA) ở cả develop và production; viết workflow `eval-quality-gate.yml` | Eval cuối 23/23, 0 regression, gate PASS; giả lập job CI: v2 exit 0, v1 exit 1. Không sửa `golden_dataset.jsonl` |
| Part 7 Bước 2 | Observability stack | Sửa Dockerfile agent (thiếu `PYTHONUSERBASE` → crash-loop); chạy Locust headless scale 1/3; chạy 5 thí nghiệm A–E và đo thời gian tới firing bằng cách poll Prometheus API; chụp Grafana/Jaeger/Prometheus bằng Chromium headless (Playwright) | Số liệu ghi vào `benchmark-results/experiments_raw.txt`; mọi số trong `BENCHMARK_REPORT.md` đối chiếu với JSON/CSV. Ghi rõ các điểm lệch so với README (cost alert firing ngay ở tải nền, D không có 504) thay vì "chỉnh" cho khớp |
| Sự cố | — | Ổ C: đầy làm Docker Desktop sập giữa chừng; AI (với sự đồng ý của tôi) chuyển ổ đĩa Docker 41 GB sang E: và tạo junction, khởi động lại container của tôi | Docker chạy lại với đủ image/container; kiểm tra kích thước file trước khi xoá bản gốc |

Trước khi nộp tôi tự: đọc lại code `06-lab-complete/app/` để giải thích được từng phần khi Lab Coach hỏi, mở URL Render trên trình duyệt, chụp `dashboard.png` / `running.png` trên Render Dashboard, và đối chiếu các bảng trong `BENCHMARK_REPORT.md` với file report.
