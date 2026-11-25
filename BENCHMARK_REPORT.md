# Benchmark & Observability Report — Day 14

> Mọi con số dưới đây lấy từ file report đã nộp: `reports/*.json` (load test, eval), `benchmark-results/scale_*_stats.csv` (Locust), `benchmark-results/experiments_raw.txt` (số đo Prometheus/Jaeger khi làm thí nghiệm) và ảnh trong `screenshots/`.

**Họ tên:** Phan Sỹ Hùng
**Ngày chạy:** 2026-11-25 (giờ UTC trong bài)
**Môi trường:** Laptop Intel Core i5-9300H, Windows 11 + Docker Desktop 29.3 (WSL2, 8 CPU / 7.7 GB RAM cho VM). Python 3.12 chạy trong container Linux (`--network host`); agent Part 7 develop chạy 1 process uvicorn/phiên bản.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~150–250 ms/câu (p50 ≈ 190–200 ms) nên 500 ms chừa ~2× headroom cho retrieval + mạng. Đặt theo **p95** chứ không theo avg vì ~3 % request bị chậm 1.5 s (tail latency) mà avg che mất. Alert production lỏng hơn (p95 > 1 s trong 2 phút) để không báo động vì spike ngắn |
| Error rate `/ask` | < 2 % | Upstream mock lỗi ~1 % (`MOCK_ERROR_RATE=0.01`) → SLO 2 % để lỗi nền nằm trong error budget; alert ở 5 % trong 1 phút |
| Accuracy trên golden dataset | ≥ 85 % (gate CI), mục tiêu 100 % | v2 ban đầu 91.3 %; sau khi sửa KB đạt 100 %. Gate CI dùng `--min-accuracy 0.85` **và** không được có regression so với baseline `eval_v2_fixed.json` |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 request mỗi mức)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.61 | 194.1 | 221.5 | 1693.6 | 0.67 % | ✅ PASS |
| 10 | 39.77 | 192.4 | 231.5 | 1707.8 | 1.00 % | ✅ PASS |
| 50 | 101.41 | 203.1 | 351.6 | 1695.5 | 0.67 % | ✅ PASS |
| 100 | 65.89 | 341.0 | 2555.1 | 3007.5 | 0.00 % | ❌ FAIL (p95) |

(File: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.)

**Nhận xét:** RPS tăng gần tuyến tính từ 1 → 10 (4.6 → 39.8, ×8.6) vì mock LLM là `asyncio.sleep` nên một process xử lý song song được nhiều request mà latency gần như không đổi (p50 ~ 193 ms). Từ 10 → 50 chỉ tăng ×2.5 (101 req/s) và p95 bắt đầu lên (352 ms). Ở 100 RPS lại **giảm** còn 65.9 và p95 vọt lên 2.6 s: một process Python duy nhất (1 event loop, cả `load_test.py` lẫn agent dùng chung CPU) bị bão hoà — request phải xếp hàng trong event loop/accept queue nên thời gian chờ cộng thẳng vào latency. Nghĩa là năng lực của 1 instance khoảng 50–100 request song song; muốn hơn phải scale ngang (thêm instance) chứ concurrency cao hơn chỉ làm tệ đi. p99 ở mọi mức ≈ 1.7 s là do 3 % request "slow" của mock, không phải do tải.

### 2.2 Tail latency (concurrency 20, 300 request)

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 195.3 | 246.5 | 294.2 |
| 0.03 (mặc định) | 279.2 | 1669.3 | 1713.1 |
| 0.1 | 350.8 | 1689.1 | 1738.1 |

(File: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.)

**Nhận xét:** Từ 0 → 0.1, avg chỉ tăng 1.8× (195 → 351 ms) nhưng p99 tăng 5.9× (294 → 1738 ms) — p95/p99 là chỉ số nhạy nhất vì chúng "nhìn" thẳng vào phần đuôi, còn avg pha loãng vài request 1.7 s với hàng trăm request 190 ms. Ở lần chạy 0.03, p95 cũng đã vọt lên 1669 ms: lần đó tỉ lệ request chậm thực tế vượt 5 % (biến động ngẫu nhiên của mẫu 300 request — kỳ vọng 9 request chậm, độ lệch chuẩn ~3), đủ để đẩy p95 vào vùng chậm; ở c=1 và c=10 (cùng 0.03) p95 chỉ ~225 ms. Đây cũng là lý do SLO phải đặt theo percentile trên mẫu đủ lớn: một user chờ 1.7 s là trải nghiệm thật mà avg 279 ms không cho thấy, và SLO theo avg sẽ "PASS" ngay cả khi 10 % user bị chậm.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 56.9 | 109.0 | 117.1 |
| Tổng cost (USD) | 0.000402 | 0.000760 | 0.000820 |
| Cost / câu đúng (USD) | 0.00040155 | 0.00003619 | 0.00003566 |
| Quality gate | FAIL | PASS (≥ 80 %, 0 regression vs v1) | PASS (≥ 100 %, 0 regression vs v2) |

