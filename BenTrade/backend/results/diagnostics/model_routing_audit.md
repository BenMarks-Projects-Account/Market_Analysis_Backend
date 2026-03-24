# Model Routing & Load Balancing — Current State Audit

**Generated:** 2026-03-23  
**Scope:** Full trace of model routing, provider management, and load balancing across all backend modules.

---

## 1. Provider Registry

### 1.1 Configured Providers

| # | Provider ID | Type | Endpoint | Model ID | Status |
|---|-------------|------|----------|----------|--------|
| 1 | `localhost_llm` | Local HTTP (LM Studio) | `http://localhost:1234/v1/chat/completions` | (LM Studio loaded model) | ✅ Enabled |
| 2 | `network_model_machine` | Local HTTP (LM Studio) | `http://192.168.1.143:1234/v1/chat/completions` | (LM Studio loaded model) | ✅ Enabled |
| 3 | `bedrock_titan_nova_pro` | AWS Bedrock (Converse API) | AWS SDK (region-based) | `us.amazon.nova-pro-v1:0` | ✅ Enabled (default) |

**Adapter Classes:**

| Provider ID | Adapter Class | File |
|-------------|--------------|------|
| `localhost_llm` | `LocalhostLLMProvider` | `app/services/model_provider_adapters.py` |
| `network_model_machine` | `NetworkModelMachineProvider` | `app/services/model_provider_adapters.py` |
| `bedrock_titan_nova_pro` | `BedrockTitanNovaProProvider` | `app/services/model_provider_adapters.py` |

### 1.2 Configuration Sources

**Endpoint URLs** — hardcoded in `app/model_sources.py`:
```python
MODEL_SOURCES = {
    "local":           {"name": "Local",          "endpoint": "http://localhost:1234/v1/chat/completions",     "enabled": True},
    "model_machine":   {"name": "Model Machine",  "endpoint": "http://192.168.1.143:1234/v1/chat/completions","enabled": True},
    "premium_online":  {"name": "Premium Online",  "endpoint": None,                                         "enabled": False},
}
```

**Bedrock Settings** — environment variables via `app/config.py`:

| Setting | Env Var | Default |
|---------|---------|---------|
| Bedrock enabled | `BEDROCK_ENABLED` | `true` |
| Region | `BEDROCK_REGION` / `AWS_DEFAULT_REGION` | `us-east-1` |
| Model ID | `BEDROCK_MODEL_ID` | `us.amazon.nova-pro-v1:0` |
| Timeout | `BEDROCK_TIMEOUT_SECONDS` | `120` seconds |

**Routing Settings** — environment variables via `app/services/model_routing_config.py`:

| Setting | Env Var | Default |
|---------|---------|---------|
| Routing enabled | `ROUTING_ENABLED` | `true` |
| Default max concurrency | `ROUTING_DEFAULT_MAX_CONCURRENCY` | `1` |
| localhost_llm concurrency | `ROUTING_CONCURRENCY_LOCALHOST_LLM` | `1` |
| network_model_machine concurrency | `ROUTING_CONCURRENCY_NETWORK_MODEL_MACHINE` | `1` |
| bedrock_titan_nova_pro concurrency | `ROUTING_CONCURRENCY_BEDROCK_TITAN_NOVA_PRO` | `1` |
| Probe timeout | `ROUTING_PROBE_TIMEOUT_SECONDS` | `3.0` seconds |
| Probe degraded threshold | `ROUTING_PROBE_DEGRADED_THRESHOLD_MS` | `2000.0` ms |

**General Model Settings** — `app/config.py`:

| Setting | Env Var | Default |
|---------|---------|---------|
| Model inference timeout | `MODEL_TIMEOUT_SECONDS` | `180` seconds |

**Runtime State** — `data/runtime_config.json`:
```json
{
  "model_source": "local",
  "execution_mode": "local_distributed"
}
```

**No `.env` file** exists in the workspace — all env vars are set via system environment or defaults.

---

## 2. Provider Adapters

### 2.1 Adapter Class Hierarchy

