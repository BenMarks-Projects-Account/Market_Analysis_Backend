"""News & Sentiment orchestration service (layered architecture).

Three independent layers:
  1. Base payload — normalized headlines + macro context (always available)
  2. Internal Engine — deterministic rule-based scoring (always runs with base)
  3. Model Analysis — LLM-based assessment, triggered manually by the user

Data fetching (Finnhub, Polygon, FRED) remains here.  Scoring is delegated.

Caching:
  - Base + engine payload cached for NEWS_CACHE_TTL seconds (default 300).
  - Model analysis cached separately under its own key.
  - Model is NOT auto-run; it must be explicitly requested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.services.news_sentiment_engine import compute_engine_scores
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

NEWS_CACHE_TTL = 300  # 5 minutes for merged news payload

# ── Category keywords for simple first-pass topic classification ────────
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "fed": ["fed", "fomc", "federal reserve", "rate hike", "rate cut", "interest rate", "powell", "monetary policy", "taper"],
    "geopolitical": ["war", "conflict", "sanction", "tariff", "geopolitical", "military", "invasion", "nato", "troops", "missile", "nuclear"],
    "macro": ["gdp", "inflation", "cpi", "ppi", "unemployment", "jobs report", "nonfarm", "payroll", "recession", "economic"],
    "commodities": ["oil", "crude", "brent", "wti", "gold", "silver", "copper", "natural gas", "commodity"],
    "earnings": ["earnings", "revenue", "eps", "beat", "miss", "guidance", "quarterly", "annual report"],
    "sector": ["sector", "industry", "rotation", "cyclical", "defensive", "tech sector", "financials", "energy sector", "healthcare"],
    "shipping": ["shipping", "supply chain", "freight", "port", "container", "logistics", "suez", "panama canal"],
    "company": ["ipo", "merger", "acquisition", "buyback", "dividend", "ceo", "board", "restructuring"],
}

# ── Sentiment keywords for rule-based scoring ──────────────────────────
_BULLISH_WORDS = frozenset([
    "surge", "rally", "gain", "soar", "bull", "upbeat", "optimism", "optimistic",
    "growth", "strong", "boost", "recovery", "positive", "beat", "outperform",
    "upgrade", "record high", "breakout", "rebound", "expansion",
])
_BEARISH_WORDS = frozenset([
    "crash", "plunge", "drop", "fall", "bear", "fear", "recession", "downturn",
    "decline", "loss", "risk", "warning", "downgrade", "sell-off", "selloff",
    "weak", "cut", "layoff", "default", "crisis", "contraction", "slump",
    "tumble", "collapse", "concern", "uncertainty", "volatile",
])


@dataclass
class NormalizedNewsItem:
    source: str
    headline: str
    summary: str
    url: str
    published_at: str
    symbols: list[str]
    category: str
    sentiment_label: str
    sentiment_score: float
    relevance_score: int


@dataclass
class MacroContext:
    vix: float | None = None
    us_10y_yield: float | None = None
    us_2y_yield: float | None = None
    fed_funds_rate: float | None = None
    oil_wti: float | None = None
    usd_index: float | None = None
    yield_curve_spread: float | None = None  # 10y - 2y
    stress_level: str = "unknown"  # low | moderate | elevated | high
    as_of: str = ""
    # Per-metric freshness — populated from MarketContextService envelopes
    _freshness: dict = field(default_factory=dict)


@dataclass
class SourceFreshness:
    source: str
    status: str  # ok | error | unavailable
    last_fetched: str | None = None
    item_count: int = 0
    error: str | None = None


class NewsSentimentService:
    """Aggregates news and sentiment from multiple providers."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        cache: TTLCache,
        *,
        fred_client: Any = None,
        market_context_service: Any = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache
        self.fred_client = fred_client
        self.market_context_service = market_context_service

    # ── Public API ──────────────────────────────────────────────────

    async def get_news_sentiment(self, *, force: bool = False) -> dict[str, Any]:
        """Return base + engine news-sentiment payload. Cached for NEWS_CACHE_TTL seconds.

        Response structure:
          internal_engine: deterministic engine result (always present)
          items: normalized headline items
          macro_context: FRED macro snapshot
          source_freshness: per-provider status
          as_of: timestamp
          item_count: int

        Model analysis is NOT included here — call run_model_analysis() separately.
        """
        cache_key = "news_sentiment:merged"

        if not force:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                return cached

        items: list[NormalizedNewsItem] = []
        freshness: list[SourceFreshness] = []

        # Fetch from all sources
        finnhub_items, finnhub_fresh = await self._fetch_finnhub()
        items.extend(finnhub_items)
        freshness.append(finnhub_fresh)

        polygon_items, polygon_fresh = await self._fetch_polygon()
        items.extend(polygon_items)
        freshness.append(polygon_fresh)

        # Tradier doesn't have news endpoints — mark unavailable
        freshness.append(SourceFreshness(
            source="tradier", status="unavailable", error="No news endpoints available",
        ))

        # FRED macro context
        macro = await self._fetch_macro_context()
        freshness.append(SourceFreshness(
            source="fred",
            status="ok" if macro.vix is not None else "error",
            last_fetched=macro.as_of or None,
            item_count=sum(1 for v in [macro.vix, macro.us_10y_yield, macro.fed_funds_rate, macro.oil_wti] if v is not None),
            error=None if macro.vix is not None else "No FRED data returned",
        ))

        # Deduplicate and sort
        items = self._deduplicate(items)
        items.sort(key=lambda x: (-x.relevance_score, x.published_at), reverse=False)
        items.sort(key=lambda x: -x.relevance_score)

        # Convert to dicts for engine and response
        item_dicts = [self._item_to_dict(i) for i in items[:100]]
        macro_dict = {
            "vix": macro.vix,
            "us_10y_yield": macro.us_10y_yield,
            "us_2y_yield": macro.us_2y_yield,
            "fed_funds_rate": macro.fed_funds_rate,
            "oil_wti": macro.oil_wti,
            "usd_index": macro.usd_index,
            "yield_curve_spread": macro.yield_curve_spread,
            "stress_level": macro.stress_level,
            "as_of": macro.as_of,
            "_freshness": macro._freshness,
        }

        # ── Track 1: Internal Engine (deterministic, always runs) ───
        engine_result = None
        try:
            logger.info("[NEWS_ENGINE] start items=%d macro_keys=%s", len(item_dicts), list(macro_dict.keys()))
            engine_result = compute_engine_scores(item_dicts, macro_dict)
            logger.info(
                "[NEWS_ENGINE] success score=%.2f label=%s components=%s",
                engine_result.get("score", -1),
                engine_result.get("regime_label"),
                list(engine_result.get("components", {}).keys()),
            )
        except Exception as exc:
            logger.exception("[NEWS_ENGINE] failure error=%s", exc)

        payload = {
            "internal_engine": engine_result,
            "items": item_dicts,
            "macro_context": macro_dict,
            "source_freshness": [
                {
                    "source": f.source,
                    "status": f.status,
                    "last_fetched": f.last_fetched,
                    "item_count": f.item_count,
                    "error": f.error,
                }
                for f in freshness
            ],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "item_count": len(item_dicts),
        }

        await self.cache.set(cache_key, payload, NEWS_CACHE_TTL)
        return payload

    def _run_model_analysis(
        self,
        items: list[dict[str, Any]],
        macro_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Attempt LLM-based news sentiment analysis. Returns None on failure."""
        try:
            from common.model_analysis import analyze_news_sentiment
            result = analyze_news_sentiment(
                items=items,
                macro_context=macro_context,
                timeout=300,
                retries=0,
            )
            logger.info("event=news_model_analysis_ok score=%s", result.get("score"))
            return result
        except Exception as exc:
            logger.warning("event=news_model_analysis_failed error=%s", exc)
            return None

    async def run_model_analysis(self, *, force: bool = False) -> dict[str, Any]:
        """Run LLM model analysis on demand. Uses cached base payload for inputs.

        Returns:
          model_analysis: dict | None — the model result, or None on failure
          as_of: ISO timestamp
        """
        model_cache_key = "news_sentiment:model"

        if not force:
            cached = await self.cache.get(model_cache_key)
            if cached is not None:
                return cached

        # Get base payload (items + macro) — may use its own cache
        base = await self.get_news_sentiment(force=False)
        items = base.get("items", [])
        macro = base.get("macro_context", {})

        model_result = self._run_model_analysis(items, macro)

        result = {
            "model_analysis": model_result,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

        # Cache model result for same TTL as base payload
        await self.cache.set(model_cache_key, result, NEWS_CACHE_TTL)
        return result

    # ── Finnhub fetch ───────────────────────────────────────────────

    async def _fetch_finnhub(self) -> tuple[list[NormalizedNewsItem], SourceFreshness]:
        """Fetch general market news from Finnhub /news endpoint."""
        if not self.settings.FINNHUB_KEY:
            return [], SourceFreshness(
                source="finnhub", status="unavailable", error="No API key configured",
            )

        try:
            url = f"{self.settings.FINNHUB_BASE_URL}/news"
            params = {
                "category": "general",
                "token": self.settings.FINNHUB_KEY,
            }
            resp = await self.http_client.get(url, params=params, timeout=10.0)
            resp.raise_for_status()
            raw = resp.json()

            if not isinstance(raw, list):
                return [], SourceFreshness(
                    source="finnhub", status="error",
                    last_fetched=datetime.now(timezone.utc).isoformat(),
                    error="Unexpected response format",
                )

            items: list[NormalizedNewsItem] = []
            for article in raw[:50]:  # cap per source
                headline = str(article.get("headline") or "").strip()
                if not headline:
                    continue

                summary = str(article.get("summary") or "").strip()
                pub_ts = article.get("datetime")
                published_at = ""
                if pub_ts:
                    try:
                        published_at = datetime.fromtimestamp(int(pub_ts), tz=timezone.utc).isoformat()
                    except (ValueError, TypeError, OSError):
                        pass

                related = str(article.get("related") or "").strip()
                symbols = [s.strip().upper() for s in related.split(",") if s.strip()] if related else []

                text = f"{headline} {summary}".lower()
                category = self._classify_category(text)
                sentiment_score = self._score_sentiment(text)
                sentiment_label = self._label_from_score(sentiment_score)
                relevance = self._compute_relevance(headline, symbols, category)

                items.append(NormalizedNewsItem(
                    source="finnhub",
                    headline=headline,
                    summary=summary[:500],
                    url=str(article.get("url") or ""),
                    published_at=published_at,
                    symbols=symbols[:10],
                    category=category,
                    sentiment_label=sentiment_label,
                    sentiment_score=round(sentiment_score, 3),
                    relevance_score=relevance,
                ))

            logger.info("event=finnhub_news_fetched count=%d", len(items))
            return items, SourceFreshness(
                source="finnhub", status="ok",
                last_fetched=datetime.now(timezone.utc).isoformat(),
                item_count=len(items),
            )

        except Exception as exc:
            logger.warning("event=finnhub_news_error error=%s", exc)
            return [], SourceFreshness(
                source="finnhub", status="error",
                last_fetched=datetime.now(timezone.utc).isoformat(),
                error=str(exc)[:200],
            )

    # ── Polygon fetch ───────────────────────────────────────────────

    async def _fetch_polygon(self) -> tuple[list[NormalizedNewsItem], SourceFreshness]:
        """Fetch news from Polygon /v2/reference/news endpoint."""
        if not self.settings.POLYGON_API_KEY:
            return [], SourceFreshness(
                source="polygon", status="unavailable", error="No API key configured",
            )

        try:
            url = f"{self.settings.POLYGON_BASE_URL}/v2/reference/news"
            params = {
                "limit": 50,
                "order": "desc",
                "sort": "published_utc",
                "apiKey": self.settings.POLYGON_API_KEY,
            }
            resp = await self.http_client.get(url, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results") or []
            items: list[NormalizedNewsItem] = []
            for article in results[:50]:
                headline = str(article.get("title") or "").strip()
                if not headline:
                    continue

                summary = str(article.get("description") or "").strip()
                published_at = str(article.get("published_utc") or "").strip()

                tickers = article.get("tickers") or []
                symbols = [str(t).strip().upper() for t in tickers if t][:10]

                text = f"{headline} {summary}".lower()
                category = self._classify_category(text)
                sentiment_score = self._score_sentiment(text)
                sentiment_label = self._label_from_score(sentiment_score)
                relevance = self._compute_relevance(headline, symbols, category)

                items.append(NormalizedNewsItem(
                    source="polygon",
                    headline=headline,
                    summary=summary[:500],
                    url=str(article.get("article_url") or ""),
                    published_at=published_at,
                    symbols=symbols,
                    category=category,
                    sentiment_label=sentiment_label,
                    sentiment_score=round(sentiment_score, 3),
                    relevance_score=relevance,
                ))

            logger.info("event=polygon_news_fetched count=%d", len(items))
            return items, SourceFreshness(
                source="polygon", status="ok",
                last_fetched=datetime.now(timezone.utc).isoformat(),
                item_count=len(items),
            )

        except Exception as exc:
            logger.warning("event=polygon_news_error error=%s", exc)
            return [], SourceFreshness(
                source="polygon", status="error",
                last_fetched=datetime.now(timezone.utc).isoformat(),
                error=str(exc)[:200],
            )

    # ── FRED macro context ──────────────────────────────────────────

    async def _fetch_macro_context(self) -> MacroContext:
        """Fetch key macro series — prefers centralized MarketContextService."""
        ctx = MacroContext(as_of=datetime.now(timezone.utc).isoformat())

        # ── Use centralized market context when available ──
        if self.market_context_service:
            try:
                mc = await self.market_context_service.get_market_context()
                freshness_map = {}
                for attr_name, key in [
                    ("vix", "vix"),
                    ("us_10y_yield", "ten_year_yield"),
                    ("us_2y_yield", "two_year_yield"),
                    ("fed_funds_rate", "fed_funds_rate"),
                    ("oil_wti", "oil_wti"),
                    ("usd_index", "usd_index"),
                ]:
                    metric = mc.get(key)
                    if metric and metric.get("value") is not None:
                        setattr(ctx, attr_name, metric["value"])
                    # Propagate freshness envelope for each metric
                    if metric:
                        freshness_map[attr_name] = {
                            "source": metric.get("source"),
                            "freshness": metric.get("freshness", "delayed"),
                            "is_intraday": metric.get("is_intraday", False),
                            "observation_date": metric.get("observation_date"),
                            "fetched_at": metric.get("fetched_at"),
                            "previous_close": metric.get("previous_close"),
                        }
                ctx._freshness = freshness_map
                if ctx.us_10y_yield is not None and ctx.us_2y_yield is not None:
                    ctx.yield_curve_spread = round(ctx.us_10y_yield - ctx.us_2y_yield, 3)
                ctx.stress_level = self._compute_stress_level(ctx)
                return ctx
            except Exception as exc:
                logger.debug("event=market_context_fallback error=%s", exc)
                # Fall through to legacy FRED path

        if not self.fred_client:
            ctx.stress_level = "unknown"
            return ctx

        # Legacy FRED-only path (fallback)
        series_map = {
            "vix": "VIXCLS",
            "us_10y_yield": "DGS10",
            "us_2y_yield": "DGS2",
            "fed_funds_rate": "FEDFUNDS",
            "oil_wti": "DCOILWTICO",
            "usd_index": "DTWEXBGS",
        }

        for field_name, series_id in series_map.items():
            try:
                value = await self.fred_client.get_latest_series_value(series_id)
                setattr(ctx, field_name, value)
            except Exception as exc:
                logger.debug("event=fred_series_error series=%s error=%s", series_id, exc)

        # Derived: yield curve spread
        if ctx.us_10y_yield is not None and ctx.us_2y_yield is not None:
            ctx.yield_curve_spread = round(ctx.us_10y_yield - ctx.us_2y_yield, 3)

        # Compute stress level from VIX
        ctx.stress_level = self._compute_stress_level(ctx)
        return ctx

    # ── Sentiment scoring (rule-based first pass) ───────────────────

    @staticmethod
    def _score_sentiment(text: str) -> float:
        """Simple keyword-based sentiment score: -1.0 (bearish) to +1.0 (bullish)."""
        words = text.lower().split()
        word_set = set(words)
        bull_count = len(word_set & _BULLISH_WORDS)
        bear_count = len(word_set & _BEARISH_WORDS)

        # Also check multi-word phrases
        for phrase in _BULLISH_WORDS:
            if " " in phrase and phrase in text.lower():
                bull_count += 1
        for phrase in _BEARISH_WORDS:
            if " " in phrase and phrase in text.lower():
                bear_count += 1

        total = bull_count + bear_count
        if total == 0:
            return 0.0
        return (bull_count - bear_count) / total

    @staticmethod
    def _label_from_score(score: float) -> str:
        if score > 0.2:
            return "bullish"
        if score < -0.2:
            return "bearish"
        if abs(score) <= 0.05:
            return "neutral"
        return "mixed"

    @staticmethod
    def _classify_category(text: str) -> str:
        """Classify text into one of the known categories by keyword matching."""
        text_lower = text.lower()
        best_cat = "company"
        best_count = 0
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if count > best_count:
                best_count = count
                best_cat = cat
        return best_cat if best_count > 0 else "company"

    @staticmethod
    def _compute_relevance(headline: str, symbols: list[str], category: str) -> int:
        """Compute relevance score 0-100.

        Higher for: BenTrade tracked symbols (SPY, QQQ, IWM, DIA),
        macro/fed/geopolitical categories, longer headlines.
        """
        score = 30  # base
        tracked = {"SPY", "QQQ", "IWM", "DIA", "XSP", "RUT", "NDX"}
        if any(s in tracked for s in symbols):
            score += 30
        high_priority_categories = {"fed", "macro", "geopolitical", "commodities"}
        if category in high_priority_categories:
            score += 20
        if len(headline) > 60:
            score += 10
        if symbols:
            score += 10
        return min(score, 100)

    # ── Stress level (used by macro fetch, kept for backward compat) ──

    @staticmethod
    def _compute_stress_level(macro: MacroContext) -> str:
        """Compute macro stress from VIX and yield curve.

        Thresholds (VIX):
          < 16: low
          16-22: moderate
          22-30: elevated
          > 30: high

        Inverted yield curve (spread < 0) adds one stress level.
        """
        vix = macro.vix
        spread = macro.yield_curve_spread

        if vix is None:
            return "unknown"

        if vix > 30:
            level = "high"
        elif vix > 22:
            level = "elevated"
        elif vix > 16:
            level = "moderate"
        else:
            level = "low"

        if spread is not None and spread < 0:
            bump = {"low": "moderate", "moderate": "elevated", "elevated": "high"}
            level = bump.get(level, level)

        return level

    # ── Deduplication ───────────────────────────────────────────────

    @staticmethod
    def _deduplicate(items: list[NormalizedNewsItem]) -> list[NormalizedNewsItem]:
        """Remove duplicate headlines (exact match on normalized headline)."""
        seen: set[str] = set()
        result: list[NormalizedNewsItem] = []
        for item in items:
            key = item.headline.lower().strip()
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    @staticmethod
    def _item_to_dict(item: NormalizedNewsItem) -> dict[str, Any]:
        return {
            "source": item.source,
            "headline": item.headline,
            "summary": item.summary,
            "url": item.url,
            "published_at": item.published_at,
            "symbols": item.symbols,
            "category": item.category,
            "sentiment_label": item.sentiment_label,
            "sentiment_score": item.sentiment_score,
            "relevance_score": item.relevance_score,
        }