(File: `reports/eval_v1.json`, `eval_v2.json`, `eval_v2_fixed.json`.) Thêm: chạy v1 với `--baseline reports/eval_v2.json` → 20 câu regression, gate FAIL, **exit code 1**.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` — "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | Không có entry KB nào chứa "jwt" → retrieval rỗng, v2 trả câu chung chung. Thêm entry `(["jwt", "json web token"], ...)` giải thích API key là chuỗi cố định không hết hạn, còn JWT là token có chữ ký số chứa claims + thời điểm hết hạn (`exp`), server chỉ cần kiểm chữ ký |
| `scaling-03` — "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Thêm entry `(["hpa", "autoscaler", "autoscaling", "kubernetes"], ...)`: HPA tăng/giảm số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) |

Sửa trong `KNOWLEDGE_BASE` của cả `develop/utils/mock_llm.py` và `production/utils/mock_llm.py`, không đụng `golden_dataset.jsonl`. Keyword kích hoạt chọn hẹp (chỉ "jwt", "hpa"...) để không cướp retrieval của câu khác — eval cuối 0 regression.

**Quality gate trong CI:** `.github/workflows/eval-quality-gate.yml` chạy trên mỗi PR đụng tới `develop/`: bật agent v2 (`MOCK_ERROR_RATE=0` để không flaky), chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`. `run_eval.py` trả exit code 1 khi accuracy dưới ngưỡng hoặc có regression → step fail → job fail; bật branch protection "Require status checks" cho `main` là PR không merge được. Đã giả lập job: v2 → exit 0, v1 → exit 1.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:** v2 (có retrieval) tốn gấp ~1.9× token/câu (57 → 109) và tổng cost gần gấp đôi, latency thêm bước retrieval ~20 ms (p95 eval 213 → 223 ms). Nhưng accuracy từ 4 % lên 91 % nên **cost / câu đúng giảm 11×** (0.000402 → 0.0000362 USD). So theo "cost / câu" sẽ kết luận sai rằng v1 rẻ hơn; so theo "cost / câu đúng" mới thấy v1 tốn tiền mà gần như không tạo ra giá trị. Bản sửa KB tăng thêm 8 token/câu (prompt có thêm context) và đưa cost/câu đúng xuống nhẹ (0.0000357).

---

## 4. Observability stack (production)

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (30 user Locust, 2 instance): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png) — Traffic 18.9 req/s, Error rate 0.58 %, p95 293 ms, 2 instance up. Thêm [`grafana-incident-AB.png`](screenshots/grafana-incident-AB.png) lúc đang chạy thí nghiệm A+B.
- [x] Một trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với 2 alert cùng **firing** (`AgentHighErrorRate` + `AgentHighLatencyP95`): [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png)

**Trả lời Exercise 7.4** (Prometheus, 30 user, 2 instance, v2 small):
1. p95 = 0.294 s, avg = 0.237 s, p99 = 1.84 s. p95 và avg gần nhau → phần lớn request đồng đều ~200 ms; khoảng cách lớn nằm ở **p99** (gấp ~8× avg) — đó là ~3 % request chậm 1.5 s của mock. Avg không phản ánh được trải nghiệm của nhóm đó.
2. Có: `Requests/s theo instance` = 9.16 vs 9.49 req/s — Nginx round-robin chia gần đều (chênh ~3 % do request chậm giữ kết nối lâu hơn ở một bên).
3. Input 1465 tokens/s > output 781 tokens/s vì v2 nhét system prompt + tài liệu retrieve vào prompt (~80 token input/câu) trong khi câu trả lời là một đoạn ngắn (~45 token). Với RAG, prompt luôn dài hơn câu trả lời — và vì output đắt hơn input 4× nên cả hai đều phải theo dõi.
4. `curl http://localhost:8080/metrics` → **404**. `nginx.conf` có `location = /metrics { return 404; }`: metrics lộ route, lưu lượng, version, cost nên chỉ Prometheus được scrape trực tiếp từng instance trong mạng Docker nội bộ, không public qua cổng ngoài.

