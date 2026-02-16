import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes_frontend import router as frontend_router
from app.api.routes_health import router as health_router
from app.api.routes_options import router as options_router
from app.api.routes_active_trades import router as active_trades_router
from app.api.routes_decisions import router as decisions_router
from app.api.routes_portfolio_risk import router as portfolio_risk_router
from app.api.routes_reports import router as reports_router
from app.api.routes_risk_capital import router as risk_capital_router
from app.api.routes_stock_analysis import router as stock_analysis_router
from app.api.routes_spreads import router as spreads_router
from app.api.routes_strategies import router as strategies_router
from app.api.routes_strategy_analytics import router as strategy_analytics_router
from app.api.routes_trade_lifecycle import router as trade_lifecycle_router
from app.api.routes_trading import router as trading_router
from app.api.routes_underlying import router as underlying_router
from app.api.routes_workbench import router as workbench_router
from app.clients.finnhub_client import FinnhubClient
from app.clients.fred_client import FredClient
from app.clients.tradier_client import TradierClient
from app.clients.yahoo_client import YahooClient
from app.config import get_settings
from app.services.base_data_service import BaseDataService
from app.services.decision_service import DecisionService
from app.services.risk_policy_service import RiskPolicyService
from app.services.report_service import ReportService
from app.services.stock_analysis_service import StockAnalysisService
from app.services.spread_service import SpreadService
from app.services.strategy_service import StrategyService
from app.services.trade_lifecycle_service import TradeLifecycleService
from app.storage.repository import InMemoryTradingRepository
from app.trading.paper_broker import PaperBroker
from app.trading.service import TradingService
from app.trading.tradier_broker import TradierBroker
from app.utils.cache import TTLCache
from app.utils.http import UpstreamError


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s event=%(message)s",
    )


def create_app() -> FastAPI:
    _setup_logging()
    settings = get_settings()

    app = FastAPI(title="BenTrade FastAPI Service", version="0.1.0")

    backend_dir = Path(__file__).resolve().parents[1]
    frontend_dir = backend_dir.parent / "frontend"
    results_dir = backend_dir / "results"

    http_client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)
    cache = TTLCache()

    tradier_client = TradierClient(settings=settings, http_client=http_client, cache=cache)
    finnhub_client = FinnhubClient(settings=settings, http_client=http_client, cache=cache)
    yahoo_client = YahooClient(settings=settings, cache=cache)
    fred_client = FredClient(settings=settings, http_client=http_client, cache=cache)

    base_data_service = BaseDataService(
        tradier_client=tradier_client,
        finnhub_client=finnhub_client,
        yahoo_client=yahoo_client,
        fred_client=fred_client,
    )
    spread_service = SpreadService(base_data_service=base_data_service)
    stock_analysis_service = StockAnalysisService(base_data_service=base_data_service, results_dir=results_dir)
    trade_lifecycle_service = TradeLifecycleService(results_dir=results_dir)
    risk_policy_service = RiskPolicyService(results_dir=results_dir)
    strategy_service = StrategyService(
        base_data_service=base_data_service,
        results_dir=results_dir,
        risk_policy_service=risk_policy_service,
    )
    report_service = ReportService(base_data_service=base_data_service, results_dir=results_dir)
    decision_service = DecisionService(results_dir=results_dir)
    trading_repository = InMemoryTradingRepository()
    paper_broker = PaperBroker()
    tradier_broker = TradierBroker(
        settings=settings,
        http_client=http_client,
        dry_run=settings.TRADIER_DRY_RUN_LIVE,
    )
    trading_service = TradingService(
        settings=settings,
        base_data_service=base_data_service,
        repository=trading_repository,
        paper_broker=paper_broker,
        live_broker=tradier_broker,
    )

    app.state.http_client = http_client
    app.state.tradier_client = tradier_client
    app.state.finnhub_client = finnhub_client
    app.state.yahoo_client = yahoo_client
    app.state.fred_client = fred_client
    app.state.base_data_service = base_data_service
    app.state.spread_service = spread_service
    app.state.stock_analysis_service = stock_analysis_service
    app.state.strategy_service = strategy_service
    app.state.trade_lifecycle_service = trade_lifecycle_service
    app.state.risk_policy_service = risk_policy_service
    app.state.report_service = report_service
    app.state.decision_service = decision_service
    app.state.trading_repository = trading_repository
    app.state.trading_service = trading_service
    app.state.backend_dir = backend_dir
    app.state.frontend_dir = frontend_dir
    app.state.results_dir = results_dir

    app.include_router(health_router)
    app.include_router(options_router)
    app.include_router(underlying_router)
    app.include_router(spreads_router)
    app.include_router(stock_analysis_router)
    app.include_router(strategies_router)
    app.include_router(portfolio_risk_router)
    app.include_router(risk_capital_router)
    app.include_router(trade_lifecycle_router)
    app.include_router(trading_router)
    app.include_router(active_trades_router)
    app.include_router(workbench_router)
    app.include_router(strategy_analytics_router)
    app.include_router(reports_router)
    app.include_router(decisions_router)
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
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "HTTP_ERROR",
                    "message": str(exc.detail),
                    "details": {},
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

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await app.state.http_client.aclose()

    return app


app = create_app()