```
ModelProviderBase (ABC)              ← app/services/model_provider_base.py
├── LocalhostLLMProvider             ← app/services/model_provider_adapters.py
├── NetworkModelMachineProvider      ← app/services/model_provider_adapters.py
└── BedrockTitanNovaProProvider      ← app/services/model_provider_adapters.py
```

All three are registered in a singleton `ProviderRegistry` via `_build_default_registry()` in `app/services/model_provider_registry.py`.

### 2.2 Inference Methods

**LM Studio Providers** (LocalhostLLMProvider + NetworkModelMachineProvider):

Both delegate to the shared `_openai_compat_call()` function:

```
execute(request) →
  1. get_provider_endpoint(provider_id) → URL from MODEL_SOURCES
  2. If no endpoint → ProviderResult(error_code="no_endpoint", UNAVAILABLE)
  3. _openai_compat_call(endpoint, request, timeout, provider_id) →
     a. Build messages: [system_prompt] + request.prompt messages
     b. Body: {"messages": [...], "stream": false, "model": request.model_name}
     c. Apply routing_overrides (max_tokens, temperature)
     d. POST endpoint with timeout
     e. On 429: retry up to 3× with exponential backoff (2s, 4s, 8s, cap 30s)
     f. On success: extract_content_from_openai_response(data)
     g. On ReadTimeout → TIMEOUT status, DEGRADED state
     h. On ConnectionError → FAILED status, UNAVAILABLE state
     i. On 429 exhausted → FAILED status, BUSY state, error_code="rate_limited"
     j. On other RequestException → FAILED status, FAILED state
```

**Bedrock Provider** (BedrockTitanNovaProProvider):

```
execute(request) →
  1. If BEDROCK_ENABLED=false → SKIPPED status, UNAVAILABLE state
  2. _ensure_client() → lazy boto3 bedrock-runtime client
  3. If no client → FAILED status, error_code="bedrock_client_unavailable"
  4. _build_bedrock_messages(request) → (messages, system_prompts)
     - System messages extracted to separate system_prompts list
     - User/assistant messages in Converse format: {"role": ..., "content": [{"text": ...}]}
  5. client.converse(modelId=..., messages=..., system=...) with botocore Config(read_timeout)
  6. On ThrottlingException: retry up to 3× with exponential backoff (same as LM Studio)
  7. On success: _extract_content_from_converse_response() → response.output.message.content[0].text
  8. Error classification via _classify_bedrock_error(exc):
     - ThrottlingException → BUSY
     - ServiceUnavailableException → UNAVAILABLE
     - ModelTimeoutException → DEGRADED
     - AccessDeniedException → FAILED
     - ValidationException → AVAILABLE (bad input, not provider fault)
     - ModelNotReadyException → UNAVAILABLE
     - Connection/timeout errors → DEGRADED
```

### 2.3 Error Handling Summary

| Error Type | LM Studio Response | Bedrock Response | Provider State |
|------------|-------------------|------------------|----------------|
| HTTP 429 / Throttling | Retry 3× (2/4/8s) then FAILED | Retry 3× (2/4/8s) then FAILED | BUSY |
| Connection refused | FAILED immediately | FAILED immediately | UNAVAILABLE |
| Read timeout | FAILED immediately | FAILED immediately | DEGRADED |
| HTTP 500 / Server error | FAILED immediately | FAILED immediately | FAILED |
| Auth error | FAILED immediately | FAILED immediately | FAILED |
| Validation error | N/A | FAILED immediately | AVAILABLE |

### 2.4 Retry Constants ✅

```
_RATE_LIMIT_MAX_RETRIES = 3        # Total attempts: 4 (0..3)
_RATE_LIMIT_BACKOFF_BASE = 2.0     # 2^0=2s, 2^1=4s, 2^2=8s
_RATE_LIMIT_BACKOFF_CAP = 30.0     # Max delay per retry
```

Retry only triggers on HTTP 429 or Bedrock `ThrottlingException`. All other errors fail immediately.

---

## 3. Router Logic

### 3.1 Execution Modes

