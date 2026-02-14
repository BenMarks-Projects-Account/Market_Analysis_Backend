import json
import time
from pathlib import Path
from datetime import datetime
import sys
import os
# Temporarily remove the repo `backend` directory from sys.path so a local
# `backend/requests` folder (leftover shim) does not shadow the real `requests`
# package installed in the venv. After importing, restore the original path.
# When utils lives in `common/`, the backend root is two levels up.
_repo_backend_dir = str(Path(__file__).parent.parent)
_removed_repo_backend = False
if _repo_backend_dir in sys.path:
    try:
        sys.path.remove(_repo_backend_dir)
        _removed_repo_backend = True
    except ValueError:
        _removed_repo_backend = False
try:
    import requests
    from requests.exceptions import RequestException
finally:
    if _removed_repo_backend and _repo_backend_dir not in sys.path:
        sys.path.insert(0, _repo_backend_dir)

RESULTS_DIR = Path(__file__).parent.parent / 'results'


def generate_mock_report() -> str:
    """Create a mocked analysis JSON file in `results/` and return the filename.

    This uses `quant_analysis.enrich_trades_batch` to produce the enriched metrics
    so the frontend receives the expanded metric set.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f"analysis_{ts}.json"

    # Base trade templates with minimal fields — the enrichment function will add metrics
    # Synthetic-but-plausible SPY spreads calibrated around SPY ~681 and IV ~0.17–0.22
    # NOTE: These are not live quotes; they’re designed to be realistic inputs for testing your pipeline.

    base_trades = [
        # --- Put credit spreads (bull put) ---
        {'underlying':'SPY','spread_type':'put_credit','short_strike':665,'long_strike':660,'underlying_price':681.3,'net_credit':0.92,'dte':7,'iv':0.19,'bid':0.90,'ask':0.94,'open_interest':4200,'volume':1800},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':660,'long_strike':650,'underlying_price':681.3,'net_credit':1.45,'dte':14,'iv':0.20,'bid':1.42,'ask':1.48,'open_interest':3800,'volume':1500},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':655,'long_strike':650,'underlying_price':681.3,'net_credit':0.78,'dte':10,'iv':0.18,'bid':0.75,'ask':0.81,'open_interest':5100,'volume':2200},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':650,'long_strike':640,'underlying_price':681.3,'net_credit':1.18,'dte':21,'iv':0.19,'bid':1.14,'ask':1.22,'open_interest':2900,'volume':900},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':645,'long_strike':640,'underlying_price':681.3,'net_credit':0.64,'dte':28,'iv':0.17,'bid':0.61,'ask':0.67,'open_interest':2600,'volume':700},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':640,'long_strike':630,'underlying_price':681.3,'net_credit':0.95,'dte':35,'iv':0.16,'bid':0.91,'ask':0.99,'open_interest':2100,'volume':520},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':670,'long_strike':665,'underlying_price':681.3,'net_credit':1.25,'dte':5,'iv':0.22,'bid':1.21,'ask':1.29,'open_interest':6100,'volume':3400},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':635,'long_strike':625,'underlying_price':681.3,'net_credit':0.72,'dte':42,'iv':0.15,'bid':0.69,'ask':0.75,'open_interest':1800,'volume':380},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':675,'long_strike':670,'underlying_price':681.3,'net_credit':1.62,'dte':3,'iv':0.24,'bid':1.58,'ask':1.66,'open_interest':7400,'volume':4100},
        {'underlying':'SPY','spread_type':'put_credit','short_strike':625,'long_strike':615,'underlying_price':681.3,'net_credit':0.48,'dte':45,'iv':0.14,'bid':0.45,'ask':0.51,'open_interest':1500,'volume':260},

        # --- Call credit spreads (bear call) ---
        {'underlying':'SPY','spread_type':'call_credit','short_strike':700,'long_strike':705,'underlying_price':681.3,'net_credit':0.82,'dte':7,'iv':0.19,'bid':0.79,'ask':0.85,'open_interest':3600,'volume':1400},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':705,'long_strike':715,'underlying_price':681.3,'net_credit':1.12,'dte':14,'iv':0.20,'bid':1.08,'ask':1.16,'open_interest':2400,'volume':880},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':710,'long_strike':715,'underlying_price':681.3,'net_credit':0.63,'dte':10,'iv':0.18,'bid':0.60,'ask':0.66,'open_interest':4100,'volume':1700},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':715,'long_strike':725,'underlying_price':681.3,'net_credit':0.88,'dte':21,'iv':0.17,'bid':0.84,'ask':0.92,'open_interest':1900,'volume':540},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':720,'long_strike':725,'underlying_price':681.3,'net_credit':0.52,'dte':28,'iv':0.16,'bid':0.49,'ask':0.55,'open_interest':1700,'volume':430},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':690,'long_strike':695,'underlying_price':681.3,'net_credit':1.05,'dte':5,'iv':0.22,'bid':1.01,'ask':1.09,'open_interest':5200,'volume':2600},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':695,'long_strike':705,'underlying_price':681.3,'net_credit':1.34,'dte':9,'iv':0.21,'bid':1.30,'ask':1.38,'open_interest':3100,'volume':1200},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':730,'long_strike':735,'underlying_price':681.3,'net_credit':0.41,'dte':35,'iv':0.15,'bid':0.39,'ask':0.43,'open_interest':1300,'volume':260},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':725,'long_strike':735,'underlying_price':681.3,'net_credit':0.69,'dte':42,'iv':0.14,'bid':0.66,'ask':0.72,'open_interest':1200,'volume':210},
        {'underlying':'SPY','spread_type':'call_credit','short_strike':685,'long_strike':690,'underlying_price':681.3,'net_credit':0.74,'dte':3,'iv':0.24,'bid':0.71,'ask':0.77,'open_interest':6800,'volume':3900},
    ]

    # small synthetic price history to compute trend/rv/rsi
    prices_history = [90 + i * 0.5 for i in range(60)]

    # Import enrichment and CreditSpread locally to avoid circular imports at module load
    enriched = base_trades
    try:
        from .quant_analysis import enrich_trades_batch, CreditSpread, _norm_cdf
    except Exception:
        enrich_trades_batch = None
        CreditSpread = None
        _norm_cdf = None

    # If enrichment is available, run it and merge CreditSpread.summary() for frontend compatibility
    # Provide a VIX value to the enrichment to allow classify_market_regime to use it
    vix_value = 17.44
    if enrich_trades_batch:
        try:
            enriched = enrich_trades_batch(
                base_trades,
                prices_history=prices_history,
                vix=vix_value,
                iv_low=0.10,
                iv_high=0.30,
            )
        except Exception:
            print('[utils] Warning: enrich_trades_batch raised an exception; falling back to base trades')
            enriched = base_trades

    if enriched and CreditSpread:
        final = []
        print('[utils] Generating enriched report — merging CreditSpread summaries')
        for tr in enriched:
            try:
                cs_kwargs = {
                    'spread_type': tr.get('spread_type'),
                    'underlying_price': tr.get('underlying_price') or tr.get('price'),
                    'short_strike': tr.get('short_strike'),
                    'long_strike': tr.get('long_strike'),
                    'net_credit': tr.get('net_credit') or tr.get('max_profit_per_share') or 0.0,
                    'dte': tr.get('dte'),
                    'short_delta_abs': tr.get('short_delta_abs'),
                    'implied_vol': tr.get('iv') or tr.get('implied_vol'),
                    'realized_vol': tr.get('realized_vol'),
                }
                cs = CreditSpread(**cs_kwargs)
                summary = cs.summary(iv_rank_value=tr.get('iv_rank'))
                # Merge enrichment fields (they may include market_regime, rsi, etc.)
                merged = {**summary, **tr}
                # ensure VIX is present in the merged output (take from trade or our vix_value)
                if merged.get('vix') is None:
                    merged['vix'] = vix_value

                # If p_win_used wasn't available from the CreditSpread summary, try a fallback
                # using the short_strike_z (distance in sigma). POP ≈ NormalCDF(short_strike_z).
                if merged.get('p_win_used') is None and _norm_cdf is not None:
                    sz = merged.get('short_strike_z')
                    try:
                        if sz is not None:
                            est_pop = float(_norm_cdf(sz))
                            merged['p_win_used'] = est_pop
                            # recompute EV/Kelly using the same CreditSpread instance
                            try:
                                merged['ev_per_share'] = cs.expected_value_per_share(p_win=est_pop)
                                merged['ev_to_risk'] = cs.ev_to_risk(p_win=est_pop)
                                merged['kelly_fraction'] = cs.kelly_fraction(p_win=est_pop)
                                merged['trade_quality_score'] = cs.trade_quality_score(p_win=est_pop, iv_rank_value=tr.get('iv_rank'))
                            except Exception:
                                pass
                    except Exception:
                        pass
                # ensure VIX is present in the merged output (take from trade or our vix_value)
                if merged.get('vix') is None:
                    merged['vix'] = vix_value

                # Ensure some frontend-expected fields exist (fallbacks)
                if merged.get('max_profit_per_share') is None:
                    merged['max_profit_per_share'] = merged.get('net_credit')
                if merged.get('max_loss_per_share') is None:
                    width = merged.get('width') if merged.get('width') is not None else (abs(merged.get('long_strike',0) - merged.get('short_strike',0)))
                    merged['max_loss_per_share'] = width - (merged.get('net_credit') or 0)
                if merged.get('break_even') is None:
                    if merged.get('spread_type') == 'put_credit':
                        merged['break_even'] = merged.get('short_strike') - (merged.get('net_credit') or 0)
                    else:
                        merged['break_even'] = merged.get('short_strike') + (merged.get('net_credit') or 0)

                final.append(merged)
            except Exception:
                print('[utils] Warning: failed to merge CreditSpread summary for trade:', tr)
                final.append(tr)

        enriched = final

    file_path = RESULTS_DIR / filename
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(enriched, f, indent=2, default=str)

    # After writing the base enriched report, optionally call the local LM Studio model
    # to append a `model_evaluation` object to each trade. By default this is disabled
    # to avoid automatic model calls on report generation. Set environment variable
    # AUTO_CALL_MODEL=1 to enable automatic calling (useful for batch runs).
    def _call_model_and_append(trades, target_path, model_url='http://localhost:1234/v1/chat/completions', retries=2, timeout=30, batch_size=1):
        """
        Send trades to the model in batches of `batch_size`, merge returned
        `model_evaluation` into each trade, and write combined output to a new
        file prefixed with 'mode_'. Returns True on success.
        """
        # Prompt/instructions for the model: return only the JSON array of trades
        PROMPT = (
            "You are an options trading risk advisor.\n\n"
            "You will receive a JSON array of a single credit spread with many metrics.\n"
            "Return ONLY a JSON array of the SAME trades in the SAME order.\n"
            "For each trade object, append EXACTLY one new key: \"model_evaluation\".\n"
            "Do NOT add any filenames, metadata, or wrapper objects.\n"
            "Do not modify, remove, rename, or reorder existing keys/values.\n\n"

            "EVALUATION GOAL:\n"
            "Provide a recommendation based on TRADE QUALITY, emphasizing risk-adjusted returns.\n"
            "You MUST explicitly consider max_profit vs max_loss, expected_value, return_on_risk, and liquidity.\n"
            "Market regime and probability can SUPPORT a decision but MUST NOT override negative expectancy.\n\n"

            "HARD DECISION RULES (apply in this exact priority order):\n"
            "1) If \"ev_per_share\" or \"expected_value\" exists and is < 0 -> recommendation MUST be \"REJECT\".\n"
            "2) If \"kelly_fraction\" exists and is < 0 -> recommendation MUST be \"REJECT\".\n"
            "3) If max_loss is missing, or max_loss <= 0, or max_profit is missing -> recommendation MUST be \"NEUTRAL\" with low confidence.\n"
            "4) If return_on_risk exists and is < 0.10 (10%) -> default to \"REJECT\" unless EV is strongly positive and liquidity is excellent.\n"
            "5) If bid_ask_spread_pct exists and is > 0.12 (12%) -> at most \"NEUTRAL\" (execution risk too high).\n\n"

            "SECONDARY GUIDELINES (only if no hard-rule forced a decision):\n"
            "- Prefer ACCEPT when EV > 0, Kelly > 0, return_on_risk >= 0.10, and short_strike_z >= 1.0.\n"
            "- Prefer NEUTRAL when EV is small/near zero, or when there is meaningful event/volatility risk.\n"
            "- Prefer REJECT when risk:reward is extreme (max_loss / max_profit > 8), even if probability is high.\n"
            "- Treat probability/pop as supportive evidence only; do not recommend a trade solely because POP is high.\n\n"

            "DATA QUALITY GUARDS:\n"
            "- If a metric is clearly implausible (e.g., iv_rv_ratio > 6, rsi14 == 100 with no trend context, "
            "or realized_vol appears near zero), mention possible data issue in key_factors and reduce confidence.\n"
            "- Never invent missing metrics.\n\n"

            "The \"model_evaluation\" object MUST contain EXACTLY these keys:\n"
            "- recommendation: one of [\"ACCEPT\", \"NEUTRAL\", \"REJECT\"]\n"
            "- confidence: number between 0 and 1\n"
            "- risk_level: one of [\"Low\", \"Moderate\", \"High\"]\n"
            "- key_factors: array of 2 to 6 short strings (include at least one factor about risk/reward or EV)\n"
            "- summary: one paragraph string\n\n"

            "CONFIDENCE RULES:\n"
            "- If a hard rule forces REJECT (negative EV or negative Kelly), confidence should be >= 0.80.\n"
            "- If required inputs are missing or data quality is questionable, confidence should be <= 0.40.\n\n"

            "OUTPUT RULES:\n"
            "- Output must be valid JSON only (a single JSON array).\n"
            "- Do not include any commentary or surrounding text.\n"
            "- Preserve all numeric values exactly as received.\n"
            "- If you cannot evaluate a trade, still return the trade but set \"model_evaluation\" with "
            "\"recommendation\"=\"NEUTRAL\" and low confidence.\n"
        )


        out_path = Path(target_path).parent / ("mode_" + Path(target_path).name)
        all_updated = []
        model_out_path = Path(target_path).parent / ("model_" + Path(target_path).name)

        def _send_batch(batch):
            payload = {
                'messages': [
                    {'role': 'system', 'content': PROMPT},
                    {'role': 'user', 'content': json.dumps(batch, ensure_ascii=False, indent=None)}
                ],
                'max_tokens': 2048,
                'temperature': 0.0,
            }

            attempt = 0
            last_err = None
            while attempt <= retries:
                try:
                    attempt += 1
                    print(f"[utils] Calling model at {model_url} (attempt {attempt}) for batch size {len(batch)}")
                    resp = requests.post(model_url, json=payload, timeout=timeout)
                    resp.raise_for_status()

                    # extract assistant text from chat response
                    try:
                        resp_json = resp.json()
                    except ValueError:
                        resp_json = None

                    assistant_text = None
                    if isinstance(resp_json, dict):
                        chs = resp_json.get('choices') or []
                        if chs and isinstance(chs, list):
                            first = chs[0]
                            if isinstance(first, dict):
                                msg = first.get('message')
                                if isinstance(msg, dict) and 'content' in msg:
                                    assistant_text = msg.get('content')
                                elif 'text' in first:
                                    assistant_text = first.get('text')
                    if assistant_text is None:
                        assistant_text = resp.text.strip()

                    # parse JSON from assistant_text
                    parsed = None
                    try:
                        parsed = json.loads(assistant_text)
                    except Exception:
                        # heuristic extraction
                        txt = assistant_text
                        start_idx = None
                        for ch in ('[', '{'):
                            i = txt.find(ch)
                            if i != -1:
                                start_idx = i
                                break
                        if start_idx is not None:
                            open_ch = txt[start_idx]
                            close_ch = ']' if open_ch == '[' else '}'
                            end_idx = txt.rfind(close_ch)
                            if end_idx != -1:
                                try:
                                    parsed = json.loads(txt[start_idx:end_idx + 1])
                                except Exception:
                                    parsed = None

                    if parsed is None:
                        print('[utils] Could not parse model response for batch; raw response:')
                        print(resp.text)
                        return None

                    # parsed should be a list or dict with 'trades'
                    if isinstance(parsed, dict) and 'trades' in parsed:
                        model_trades = parsed['trades']
                    elif isinstance(parsed, list):
                        model_trades = parsed
                    else:
                        print('[utils] Unexpected parsed model shape for batch; raw parsed:')
                        print(parsed)
                        return None

                    if len(model_trades) != len(batch):
                        print('[utils] Model returned different number of trades for batch; skipping')
                        return None

                    # merge model_evaluation
                    updated = []
                    for orig, m in zip(batch, model_trades):
                        new = dict(orig)
                        if isinstance(m, dict) and 'model_evaluation' in m:
                            new['model_evaluation'] = m['model_evaluation']
                        else:
                            # maybe the model returned the evaluation nested differently
                            # try to find keys matching recommendation/confidence
                            if isinstance(m, dict) and any(k in m for k in ('recommendation','confidence','risk_level','key_factors','summary')):
                                # assume m itself is the evaluation object
                                new['model_evaluation'] = {k: m.get(k) for k in ('recommendation','confidence','risk_level','key_factors','summary') if k in m}
                        updated.append(new)

                    return updated

                except RequestException as e:
                    last_err = e
                    print(f"[utils] Model call failed (attempt {attempt}): {e}")
                    time.sleep(1)

            print('[utils] All model call attempts failed for this batch; last error:', last_err)
            return None

        # Iterate batches and append each returned evaluated trade to model_<filename>
        for i in range(0, len(trades), batch_size):
            batch = trades[i:i+batch_size]
            res = _send_batch(batch)
            if res is None:
                print(f"[utils] Aborting model batching at batch starting index {i}")
                return False

            # res is a list of evaluated trades for this batch (same order)
            for evaluated_trade in res:
                # read existing file if present
                existing = []
                try:
                    if model_out_path.exists():
                        with open(model_out_path, 'r', encoding='utf-8') as rf:
                            existing = json.load(rf)
                except Exception:
                    print(f"[utils] Warning: failed to read existing model output {model_out_path}; starting fresh")
                    existing = []

                existing.append(evaluated_trade)

                try:
                    with open(model_out_path, 'w', encoding='utf-8') as wf:
                        json.dump(existing, wf, indent=2, default=str)
                    print(f"[utils] Appended evaluated trade to {model_out_path} (total={len(existing)})")
                except Exception as e:
                    print('[utils] Failed to write model output file:', e)
                    return False

        return True

    # Try calling model and append evaluations if explicitly enabled by env var.
    # This prevents automatic network calls during report generation unless desired.
    try:
        import os
        if os.environ.get('AUTO_CALL_MODEL', '0') == '1':
            _call_model_and_append(enriched, file_path)
        else:
            print('[utils] AUTO_CALL_MODEL not set; skipping automatic model calls')
    except Exception:
        print('[utils] Unexpected error while calling model; continuing without model evaluations')

    return filename


def analyze_trade_with_model(trade: dict, source_filename: str, model_url='http://localhost:1234/v1/chat/completions', retries=2, timeout=30):
    """Send a single trade to the local model and append the evaluated trade
    to `results/model_<source_filename>`. Returns the evaluated trade dict
    (including `model_evaluation`) on success. On model-call failure a
    provisional NEUTRAL evaluation is persisted and returned so the UI
    receives a deterministic response (no 500).
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    model_out_path = RESULTS_DIR / ("model_" + Path(source_filename).name)

    PROMPT = (
        "You are an options trading risk advisor.\n\n"
        "You will receive a JSON array of trades (this request contains up to 1 trade).\n"
        "Return ONLY a JSON array of the SAME trades in the SAME order.\n"
        "For each trade object, append EXACTLY one new key: \"model_evaluation\".\n"
        "Do NOT add any filenames, metadata, or wrapper objects.\n"
        "Do not modify, remove, rename, or reorder existing keys/values.\n\n"
        "The \"model_evaluation\" object MUST contain EXACTLY these keys:\n"
        "- recommendation: one of [\"ACCEPT\", \"NEUTRAL\", \"REJECT\"]\n"
        "- confidence: number between 0 and 1\n"
        "- risk_level: one of [\"Low\", \"Moderate\", \"High\"]\n"
        "- key_factors: array of 2 to 6 short strings\n"
        "- summary: one paragraph string\n\n"
        "OUTPUT RULES:\n"
        "- Output must be valid JSON only (a single JSON array).\n"
        "- Do not include any commentary or surrounding text.\n"
        "- Preserve all numeric values exactly as received.\n"
        "- If you cannot evaluate a trade, still return the trade but set \"model_evaluation\" with an appropriate recommendation and low confidence."
    )

    # Pre-call deterministic override
    base_me = trade.get('model_evaluation') or {}
    me_override = hard_gate_override(trade, base_me)

    # If hard gate forced a decision, persist and return immediately
    if me_override and me_override.get('_hard_gate_forced'):
        forced_eval = dict(me_override)
        forced_eval.pop('_hard_gate_forced', None)
        new = dict(trade)
        new['model_evaluation'] = forced_eval
        existing = []
        try:
            if model_out_path.exists():
                with open(model_out_path, 'r', encoding='utf-8') as rf:
                    existing = json.load(rf)
        except Exception:
            existing = []
        existing.append(new)
        try:
            with open(model_out_path, 'w', encoding='utf-8') as wf:
                json.dump(existing, wf, indent=2, default=str)
            print(f"[utils] Hard-gate forced evaluation appended to {model_out_path} (total={len(existing)})")
            return new
        except Exception as e:
            print('[utils] Failed to write forced model output file for single trade:', e)

    # Prepare payload (include provisional evaluation if present)
    trade_for_payload = dict(trade)
    if me_override and me_override.get('recommendation') in ('REJECT', 'NEUTRAL', 'ACCEPT'):
        mcopy = dict(me_override)
        mcopy.pop('_hard_gate_forced', None)
        trade_for_payload['model_evaluation'] = mcopy

    payload = {
        'messages': [
            {'role': 'system', 'content': PROMPT},
            {'role': 'user', 'content': json.dumps([trade_for_payload], ensure_ascii=False, indent=None)}
        ],
        'max_tokens': 2048,
        'temperature': 0.0,
    }

    attempt = 0
    last_err = None
    while attempt <= retries:
        try:
            attempt += 1
            print(f"[utils] Calling model at {model_url} (attempt {attempt}) for single trade")
            resp = requests.post(model_url, json=payload, timeout=timeout)
            resp.raise_for_status()

            try:
                resp_json = resp.json()
            except Exception:
                resp_json = None

            assistant_text = None
            if isinstance(resp_json, dict):
                chs = resp_json.get('choices') or []
                if chs and isinstance(chs, list):
                    first = chs[0]
                    if isinstance(first, dict):
                        msg = first.get('message')
                        if isinstance(msg, dict) and 'content' in msg:
                            assistant_text = msg.get('content')
                        elif 'text' in first:
                            assistant_text = first.get('text')
            if assistant_text is None:
                assistant_text = getattr(resp, 'text', '') or ''

            parsed = None
            try:
                parsed = json.loads(assistant_text)
            except Exception:
                txt = assistant_text
                start_idx = None
                for ch in ('[', '{'):
                    i = txt.find(ch)
                    if i != -1:
                        start_idx = i
                        break
                if start_idx is not None:
                    open_ch = txt[start_idx]
                    close_ch = ']' if open_ch == '[' else '}'
                    end_idx = txt.rfind(close_ch)
                    if end_idx != -1:
                        try:
                            parsed = json.loads(txt[start_idx:end_idx + 1])
                        except Exception:
                            parsed = None

            if parsed is None:
                print('[utils] Could not parse model response for single trade; raw response:')
                print(getattr(resp, 'text', ''))
                last_err = 'unparsable_response'
                break

            if isinstance(parsed, dict) and 'trades' in parsed:
                model_trades = parsed['trades']
            elif isinstance(parsed, list):
                model_trades = parsed
            else:
                print('[utils] Unexpected parsed model shape for single trade; raw parsed:')
                print(parsed)
                last_err = 'unexpected_shape'
                break

            if len(model_trades) != 1:
                print('[utils] Model returned unexpected number of trades for single-call')
                last_err = 'unexpected_count'
                break

            m = model_trades[0]
            new = dict(trade)
            if isinstance(m, dict) and 'model_evaluation' in m:
                new['model_evaluation'] = m['model_evaluation']
            else:
                if isinstance(m, dict) and any(k in m for k in ('recommendation','confidence','risk_level','key_factors','summary')):
                    new['model_evaluation'] = {k: m.get(k) for k in ('recommendation','confidence','risk_level','key_factors','summary') if k in m}

            # append to model_out_path
            existing = []
            try:
                if model_out_path.exists():
                    with open(model_out_path, 'r', encoding='utf-8') as rf:
                        existing = json.load(rf)
            except Exception:
                existing = []

            existing.append(new)
            try:
                with open(model_out_path, 'w', encoding='utf-8') as wf:
                    json.dump(existing, wf, indent=2, default=str)
                print(f"[utils] Appended evaluated trade to {model_out_path} (total={len(existing)})")
                return new
            except Exception as e:
                print('[utils] Failed to write model output file for single trade:', e)
                last_err = e
                break

        except RequestException as e:
            last_err = e
            print(f"[utils] Model call failed for single trade (attempt {attempt}): {e}")
            time.sleep(1)

    print('[utils] Model attempts exhausted or errored; last error:', last_err)

    # Fallback: persist a provisional NEUTRAL evaluation so caller receives a response
    fallback_eval = {
        'recommendation': 'NEUTRAL',
        'confidence': 0.35,
        'risk_level': 'Moderate',
        'key_factors': [
            'Model unreachable or returned invalid response',
            'Persisted provisional NEUTRAL evaluation'
        ],
        'summary': 'Model call failed; saved provisional NEUTRAL evaluation so UI reflects a decision.'
    }
    try:
        if me_override and isinstance(me_override, dict) and not me_override.get('_hard_gate_forced'):
            if me_override.get('recommendation'):
                fallback_eval['recommendation'] = me_override.get('recommendation')
            if me_override.get('confidence') is not None:
                fallback_eval['confidence'] = me_override.get('confidence')
            if me_override.get('risk_level'):
                fallback_eval['risk_level'] = me_override.get('risk_level')
            if me_override.get('key_factors'):
                fallback_eval['key_factors'] = (['Model unreachable or returned invalid response'] + me_override.get('key_factors'))[:6]
    except Exception:
        pass

    new = dict(trade)
    new['model_evaluation'] = fallback_eval
    existing = []
    try:
        if model_out_path.exists():
            with open(model_out_path, 'r', encoding='utf-8') as rf:
                existing = json.load(rf)
    except Exception:
        existing = []
    existing.append(new)
    try:
        with open(model_out_path, 'w', encoding='utf-8') as wf:
            json.dump(existing, wf, indent=2, default=str)
        print(f"[utils] Wrote provisional model evaluation to {model_out_path} (total={len(existing)})")
        return new
    except Exception as e:
        print('[utils] Failed to write provisional model output file for single trade:', e)
        return None