Ghi chú khi dựng stack: Dockerfile agent của stack gốc bị crash-loop (`No module named 'uvicorn'`) vì user `agent` có HOME là `/app` nên Python tìm site-packages ở `/app/.local`, trong khi packages được copy vào `/home/agent/.local`. Đã sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local`. Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.2 Thí nghiệm "phá hệ thống"

Đo bằng cách poll `GET /api/v1/alerts` của Prometheus mỗi 2 s, tính từ lúc chạy `docker compose up -d` với cấu hình mới (`experiments_raw.txt`).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | pending sau 30 s, **firing sau 88 s** (`for: 1m`) | Error rate (17.4 %), Requests/s theo status code (503 tăng), LLM calls theo kết quả (error) |
| B | `MOCK_SLOW_RATE=0.3` (giữ A) | `AgentHighLatencyP95` | pending sau 28 s, **firing sau 148 s** (`for: 2m`) | Latency p50/p95/p99: p95 1.90 s, p99 1.98 s nhưng avg chỉ 0.59 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | pending sau 27 s, **firing sau 146 s** (`for: 2m`) | Cost burn rate (USD/giờ): 12.2 $/h (rate 1m) ở 10 user, p95 lên 0.70 s |
| D | `docker pause` 1 instance | `AgentInstanceDown` | pending sau 11 s, **firing sau 41 s** (`for: 30s`) | Instances up (2 → 1); `up{instance="172.27.0.3:8000"} = 0`. Error rate của agent vẫn xanh (0.6 %) |
| E | `AGENT_VERSION=v1` | Không có alert SLO nào | — | Bảng "Phiên bản đang chạy" đổi sang v1; Retrieval hit ratio mất; input tokens/call 80 → 36.5, tokens/s giảm; error 1.7 %, p95 0.198 s — mọi thứ "xanh hơn" |

Ghi chú quan trọng:
- **`LLMCostBurnRateHigh` firing ngay ở trạng thái bình thường**: với 30 user, model small vẫn đốt ~2.4–2.5 $/h > ngưỡng 2 $/h. Ngưỡng cost đang đặt thấp hơn tải bình thường → alert này "kêu" liên tục và sẽ bị bỏ qua (alert fatigue). Để đo thí nghiệm C sạch, tôi hạ Locust xuống 10 user (cost nền ~1.8 $/h → alert inactive) rồi mới đổi model. Production nên đặt ngưỡng cost theo budget thật (vd 1.5× mức chi tiêu bình thường) hoặc theo cost/request thay vì tuyệt đối.
- **Thí nghiệm D không có 504** như README dự đoán: Nginx đếm 0 dòng 504, nhưng Locust ghi nhận `max = 10004 ms`. Lý do: `proxy_read_timeout 10s` + `proxy_next_upstream error timeout` → Nginx chờ instance bị pause đủ 10 s rồi gửi lại sang instance còn sống, nên user nhận 200 **sau 10 giây** chứ không nhận lỗi. Metric của agent không thấy gì (request chưa từng tới instance sống trong 10 s đó) → cần giám sát ở tầng LB (latency upstream của Nginx) hoặc synthetic check.
- Cách đo của tôi bắt đầu từ lúc recreate container nên đã gồm ~5–10 s khởi động lại agent; phần còn lại là cửa sổ `rate([1m])` cần thời gian tích đủ dữ liệu xấu + thời gian `for`.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Lỗi chất lượng không làm metric hạ tầng đỏ — thậm chí v1 còn nhanh và rẻ hơn. Phải đo **chất lượng** trực tiếp: (1) chạy eval golden dataset trong CI trước khi deploy (gate của tôi chặn đúng trường hợp này: v1 → 20 regression, exit 1), (2) online eval: lấy mẫu vài % request thật chấm bằng LLM-as-judge / so keyword, export thành metric `agent_answer_quality` và alert khi giảm, (3) theo dõi metric proxy của chất lượng: retrieval hit ratio (rơi về 0 ở E), số token context/câu, tỉ lệ câu trả lời fallback chung chung, feedback 👍/👎 của user, (4) alert khi `agent_info{version}` đổi ngoài quy trình deploy.

### 4.3 Debug bằng trace

Trace chậm nhất tìm được trong 3 phút tải (Jaeger API v3, `duration ≥ 1 s`, sắp xếp theo thời gian):

- `trace_id`: `959eb1b1972c3d9977a605ecf7752d96`
- Tổng thời gian: 1753.4 ms
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1675.4 ms, 95.6 %). `agent.run` 1702.1 ms (bao span LLM), `agent.retrieve_context` chỉ 26.5 ms (1.5 %) → chậm nằm ở upstream LLM, không phải code agent hay retrieval.
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-25T08:56:38.866933+00:00", "level": "INFO", "service": "observable-agent", "instance": "d6dbd25f29da", "event": "llm_call", "trace_id": "959eb1b1972c3d9977a605ecf7752d96", "span_id": "0ec1882f24db9ca3", "request_id": "59717af4d544", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 81, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1674.6}
{"ts": "2026-11-25T08:56:38.867474+00:00", "level": "INFO", "service": "observable-agent", "instance": "d6dbd25f29da", "event": "http_request", "trace_id": "959eb1b1972c3d9977a605ecf7752d96", "span_id": "0ec1882f24db9ca3", "request_id": "59717af4d544", "method": "POST", "route": "/ask", "status": 200, "ms": 1752.8}
```