| Mode | Candidates (in order) | Behavior |
|------|----------------------|----------|
| `local` | `[localhost_llm]` | Single provider, no fallback |
| `model_machine` | `[network_model_machine]` | Single provider, no fallback |
| `premium_online` | `[bedrock_titan_nova_pro]` | Single provider, no fallback |
| `local_distributed` | `[localhost_llm, network_model_machine]` | ✅ Fallback: try local → try network |
| `online_distributed` | `[localhost_llm, network_model_machine, bedrock_titan_nova_pro]` | ✅ Full fallback chain |

**Current active mode:** `local_distributed`

### 3.2 Selection Algorithm ✅

```
route_and_execute(request):
  1. RESOLVE overrides (premium_override > override_mode > preferred_provider > base mode)
  2. GET candidate list from mode → DEFAULT_PROVIDER_ORDER
  3. ROTATE candidates (round-robin for distributed modes, thread-safe counter)
  4. PROBE all candidates once (cached per routing cycle)
  5. FOR EACH candidate in rotated order:
     a. Check REGISTERED → skip if unknown
     b. Check ELIGIBLE:
        - Must be configured
        - Not UNAVAILABLE or FAILED state
        - Not BUSY state
        - Has capacity in gate (in_flight < max_concurrency)
        - DEGRADED is eligible (warned but accepted)
     c. ACQUIRE gate slot → if denied, skip
     d. EXECUTE via adapter.execute(request)
     e. If SUCCESS → return result
     f. If RETRYABLE failure (connection/timeout/not-configured) → release slot, try next
     g. If NON-RETRYABLE failure → release slot, return failure
  6. If ALL candidates at capacity → wait_for_any_capacity (up to 3 attempts)
  7. Return (result | None, ExecutionTrace with full decision log)
```

### 3.3 Override Precedence

```
Resolution (highest to lowest priority):
  1. premium_override=True    → forces "premium_online" mode + [bedrock]
  2. override_mode="X"        → forces mode X's candidate order
  3. preferred_provider="Y"   → moves Y to front of candidate list
  4. base request.mode        → default candidate order
```

### 3.4 Round-Robin Rotation ✅

For distributed modes with >1 candidate, a thread-safe rotation counter ensures even distribution:

```python
_rotation_counter: int = 0
_rotation_lock = threading.Lock()

def _rotate_candidates(candidates):
    # Rotates by _rotation_counter % len(candidates)
    # Prevents first candidate from always being tried first
```

### 3.5 Fallback Chain

```
         ┌─────────────┐     ┌────────────────────┐     ┌──────────────────────┐
         │ localhost_llm │──→──│ network_model_machine│──→──│ bedrock_titan_nova_pro│
         └─────────────┘     └────────────────────┘     └──────────────────────┘
              ▲                       ▲                         ▲
           Probe first            Probe first              Probe first
           Check gate             Check gate               Check gate
           Execute                Execute                  Execute
              │                       │                         │
         On retryable             On retryable             Final result
         failure → next           failure → next           (success or fail)
```

In `local_distributed` mode, only the first two providers are candidates. In `online_distributed`, all three are tried.

---

## 4. Busy State Management

### 4.1 Concurrency Gate ✅

**Implementation:** `ProviderExecutionGate` in `app/services/model_execution_gate.py`

**Mechanism:**
- Per-provider `in_flight` counters (dict)
- Per-provider `max_concurrency` limits (dict, default=1)
- Thread-safe via `threading.Lock` + `threading.Condition`

```python
class ProviderExecutionGate:
    _in_flight: dict[str, int]        # Current concurrent requests
    _max_concurrency: dict[str, int]  # Per-provider limits
    _lock: threading.Lock
    _condition: threading.Condition   # For wait_for_any_capacity

    def acquire(provider_id) -> bool:
        # Atomic: if in_flight < max → increment, return True
        # Otherwise: return False (no blocking)

    def release(provider_id) -> None:
        # Decrement; notify_all() on condition for waiting threads

    def reservation(provider_id):
        # Context manager: acquire → yield → release (in finally)

    def wait_for_any_capacity(provider_ids, timeout) -> bool:
        # Block on condition variable until ANY provider has a free slot
        # Returns True if capacity found, False on timeout
```

