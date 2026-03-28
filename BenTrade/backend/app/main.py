import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_frontend import router as frontend_router
from app.api.routes_admin import router as admin_router
from app.api.routes_data_population import router as data_population_router
from app.api.routes_dev import router as dev_router
from app.api.routes_health import router as health_router
from app.api.routes_snapshots import router as snapshots_router
from app.api.routes_options import router as options_router
from app.api.routes_active_trades import router as active_trades_router
from app.api.routes_active_trade_pipeline import router as active_trade_pipeline_router
from app.api.routes_decisions import router as decisions_router
from app.api.routes_playbook import router as playbook_router
from app.api.routes_portfolio_risk import router as portfolio_risk_router
from app.api.routes_recommendations import router as recommendations_router
from app.api.routes_regime import router as regime_router
from app.api.routes_contextual_chat import router as contextual_chat_router
from app.api.routes_reports import router as reports_router
from app.api.routes_risk_capital import router as risk_capital_router
from app.api.routes_signals import router as signals_router
from app.api.routes_stock_analysis import router as stock_analysis_router
from app.api.routes_stock_strategies import router as stock_strategies_router
from app.api.routes_spreads import router as spreads_router
from app.api.routes_strategies import router as strategies_router
from app.api.routes_strategy_analytics import router as strategy_analytics_router
from app.api.routes_trade_lifecycle import router as trade_lifecycle_router
from app.api.routes_trading import router as trading_router
from app.api.routes_underlying import router as underlying_router
from app.api.routes_workbench import router as workbench_router
from app.api.routes_breadth import router as breadth_router
from app.api.routes_cross_asset_macro import router as cross_asset_macro_router
from app.api.routes_flows_positioning import router as flows_positioning_router
from app.api.routes_liquidity_conditions import router as liquidity_conditions_router
from app.api.routes_news_sentiment import router as news_sentiment_router
from app.api.routes_volatility_options import router as volatility_options_router
# NOTE: routes_pipeline_monitor removed — deprecated as part of workflow pivot (Prompt 0)
from app.api.routes_routing import router as routing_router
from app.api.routes_scanner_review import router as scanner_review_router
from app.api.routes_market_picture import router as market_picture_router
from app.api.routes_tmc import router as tmc_router
from app.clients.finnhub_client import FinnhubClient
from app.clients.fred_client import FredClient
from app.clients.futures_client import FuturesClient
from app.services.pre_market_intelligence import PreMarketIntelligenceService
from app.api.routes_pre_market import router as pre_market_router
from app.api.routes_orchestrator import router as orchestrator_router
from app.api.routes_notifications import router as notifications_router
from app.api.routes_company_evaluator import router as company_evaluator_router
from app.clients.polygon_client import PolygonClient
from app.clients.tradier_client import TradierClient
from app.config import get_settings
from app.services.base_data_service import BaseDataService
from app.services.decision_service import DecisionService
from app.services.playbook_service import PlaybookService
from app.services.risk_policy_service import RiskPolicyService
from app.services.regime_service import RegimeService
from app.services.recommendation_service import RecommendationService
from app.services.report_service import ReportService
from app.services.signal_service import SignalService
from app.services.stock_analysis_service import StockAnalysisService
from app.services.stock_execution_service import StockExecutionService
from app.services.pullback_swing_service import PullbackSwingService
from app.services.momentum_breakout_service import MomentumBreakoutService
from app.services.mean_reversion_service import MeanReversionService
from app.services.volatility_expansion_service import VolatilityExpansionService
from app.services.stock_engine_service import StockEngineService
from app.services.spread_service import SpreadService
from app.services.strategy_service import StrategyService
from app.services.trade_lifecycle_service import TradeLifecycleService
from app.services.validation_events import ValidationEventsService
from app.storage.repository import InMemoryTradingRepository
from app.trading.paper_broker import PaperBroker
from app.trading.service import TradingService
from app.trading.tradier_broker import TradierBroker
from app.utils.cache import TTLCache
from app.utils.http import UpstreamError
from app.utils.snapshot import SnapshotChainSource, SnapshotRecorder, TradierChainSource, run_snapshot_cleanup
from app.services.platform_settings import PlatformSettings
from app.services.active_trade_monitor_service import ActiveTradeMonitorService
from app.services.breadth_data_provider import BreadthDataProvider
from app.services.breadth_service import BreadthService
from app.services.cross_asset_macro_data_provider import CrossAssetMacroDataProvider
from app.services.cross_asset_macro_service import CrossAssetMacroService
from app.services.flows_positioning_data_provider import FlowsPositioningDataProvider
from app.services.flows_positioning_service import FlowsPositioningService
from app.services.liquidity_conditions_data_provider import LiquidityConditionsDataProvider
from app.services.liquidity_conditions_service import LiquidityConditionsService
from app.services.news_sentiment_service import NewsSentimentService
from app.services.market_context_service import MarketContextService
from app.services.volatility_options_data_provider import VolatilityOptionsDataProvider
from app.services.volatility_options_service import VolatilityOptionsService
from app.services.data_population_service import DataPopulationService
from app.services.model_router import async_model_request, model_request
from app.workflows.market_intelligence_runner import MarketIntelligenceDeps
from app.workflows.tmc_bootstrap import build_tmc_stock_deps, build_tmc_options_deps


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s event=%(message)s",
    )