def hard_gate_override(trade: dict, me: dict) -> dict:
    """Apply deterministic rules to force REJECT decisions before calling the model.

    Returns an updated `me` dict when a hard gate forces a decision. The returned
    dict will include `_hard_gate_forced=True` when a forced decision is applied.
    """
    # Read and normalize candidate fields
    def _to_float(x):
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        if isinstance(x, str):
            s = x.strip().replace('%', '')
            try:
                return float(s)
            except Exception:
                return None
        return None

    ev = _to_float(trade.get('ev_per_share', trade.get('expected_value')))
    kelly = _to_float(trade.get('kelly_fraction'))
    ror = _to_float(trade.get('return_on_risk'))
    max_profit = _to_float(trade.get('max_profit_per_share', trade.get('max_profit')))
    max_loss = _to_float(trade.get('max_loss_per_share', trade.get('max_loss')))

    forced = None
    reasons = []
    if ev is not None and ev < 0:
        forced = 'REJECT'
        reasons.append('Negative expected value')
    if kelly is not None and kelly < 0:
        forced = 'REJECT'
        reasons.append('Negative Kelly fraction')
    if ror is not None and ror < 0.10:
        forced = 'REJECT'
        reasons.append('Return on risk below threshold')
    if max_profit and max_loss and max_profit > 0 and (max_loss / max_profit) > 8:
        forced = 'REJECT'
        reasons.append('Risk/reward too extreme')

    if forced:
        out = dict(me) if me else {}
        out['recommendation'] = forced
        try:
            out['confidence'] = max(float(out.get('confidence', 0.5)), 0.85)
        except Exception:
            out['confidence'] = 0.85
        out['risk_level'] = 'High'
        kf = out.get('key_factors') or []
        out['key_factors'] = (reasons + kf)[:6]
        out['_hard_gate_forced'] = True
        return out

    return me

    # allow a pre-call hard-gate override to force REJECT/NEUTRAL decisions
    base_me = trade.get('model_evaluation') or {}
    me_override = hard_gate_override(trade, base_me)

    # If the hard gate forced a decision, append locally and return immediately
    if me_override and me_override.get('_hard_gate_forced'):
        # copy and remove internal flag before persisting
        forced_eval = dict(me_override)
        forced_eval.pop('_hard_gate_forced', None)

        new = dict(trade)
        new['model_evaluation'] = forced_eval

        # append to model_out_path immediately so UI sees the change without model call
        existing = []
        try:
            if model_out_path.exists():
                with open(model_out_path, 'r', encoding='utf-8') as rf:
                    existing = json.load(rf)
        except Exception:
            existing = []

        existing.append(new)
        try:
            with open(model_out_path, 'w', encoding='utf-8') as wf:
                json.dump(existing, wf, indent=2, default=str)
            print(f"[utils] Hard-gate forced evaluation appended to {model_out_path} (total={len(existing)})")
            return new
        except Exception as e:
            print('[utils] Failed to write forced model output file for single trade:', e)
            # fall back to attempting a model call below

    # Build the trade payload; if an override provided a provisional evaluation, include it
    trade_for_payload = dict(trade)
    if me_override and me_override.get('recommendation') in ('REJECT', 'NEUTRAL', 'ACCEPT'):
        # don't include internal flag in payload
        mcopy = dict(me_override)
        mcopy.pop('_hard_gate_forced', None)
        trade_for_payload['model_evaluation'] = mcopy

    payload = {
        'messages': [
            {'role': 'system', 'content': PROMPT},
            {'role': 'user', 'content': json.dumps([trade_for_payload], ensure_ascii=False, indent=None)}
        ],
        'max_tokens': 2048,
        'temperature': 0.0,
    }

    attempt = 0
    last_err = None
    while attempt <= retries:
        try:
            attempt += 1
            print(f"[utils] Calling model at {model_url} (attempt {attempt}) for single trade")
            resp = requests.post(model_url, json=payload, timeout=timeout)
            resp.raise_for_status()

            try:
                resp_json = resp.json()
            except ValueError:
                resp_json = None

            assistant_text = None
            if isinstance(resp_json, dict):
                chs = resp_json.get('choices') or []
                if chs and isinstance(chs, list):
                    first = chs[0]
                    if isinstance(first, dict):
                        msg = first.get('message')
                        if isinstance(msg, dict) and 'content' in msg:
                            assistant_text = msg.get('content')
                        elif 'text' in first:
                            assistant_text = first.get('text')
            if assistant_text is None:
                assistant_text = resp.text.strip()

            parsed = None
            try:
                parsed = json.loads(assistant_text)
            except Exception:
                txt = assistant_text
                start_idx = None
                for ch in ('[', '{'):
                    i = txt.find(ch)
                    if i != -1:
                        start_idx = i
                        break
                if start_idx is not None:
                    open_ch = txt[start_idx]
                    close_ch = ']' if open_ch == '[' else '}'
                    end_idx = txt.rfind(close_ch)
                    if end_idx != -1:
                        try:
                            parsed = json.loads(txt[start_idx:end_idx + 1])
                        except Exception:
                            parsed = None

            if parsed is None:
                print('[utils] Could not parse model response for single trade; raw response:')
                print(resp.text)
                return None

            if isinstance(parsed, dict) and 'trades' in parsed:
                model_trades = parsed['trades']
            elif isinstance(parsed, list):
                model_trades = parsed
            else:
                print('[utils] Unexpected parsed model shape for single trade; raw parsed:')
                print(parsed)
                return None

            if len(model_trades) != 1:
                print('[utils] Model returned unexpected number of trades for single-call')
                return None

            m = model_trades[0]
            new = dict(trade)
            if isinstance(m, dict) and 'model_evaluation' in m:
                new['model_evaluation'] = m['model_evaluation']
            else:
                if isinstance(m, dict) and any(k in m for k in ('recommendation','confidence','risk_level','key_factors','summary')):
                    new['model_evaluation'] = {k: m.get(k) for k in ('recommendation','confidence','risk_level','key_factors','summary') if k in m}

            # append to model_out_path
            existing = []
            try:
                if model_out_path.exists():
                    with open(model_out_path, 'r', encoding='utf-8') as rf:
                        existing = json.load(rf)
            except Exception:
                existing = []

            existing.append(new)
            try:
                with open(model_out_path, 'w', encoding='utf-8') as wf:
                    json.dump(existing, wf, indent=2, default=str)
                print(f"[utils] Appended evaluated trade to {model_out_path} (total={len(existing)})")
                return new
            except Exception as e:
                print('[utils] Failed to write model output file for single trade:', e)
                return None

        except RequestException as e:
            last_err = e
            print(f"[utils] Model call failed for single trade (attempt {attempt}): {e}")
            time.sleep(1)

    print('[utils] All model call attempts failed for single trade; last error:', last_err)
    return None