**Slot Lifecycle:**
```
Provider idle (in_flight=0)
  → route_and_execute calls gate.acquire("localhost_llm")
  → in_flight=1, slot acquired
  → adapter.execute() called
  → on completion: gate.release("localhost_llm")
  → in_flight=0, condition.notify_all()
```

### 4.2 Provider States ✅

| State | Meaning | Eligible? | Set By |
|-------|---------|-----------|--------|
| `AVAILABLE` | Healthy & responsive | ✅ Yes | Successful probe |
| `DEGRADED` | Slow but responding | ✅ Yes (warned) | Probe response > 2000ms |
| `BUSY` | Rate-limited (429) | ❌ No | 429 response or ThrottlingException |
| `UNAVAILABLE` | Not reachable | ❌ No | Connection refused, not configured |
| `FAILED` | Server error | ❌ No | 500-level error, auth failure |

**State is transient** — determined fresh on each probe. No persistent busy marking. No cooldown timer.

### 4.3 Probe Mechanism ✅

**LM Studio probes** (`_lmstudio_probe()`):
- Target: `GET /v1/models` (derived from chat completions URL)
- Timeout: 3 seconds (configurable via `ROUTING_PROBE_TIMEOUT_SECONDS`)
- State classification:
  - HTTP 200 + response < 2000ms → **AVAILABLE**
  - HTTP 200 + response >= 2000ms → **DEGRADED**
  - HTTP 429 or 503 → **BUSY**
  - HTTP 4xx/5xx (other) → **FAILED**
  - ConnectionError → **UNAVAILABLE**
  - ReadTimeout → **UNAVAILABLE**

**Bedrock probe** (`BedrockTitanNovaProProvider.probe()`):
- **Config-only** — no live inference or API call
- Checks: `BEDROCK_ENABLED` flag + boto3 client creation success
- Returns AVAILABLE if configured, UNAVAILABLE otherwise
- Metadata includes: `"probe_type": "config_only"`

⚠️ **Gap:** Bedrock probe does NOT verify actual endpoint reachability or model availability. A config-only check means Bedrock appears "AVAILABLE" even if the service is down or credentials are expired.

---

## 5. Health & Monitoring

### 5.1 Health Probes

**Per-routing-cycle probes:**
- Before each request, all candidates are probed once (via `_RoutingCycleProbeCache`)
- Results cached within the cycle — each provider probed at most once per request
- LM Studio: live HTTP probe to `/v1/models`
- Bedrock: config-only check

**Legacy health service** (`app/services/model_health_service.py`):
- Probes active model source's `/v1/models` endpoint
- 10-second cache TTL
- Returns: status, latency_ms, models_loaded, endpoint
- Only checks the **currently selected** source (not all providers)

**No periodic background probing.** Probes run only when a request is routed.

### 5.2 Logging & Observability ✅

**Structured event logging** via `app/services/model_routing_telemetry.py`:

| Event | Logger | Level | Key Fields |
|-------|--------|-------|-----------|
| `[route:start]` | `bentrade.routing.telemetry` | INFO | mode, candidates, task_type, overrides |
| `[route:probe]` | `bentrade.routing.telemetry` | INFO | provider, state, timing_ms, probe_type |
| `[route:skip]` | `bentrade.routing.telemetry` | INFO | provider, skip_reason, gate in_flight/max |
| `[gate:acquired]` | `bentrade.routing.gate` | INFO | provider |
| `[gate:denied]` | `bentrade.routing.gate` | WARNING | provider, in_flight, max_concurrency |
| `[route:dispatch]` | `bentrade.routing.telemetry` | INFO | provider, state |
| `[route:success]` | `bentrade.routing.telemetry` | INFO | provider, timing_ms |
| `[route:failed]` | `bentrade.routing.telemetry` | WARNING | provider, error_code, retryable |
| `[route:complete]` | `bentrade.routing.telemetry` | INFO | status, provider, fallback, skip_summary |