Trace lỗi: `eb7f9efc0a46b6be4dc1bbbc26e55b18` (POST /ask → 503, 93.2 ms). Span `chat mock-llm-small` có `error.type = upstream_unavailable` và event `exception` (`utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`). Log tương ứng:

```json
{"ts": "2026-11-25T08:56:51.425102+00:00", "level": "INFO", "service": "observable-agent", "instance": "d6dbd25f29da", "event": "llm_error", "trace_id": "eb7f9efc0a46b6be4dc1bbbc26e55b18", "span_id": "2daf5f3e459f3483", "request_id": "1662172c9916", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Số của dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.85 | 200 | 270 | 1700 | 82 / 7100 (1.15 %) |
| 3 | 60.27 | 190 | 230 | 1700 | 77 / 7201 (1.07 %) |

**Giải thích kết quả:** Scale 1 → 3 instance gần như **không** đổi RPS (59.9 → 60.3) và chỉ cải thiện p95 một chút (270 → 230 ms); p99 giữ nguyên 1.7 s. Lý do:
1. **Tải bị giới hạn bởi chính Locust (closed-loop):** 100 user, mỗi user nghỉ 0.5–2 s (trung bình 1.25 s) giữa các request → trần lý thuyết ≈ 100 / (1.25 + 0.2) ≈ 69 req/s tổng (≈ 60 `/ask` + 6 `/health` như đo được). Một instance đã phục vụ đủ mức này nên thêm instance không tạo thêm throughput.
2. **Nút thắt latency là LLM upstream**, không phải CPU của agent: mỗi request tốn ~190 ms chờ LLM (+3 % chờ 1.7 s) dù chạy trên bao nhiêu instance — đó là lý do p50 và p99 không đổi. p95 giảm nhẹ vì với 3 instance mỗi instance (giới hạn 0.5 CPU) ít phải xếp hàng hơn.
Muốn thấy lợi ích của scale ngang phải tăng tải tới khi 1 instance bão hoà (như c=100 ở mục 2.1, p95 2.6 s) — lúc đó 3 instance sẽ chia tải và giữ p95 thấp. Còn tail latency do LLM thì scale không giải quyết được; cần timeout + retry/hedging, cache, hoặc model/provider nhanh hơn.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Percentile kể sự thật, average thì không:** ở `MOCK_SLOW_RATE=0.1` avg chỉ 351 ms trong khi p99 là 1.74 s; SLO và alert phải viết theo p95/p99 và trên mẫu đủ lớn (23 câu eval không đủ để nói về latency).
  2. **Metric hạ tầng không thấy lỗi chất lượng, và không thấy cả những gì không chạm tới service:** v1 làm accuracy rơi từ 100 % xuống 4 % mà dashboard xanh hơn; instance bị pause tạo ra request 10 s ở Nginx mà Error rate của agent vẫn 0.6 %. Cần eval/quality gate và giám sát ở tầng LB/synthetic bên cạnh metrics.
  3. **Ba trụ cột phải nối được với nhau:** dashboard cho biết p95 tăng → Jaeger chỉ ra 95.6 % thời gian nằm ở span LLM → `trace_id` trong log JSON cho đúng request, model, token, cost. Và alert cũng phải được kiểm thử (`promtool`) và **tinh chỉnh theo tải thật** — ngưỡng cost 2 $/h của bài kêu ngay ở tải bình thường.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - Metric chất lượng online (`agent_answer_quality` từ sampling + LLM-as-judge, `retrieval_hit_ratio`) và alert khi giảm so với baseline; alert khi `agent_info{version}` thay đổi ngoài pipeline.
  - Latency/5xx/504 **ở Nginx** (exporter hoặc log-based metric) + synthetic probe từ bên ngoài gọi `/ask` mỗi phút.
  - Alert **burn rate nhiều cửa sổ** (1h + 5m) cho error budget 30 ngày thay vì ngưỡng tuyệt đối; cost alert theo budget ngày (`increase(llm_cost_usd_total[1d])` so với `DAILY_BUDGET_USD`) và theo cost/request.
  - Saturation: `agent_in_flight_requests` / CPU / event-loop lag per instance, alert khi gần mức bão hoà đo được (~50–100 request song song/instance); số lần 429/402 của rate limiter & cost guard (Part 6) để biết user bị chặn.
  - LLM upstream: tỉ lệ timeout/429 của provider, p99 `llm_request_duration_seconds` theo model — để quyết định retry/fallback model.