def create_app() -> FastAPI:
    _setup_logging()
    settings = get_settings()

    app = FastAPI(title="BenTrade FastAPI Service", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    backend_dir = Path(__file__).resolve().parents[1]
    frontend_dir = backend_dir.parent / "frontend"
    results_dir = backend_dir / "results"
    snapshot_dir = Path(settings.SNAPSHOT_DIR) if settings.SNAPSHOT_DIR else backend_dir / "data" / "snapshots"

    http_client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)
    cache = TTLCache()

    tradier_client = TradierClient(settings=settings, http_client=http_client, cache=cache)
    finnhub_client = FinnhubClient(settings=settings, http_client=http_client, cache=cache)
    polygon_client = PolygonClient(settings=settings, http_client=http_client, cache=cache)
    fred_client = FredClient(settings=settings, http_client=http_client, cache=cache)
    futures_client = FuturesClient(settings=settings, cache=cache, http_client=http_client)

    # -- Snapshot chain source / recorder -----------------------------------
    _logger = logging.getLogger(__name__)

    if settings.OPTION_CHAIN_SOURCE == "snapshot":
        chain_source = SnapshotChainSource(snapshot_dir, provider="tradier")
        _logger.info("event=chain_source_mode mode=snapshot dir=%s", snapshot_dir)
    else:
        chain_source = TradierChainSource(tradier_client)

    snapshot_recorder: SnapshotRecorder | None = None
    if settings.SNAPSHOT_CAPTURE:
        _capture_syms: set[str] | None = None
        if settings.SNAPSHOT_CAPTURE_SYMBOLS:
            _capture_syms = {s.strip().upper() for s in settings.SNAPSHOT_CAPTURE_SYMBOLS.split(",") if s.strip()}
        _limit = settings.SNAPSHOT_CAPTURE_LIMIT_PER_SYMBOL or None
        snapshot_recorder = SnapshotRecorder(
            snapshot_dir,
            enabled=True,
            capture_symbols=_capture_syms if _capture_syms else None,
            limit_per_symbol=_limit,
        )
        _logger.info(
            "event=snapshot_capture_enabled symbols=%s limit=%s dir=%s",
            _capture_syms or "ALL", _limit, snapshot_dir,
        )

    base_data_service = BaseDataService(
        tradier_client=tradier_client,
        finnhub_client=finnhub_client,
        fred_client=fred_client,
        polygon_client=polygon_client,
        chain_source=chain_source,
        snapshot_recorder=snapshot_recorder,
    )

    # -- Platform settings (runtime data-source toggle) --------------------
    data_dir = backend_dir / "data"
    platform_settings = PlatformSettings(
        data_dir,
        env_default_mode="snapshot" if settings.OPTION_CHAIN_SOURCE == "snapshot" else "live",
    )
    _logger.info(
        "event=platform_settings_init mode=%s",
        platform_settings.data_source_mode,
    )

    # -- Snapshot retention cleanup on startup ─────────────────────────────
    try:
        _cleaned = run_snapshot_cleanup(snapshot_dir, retention_days=settings.SNAPSHOT_RETENTION_DAYS)
        if _cleaned:
            _logger.info("event=startup_snapshot_cleanup removed=%d", len(_cleaned))
    except Exception as _exc:
        _logger.warning("event=startup_snapshot_cleanup_error error=%s", _exc)

    signal_service = SignalService(base_data_service=base_data_service, cache=cache, ttl_seconds=45)
    spread_service = SpreadService(base_data_service=base_data_service)
    stock_analysis_service = StockAnalysisService(base_data_service=base_data_service, results_dir=results_dir, signal_service=signal_service)
    pullback_swing_service = PullbackSwingService(base_data_service=base_data_service)
    momentum_breakout_service = MomentumBreakoutService(base_data_service=base_data_service)
    mean_reversion_service = MeanReversionService(base_data_service=base_data_service)
    volatility_expansion_service = VolatilityExpansionService(base_data_service=base_data_service)
    stock_engine_service = StockEngineService(
        pullback_swing_service=pullback_swing_service,
        momentum_breakout_service=momentum_breakout_service,
        mean_reversion_service=mean_reversion_service,
        volatility_expansion_service=volatility_expansion_service,
    )
    trade_lifecycle_service = TradeLifecycleService(results_dir=results_dir)
    risk_policy_service = RiskPolicyService(results_dir=results_dir)
    regime_service = RegimeService(base_data_service=base_data_service, cache=cache, ttl_seconds=45)
    active_trade_monitor_service = ActiveTradeMonitorService(
        base_data_service=base_data_service,
        regime_service=regime_service,
        cache=cache,
        ttl_seconds=45,
    )
    strategy_service = StrategyService(
        base_data_service=base_data_service,
        results_dir=results_dir,
        risk_policy_service=risk_policy_service,
        signal_service=signal_service,
        regime_service=regime_service,
        platform_settings=platform_settings,
        snapshot_dir=snapshot_dir,
    )
    playbook_service = PlaybookService(regime_service=regime_service, signal_service=signal_service)
    recommendation_service = RecommendationService(
        strategy_service=strategy_service,
        stock_analysis_service=stock_analysis_service,
        regime_service=regime_service,
    )
    report_service = ReportService(base_data_service=base_data_service, results_dir=results_dir)
    decision_service = DecisionService(results_dir=results_dir)
    validation_events_service = ValidationEventsService(results_dir=results_dir)
    trading_repository = InMemoryTradingRepository()
    paper_broker = PaperBroker()
    tradier_broker = TradierBroker(
        settings=settings,
        http_client=http_client,
        dry_run=True,  # default safe: service.py overrides per-call via TRADIER_EXECUTION_ENABLED
    )
    trading_service = TradingService(
        settings=settings,
        base_data_service=base_data_service,
        repository=trading_repository,
        paper_broker=paper_broker,
        live_broker=tradier_broker,
        risk_policy_service=risk_policy_service,
    )
    stock_execution_service = StockExecutionService(
        settings=settings,
        http_client=http_client,
        repository=trading_repository,
    )

    app.state.http_client = http_client
    app.state.cache = cache
    app.state.tradier_client = tradier_client
    app.state.finnhub_client = finnhub_client
    app.state.polygon_client = polygon_client
    app.state.fred_client = fred_client
    app.state.futures_client = futures_client
    pre_market_service = PreMarketIntelligenceService(
        futures_client=futures_client,
        cache=cache,
    )
    app.state.pre_market_service = pre_market_service
    app.state.base_data_service = base_data_service
    app.state.signal_service = signal_service
    app.state.spread_service = spread_service
    app.state.stock_analysis_service = stock_analysis_service
    app.state.pullback_swing_service = pullback_swing_service
    app.state.momentum_breakout_service = momentum_breakout_service
    app.state.mean_reversion_service = mean_reversion_service
    app.state.volatility_expansion_service = volatility_expansion_service
    app.state.strategy_service = strategy_service
    app.state.trade_lifecycle_service = trade_lifecycle_service
    app.state.risk_policy_service = risk_policy_service
    app.state.regime_service = regime_service
    app.state.playbook_service = playbook_service
    app.state.recommendation_service = recommendation_service
    app.state.report_service = report_service
    app.state.decision_service = decision_service
    app.state.validation_events = validation_events_service
    app.state.trading_repository = trading_repository
    app.state.trading_service = trading_service
    app.state.stock_execution_service = stock_execution_service
    app.state.stock_engine_service = stock_engine_service
    app.state.active_trade_monitor_service = active_trade_monitor_service
    market_context_service = MarketContextService(
        fred_client=fred_client,
        finnhub_client=finnhub_client,
        cache=cache,
        tradier_client=tradier_client,
    )
    app.state.market_context_service = market_context_service
    news_sentiment_service = NewsSentimentService(
        settings=settings,
        http_client=http_client,
        cache=cache,
        fred_client=fred_client,
        market_context_service=market_context_service,
    )
    app.state.news_sentiment_service = news_sentiment_service

    breadth_data_provider = BreadthDataProvider(tradier_client=tradier_client)
    breadth_service = BreadthService(
        data_provider=breadth_data_provider,
        cache=cache,
    )
    app.state.breadth_service = breadth_service

    vol_data_provider = VolatilityOptionsDataProvider(
        tradier_client=tradier_client,
        market_context_service=market_context_service,
        fred_client=fred_client,
        futures_client=futures_client,
    )
    vol_service = VolatilityOptionsService(
        data_provider=vol_data_provider,
        cache=cache,
    )
    app.state.volatility_options_service = vol_service

    cross_asset_data_provider = CrossAssetMacroDataProvider(
        market_context_service=market_context_service,
        fred_client=fred_client,
        futures_client=futures_client,
    )
    cross_asset_macro_service = CrossAssetMacroService(
        data_provider=cross_asset_data_provider,
        cache=cache,
    )
    app.state.cross_asset_macro_service = cross_asset_macro_service

    flows_data_provider = FlowsPositioningDataProvider(
        market_context_service=market_context_service,
    )
    flows_positioning_service = FlowsPositioningService(
        data_provider=flows_data_provider,
        cache=cache,
    )
    app.state.flows_positioning_service = flows_positioning_service

    liquidity_conditions_data_provider = LiquidityConditionsDataProvider(
        market_context_service=market_context_service,
    )
    liquidity_conditions_service = LiquidityConditionsService(
        data_provider=liquidity_conditions_data_provider,
        cache=cache,
    )
    app.state.liquidity_conditions_service = liquidity_conditions_service

    # Late-bind MI engine services to RegimeService (constructed earlier)
    regime_service.bind_engines(
        breadth_service=breadth_service,
        volatility_options_service=vol_service,
        cross_asset_macro_service=cross_asset_macro_service,
        flows_positioning_service=flows_positioning_service,
        liquidity_conditions_service=liquidity_conditions_service,
        news_sentiment_service=news_sentiment_service,
    )

    app.state.settings = settings
    app.state.backend_dir = backend_dir
    app.state.frontend_dir = frontend_dir
    app.state.results_dir = results_dir
    app.state.snapshot_dir = snapshot_dir
    app.state.platform_settings = platform_settings

    # -- TMC workflow dependencies (Prompt 10.5) ----------------------------
    app.state.tmc_stock_deps = build_tmc_stock_deps(
        stock_engine_service=stock_engine_service,
        model_request_fn=model_request,
    )
    app.state.tmc_options_deps = build_tmc_options_deps(
        base_data_service=base_data_service,
        finnhub_client=finnhub_client,
    )

    # -- Data Population service (MI scheduler) ----------------------------
    # Use adaptive wrapper that checks routing_enabled per-request (Step 14).
    # This removes the restart requirement when ROUTING_ENABLED changes.
    from app.services.model_routing_integration import adaptive_routed_model_interpretation
    mi_model_fn = adaptive_routed_model_interpretation

    mi_deps = MarketIntelligenceDeps(
        market_context_service=market_context_service,
        breadth_service=breadth_service,
        volatility_options_service=vol_service,
        cross_asset_macro_service=cross_asset_macro_service,
        flows_positioning_service=flows_positioning_service,
        liquidity_conditions_service=liquidity_conditions_service,
        news_sentiment_service=news_sentiment_service,
        http_client=http_client,
        model_request_fn=mi_model_fn,
        pre_market_service=pre_market_service,
    )
    data_population_service = DataPopulationService(
        data_dir=data_dir,
        mi_deps=mi_deps,
    )
    app.state.data_population_service = data_population_service

    app.include_router(health_router)
    app.include_router(options_router)
    app.include_router(underlying_router)
    app.include_router(spreads_router)
    app.include_router(stock_analysis_router)
    app.include_router(stock_strategies_router)
    app.include_router(signals_router)
    app.include_router(playbook_router)
    app.include_router(strategies_router)
    app.include_router(portfolio_risk_router)
    app.include_router(risk_capital_router)
    app.include_router(regime_router)
    app.include_router(recommendations_router)
    app.include_router(trade_lifecycle_router)
    app.include_router(trading_router)
    app.include_router(active_trades_router)
    app.include_router(active_trade_pipeline_router)
    app.include_router(workbench_router)
    app.include_router(strategy_analytics_router)
    app.include_router(reports_router)
    app.include_router(decisions_router)
    app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
    app.include_router(news_sentiment_router)
    app.include_router(breadth_router)
    app.include_router(volatility_options_router)
    app.include_router(cross_asset_macro_router)
    app.include_router(flows_positioning_router)
    app.include_router(liquidity_conditions_router)
    app.include_router(snapshots_router)
    # NOTE: pipeline_monitor_router removed — deprecated as part of workflow pivot (Prompt 0)
    app.include_router(scanner_review_router)
    app.include_router(tmc_router)
    app.include_router(market_picture_router)
    app.include_router(pre_market_router)
    app.include_router(routing_router, prefix="/api/admin", tags=["routing"])
    app.include_router(data_population_router)
    app.include_router(contextual_chat_router)
    app.include_router(dev_router)
    app.include_router(orchestrator_router)
    app.include_router(notifications_router)
    app.include_router(company_evaluator_router)
    app.include_router(frontend_router)

    @app.exception_handler(UpstreamError)
    async def upstream_error_handler(_: Request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": str(exc),
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        # Preserve structured detail (dicts) for trading error responses
        if isinstance(exc.detail, dict):
            message = exc.detail.get("message", str(exc.detail))
            details = {k: v for k, v in exc.detail.items() if k != "message"}
        else:
            message = str(exc.detail)
            details = {}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "HTTP_ERROR",
                    "message": message,
                    "details": details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": {"issues": exc.errors()},
                }
            },
        )

    @app.on_event("startup")
    async def _startup() -> None:
        await app.state.data_population_service.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await app.state.data_population_service.stop()
        await app.state.http_client.aclose()

    return app


app = create_app()