**Safety:** No prompt content is ever logged. Only metadata (message count, approx token chars, presence flags).

### 5.3 Status Endpoints ✅

All mounted at `/api/admin/routing`:

| Method | Endpoint | Purpose | Cooldown |
|--------|----------|---------|----------|
| GET | `/health?refresh=bool` | Per-provider health summaries | 10s |
| GET | `/system` | Global routing config and state | None |
| GET | `/recent?limit=1-50` | Recent trace summaries (newest first) | None |
| GET | `/dashboard?refresh=bool&recent_limit=1-50` | Composite: system + health + traces | 10s |
| GET | `/execution-mode` | Current mode + display label + options | None |
| POST | `/execution-mode` | Update execution mode | 10s |
| POST | `/refresh-config` | Reload config from env, return diff | 10s |
| POST | `/refresh-providers` | Live-probe all providers | 10s |
| POST | `/refresh-runtime` | Full coherent refresh: config → gate → providers | 10s |

### 5.4 Trace Buffer ✅

**In-memory ring buffer** (`app/services/routing_dashboard_service.py`):
- Capacity: 50 traces (oldest evicted on overflow)
- Thread-safe: `threading.Lock` + `collections.deque(maxlen=50)`
- Traces stored via `record_trace()` after every `route_and_execute()` call
- Retrieved via `/api/admin/routing/recent` or `/dashboard`

⚠️ **Gap:** Traces are in-memory only — lost on restart. No persistent trace storage or aggregation (no "last 24h success rate" or per-provider latency percentiles).

---

## 6. Caller Integration

### 6.1 All Callers

The routing system has a **two-layer architecture**:

```
Layer 1: Integration entry points (callers use these)
  ├── execute_routed_model()              ← general-purpose
  ├── routed_tmc_final_decision()         ← TMC with parse-repair
  ├── routed_model_interpretation()       ← MI engines (sync)
  ├── async_routed_model_interpretation() ← MI engines (async)
  └── adaptive_routed_model_interpretation() ← per-request routing toggle

Layer 2: Core routing (called by Layer 1 only)
  └── route_and_execute() → probe → select → gate → execute → trace
```

| # | Caller | File | Routing Function | Mode | Timeout | Failure Handling |
|---|--------|------|-----------------|------|---------|-----------------|
| 1 | API routes (4 endpoints) | `app/api/routes_reports.py` | `_model_transport()` → `execute_routed_model()` | resolved | 180s | Retry + repair; legacy fallback if routing disabled |
| 2 | MI Breadth engine | `app/services/breadth_service.py` | `_model_transport()` → `execute_routed_model()` | resolved | 180s | Retry + repair; legacy fallback |
| 3 | MI Cross-Asset Macro | `app/services/cross_asset_macro_service.py` | `_model_transport()` → `execute_routed_model()` | resolved | 180s | Retry + repair; legacy fallback |
| 4 | MI Flows & Positioning | `app/services/flows_positioning_service.py` | `_model_transport()` → `execute_routed_model()` | resolved | 180s | Retry + repair; legacy fallback |
| 5 | MI News Sentiment | `app/services/news_sentiment_service.py` | `_model_transport()` → `execute_routed_model()` | resolved | 180s | Retry + repair; legacy fallback |
| 6 | MI Liquidity | `app/services/liquidity_conditions_service.py` | `_model_transport()` → `execute_routed_model()` | resolved | 180s | Retry + repair; legacy fallback |
| 7 | MI Volatility | `app/services/volatility_options_service.py` | `_model_transport()` → `execute_routed_model()` | resolved | 180s | Retry + repair; legacy fallback |
| 8 | Active Trade Pipeline | `app/services/active_trade_pipeline.py` | `_routed_model_executor()` → `execute_routed_model()` | `local_distributed` | 120s | Degraded model output (engine-only fallback) |
| 9 | TMC Final Decision | `app/api/routes_tmc.py` / `app/workflows/stock_opportunity_runner.py` | `routed_tmc_final_decision()` | `online_distributed` | 180s | Parse-repair retry → legacy cascade → PASS fallback |
| 10 | Contextual Chat | `app/services/contextual_chat_service.py` | `_model_transport()` | resolved | 30s | Routing disabled → legacy fallback |

### 6.2 Failure Handling Patterns

**Pattern A — Cascade Fallback** (TMC Final Decision):
```
routed_tmc_final_decision()
  → execute_routed_model() with online_distributed
  → on RoutingDisabledError or failure:
     → analyze_tmc_final_decision() (legacy direct call)
     → on parse failure: retry-with-fix (ONE additional LLM call)
     → on total failure: return fallback PASS decision with _fallback=True
```

**Pattern B — Transport Layer Fallback** (MI engines, API routes):
```
_model_transport()
  → execute_routed_model()
  → on RoutingDisabledError or exception:
     → legacy requests.post() to get_model_endpoint()
     → on legacy failure: raise to caller
```

**Pattern C — Dependency Injection** (Active Trade Pipeline):
```
run_model_analysis(packet, engine_out, model_executor=_routed_model_executor)
  → model_executor(packet, engine_out)
  → on any failure: _degraded_model_output(reason)
  → pipeline continues with engine-only recommendation
```

### 6.3 Provider Attribution

| Component | Logs Provider Used? | How? |
|-----------|:---:|------|
| `execute_routed_model()` | ✅ | `ExecutionTrace.selected_provider` |
| `routed_tmc_final_decision()` | ✅ | `ExecutionTrace.selected_provider` in metadata |
| `_model_transport()` (routed path) | ✅ | `TransportResult.provider` |
| `_model_transport()` (legacy path) | ⚠️ | Logs source_key from `model_state` but may show "unknown" |
| `_default_model_executor()` (legacy) | ❌ | No provider tracking |

---

## 7. Current Configuration

### 7.1 Active State

| Setting | Value | Source |
|---------|-------|--------|
| Execution mode | `local_distributed` | `data/runtime_config.json` |
| Legacy model source | `local` | `data/runtime_config.json` |
| Routing enabled | `true` (default) | No env override |
| Bedrock enabled | `true` (default) | No env override |

### 7.2 Active Candidate Order

For `local_distributed` mode:
1. **localhost_llm** → `http://localhost:1234/v1/chat/completions`
2. **network_model_machine** → `http://192.168.1.143:1234/v1/chat/completions`

Bedrock (`bedrock_titan_nova_pro`) is NOT in the candidate list for `local_distributed`. It is only available in `online_distributed` or `premium_online` modes.

### 7.3 Concurrency Limits

All providers: **1 concurrent request** (serialized dispatch per provider).

### 7.4 Provider Setup Details

| Machine | Role | GPU | URL | Software |
|---------|------|-----|-----|----------|
| Machine 1 (localhost) | Primary | (local machine GPU) | `localhost:1234` | LM Studio |
| Machine 2 (network) | Secondary | (network machine GPU) | `192.168.1.143:1234` | LM Studio |
| AWS Bedrock | Tertiary (premium) | N/A (managed) | AWS SDK | Amazon Nova Pro v1 |

---

## 8. Data Flow Diagram

```
  ┌────────────────────────────────────────────────────────────┐
  │                         CALLERS                            │
  │  MI Engines · TMC · Active Trade · API Routes · Chat       │
  └──────────────┬─────────────────────────────────────────────┘
                 │
                 ▼
  ┌────────────────────────────────────────────────────────────┐
  │           INTEGRATION LAYER (Layer 1)                      │
  │                                                            │
  │  execute_routed_model()         ✅ works                    │
  │  routed_tmc_final_decision()    ✅ works (with parse-repair)│
  │  routed_model_interpretation()  ✅ works                    │
  │  _model_transport()             ✅ works (with legacy fallback)|
  │                                                            │
  │  ┌ Routing disabled? ──→ RoutingDisabledError ──→ Legacy ┐│
  │  │                                                        ││
  │  └ Mode resolution (premium > override > preferred > base)┘│
  └──────────────┬─────────────────────────────────────────────┘
                 │
                 ▼
  ┌────────────────────────────────────────────────────────────┐
  │            ROUTING POLICY (Layer 2)                        │
  │                                                            │
  │  route_and_execute()            ✅ works                    │
  │                                                            │
  │  1. Resolve candidate order     ✅ override precedence      │
  │  2. Round-robin rotation        ✅ thread-safe counter      │
  │  3. Probe all candidates        ✅ cached per cycle         │
  │  4. Walk + eligibility check    ✅ per-provider             │
  │  5. Gate acquire                ✅ concurrency control       │
  │  6. Execute via adapter         ✅ dispatch                  │
  │  7. Fallback on retryable fail  ✅ next candidate            │
  │  8. Build ExecutionTrace        ✅ full decision log         │
  └──────────────┬─────────────────────────────────────────────┘
                 │
        ┌────────┼────────┐
        ▼        ▼        ▼
  ┌──────────┐┌──────────┐┌──────────────────┐
  │localhost  ││ network  ││ bedrock_titan    │
  │_llm      ││_model    ││ _nova_pro        │
  │          ││_machine  ││                  │
  │ LM Studio││ LM Studio││ AWS Converse API │
  │ :1234    ││ :1234    ││ us-east-1        │
  │          ││          ││                  │
  │Probe: GET││Probe: GET││Probe: config-    │
  │/v1/models││/v1/models││only (no live)    │
  │          ││          ││                  │
  │429 retry ││429 retry ││Throttle retry    │
  │3× backoff││3× backoff││3× backoff       │
  └──────────┘└──────────┘└──────────────────┘
       ✅           ✅           ⚠️ (config-only probe)
```

---

## 9. Issues & Gaps

### 9.1 Bugs / Race Conditions

| # | Issue | Severity | Detail |
|---|-------|----------|--------|
| 1 | ⚠️ **Bedrock probe is config-only** | Medium | `BedrockTitanNovaProProvider.probe()` only checks if boto3 client initializes — never calls AWS. Provider appears AVAILABLE even if credentials are expired, service is down, or model is unavailable. In `online_distributed` mode, this means Bedrock is always "eligible" but may fail on execution. |
| 2 | ⚠️ **No persistent state between routing cycles** | Low | Provider state (AVAILABLE/BUSY/FAILED) is determined fresh on each probe. If a provider returned 429 and is BUSY, the next request 100ms later will probe again and may get a 200 — or may not. There's no cooldown/backoff window at the routing level (only at the adapter retry level). |
| 3 | ⚠️ **Round-robin counter is in-memory** | Low | `_rotation_counter` resets to 0 on server restart. Not a bug, but first request after restart always hits the first candidate. |

### 9.2 Missing Features

| # | Feature | Impact | Detail |
|---|---------|--------|--------|
| 1 | ❌ **No circuit breaker** | High | A provider that consistently fails (e.g., network machine offline for hours) gets probed and attempted on every single request. No mechanism to "open the circuit" and skip it for a cooldown period. Each routing cycle pays the probe timeout (3s) cost. |
| 2 | ❌ **No persistent trace storage** | Medium | In-memory ring buffer (50 traces) lost on restart. No historical metrics like "last 24h success rate", "avg latency by provider", or "failure trend over time". |
| 3 | ❌ **No cost awareness** | Medium | Bedrock has per-token costs (input + output tokens billed). Local providers are "free" (electricity only). Router has no concept of cost — `online_distributed` will use Bedrock as fallback without noting the cost impact. |
| 4 | ❌ **No request prioritization** | Medium | Active trade analysis (time-sensitive, user-facing) and regime analysis (background, can wait) compete equally for the same provider slots. No priority queuing. |
| 5 | ❌ **No model-specific routing** | Low | Some prompts may produce better results on certain models (e.g., regime analysis on Nova Pro vs local Llama). Router treats all providers as interchangeable for all task types. |
| 6 | ❌ **No latency-based routing** | Low | Router uses round-robin, not latency-weighted. If one provider is consistently 2× faster, it still gets equal share of requests. |
| 7 | ❌ **No periodic background probing** | Low | Probes only run when a request needs routing. If all providers go down between requests, the user's next request pays the discovery cost (probe timeouts) inline. |
| 8 | ❌ **No user-facing degradation notice** | Low | When falling back to a secondary provider, the TMC doesn't show "Using backup model" or "May be slower than usual." The model_summary section shows provider name/latency but isn't highlighted as degraded. |

### 9.3 Enhancement Opportunities

| # | Enhancement | Priority | Rationale |
|---|-------------|----------|-----------|
| 1 | **Circuit breaker with exponential backoff** | High | After N consecutive failures, stop probing a provider for a cooldown period (30s → 60s → 120s). Reduces wasted probe timeout latency. |
| 2 | **Persistent trace/metrics storage** | Medium | Write traces to SQLite or JSON file. Enable "last N hours" reporting, per-provider success rates, latency percentiles. Powers a real operational dashboard. |
| 3 | **Live Bedrock probe** | Medium | Call a lightweight Bedrock Converse request (e.g., "respond with OK") to verify actual reachability. Current config-only probe gives false confidence. |
| 4 | **Cost tracking + budget gate** | Medium | Track token usage per Bedrock call (already available in response metadata: `usage.inputTokens`, `usage.outputTokens`). Add daily cost budget — when exceeded, skip Bedrock. |
| 5 | **Priority queue for task types** | Medium | Let active trade analysis (urgency > 1) preempt regime analysis. Define task_type priority tiers. |
| 6 | **Concurrency > 1 for LM Studio** | Medium | LM Studio can handle multiple concurrent requests (queued internally). Raising max_concurrency from 1 to 2-3 would improve throughput significantly during batch runs (e.g., 6 MI engines + active trade analysis). |
| 7 | **Latency-weighted selection** | Low | Track rolling average latency per provider. Prefer faster provider when both are available. Simple exponential moving average. |
| 8 | **Background health heartbeat** | Low | Every 30s, probe all configured providers in background. Pre-populate probe cache. Eliminates inline probe latency for the first request after idle periods. |
| 9 | **UI degradation banner** | Low | When fallback is used, add `fallback_notice` to API response. TMC displays "⚠️ Using backup provider" with provider name. |
| 10 | **Model-task affinity hints** | Low | Allow routing config to express hints like "prefer bedrock for tmc_final_decision". Optional, not mandatory. |

---

## 10. Component Health Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Provider Registry (singleton) | ✅ Works | 3 providers registered, lazy init |
| Adapter: LocalhostLLMProvider | ✅ Works | Live probe + 429 retry |
| Adapter: NetworkModelMachineProvider | ✅ Works | Live probe + 429 retry |
| Adapter: BedrockTitanNovaProProvider | ⚠️ Partial | Config-only probe, no live health check |
| Router: execute_with_provider() | ✅ Works | Single-provider dispatch |
| Router Policy: route_and_execute() | ✅ Works | Full fallback chain |
| Round-robin rotation | ✅ Works | Thread-safe |
| Execution Gate (concurrency) | ✅ Works | Per-provider semaphore, 1-slot default |
| Integration: execute_routed_model() | ✅ Works | Legacy adaptation layer |
| Integration: routed_tmc_final_decision() | ✅ Works | Parse-repair cascade |
| Integration: _model_transport() | ✅ Works | Legacy fallback if routing disabled |
| Telemetry logging | ✅ Works | Structured events, no secrets |
| Trace buffer (in-memory) | ⚠️ Partial | 50-entry ring buffer, lost on restart |
| Admin API (/api/admin/routing/) | ✅ Works | Health, system, traces, mode control |
| Execution mode state | ✅ Works | Persisted to runtime_config.json |
| Model health service (legacy) | ✅ Works | 10s cache, active source only |
| Circuit breaker | ❌ Missing | No cooldown on repeated failures |
| Persistent metrics | ❌ Missing | No historical aggregation |
| Cost tracking | ❌ Missing | No Bedrock spend awareness |
| Priority queue | ❌ Missing | All tasks compete equally |
| Background probing | ❌ Missing | Probes only on demand |
