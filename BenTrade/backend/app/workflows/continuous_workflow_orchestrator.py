"""Continuous workflow orchestrator — chains MI → TMC Full Refresh in a loop.

Replaces the standalone MI cron job with a unified loop:
  1. Market Intelligence (data collection + 6 engine model analyses)
  2. TMC Full Refresh (Stock + Options + Active Trades in parallel → Portfolio Balance)
  3. Configurable delay
  4. Repeat

The orchestrator is a singleton started after the boot modal completes.
It reuses the same service dependencies that the API routes use.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.utils.market_hours import is_market_open, market_status, next_market_event, _to_et

_log = logging.getLogger("bentrade.orchestrator")


class ContinuousWorkflowOrchestrator:
    """Runs MI → TMC Full Refresh → delay → repeat."""

    def __init__(self, app: Any) -> None:
        """Initialise with the FastAPI app instance for access to app.state."""
        self._app = app
        self._running = False
        self._paused = False
        self._delay_seconds: float = 0  # Configurable delay after TMC completes
        self._cycle_count = 0
        self._current_stage = "idle"
        self._last_cycle_completed: str | None = None
        self._last_cycle_duration_ms: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._account_mode = "paper"

    # ── Public properties ────────────────────────────────────────

    @property
    def status(self) -> dict[str, Any]:
        mkt_open = is_market_open()
        return {
            "running": self._running,
            "paused": self._paused,
            "current_stage": self._current_stage,
            "cycle_count": self._cycle_count,
            "delay_seconds": self._delay_seconds,
            "last_cycle_completed": self._last_cycle_completed,
            "last_cycle_duration_ms": self._last_cycle_duration_ms,
            "account_mode": self._account_mode,
            "market_open": mkt_open,
            "market_status": market_status(),
            "next_market_event": next_market_event(),
        }

    # ── Controls ─────────────────────────────────────────────────

    def set_delay(self, seconds: float) -> None:
        """Set the delay between cycles (0 = no delay, continuous)."""
        self._delay_seconds = max(0.0, seconds)
        _log.info("event=orchestrator_delay_set delay_s=%.1f", self._delay_seconds)

    async def start(self, *, account_mode: str = "paper") -> None:
        """Start the continuous workflow loop."""
        if self._running:
            _log.warning("event=orchestrator_already_running")
            return

        self._running = True
        self._paused = False
        self._account_mode = account_mode
        _log.info(
            "event=orchestrator_start account_mode=%s delay_s=%.1f",
            account_mode,
            self._delay_seconds,
        )

        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the continuous workflow loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._current_stage = "stopped"
        _log.info("event=orchestrator_stopped cycles_completed=%d", self._cycle_count)

    def pause(self) -> None:
        """Pause the loop (current stage finishes, next cycle doesn't start)."""
        self._paused = True
        self._current_stage = "paused"
        _log.info("event=orchestrator_paused")

    def resume(self) -> None:
        """Resume the loop."""
        self._paused = False
        _log.info("event=orchestrator_resumed")

    # ── Main loop ────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Main loop: MI → TMC → delay → repeat."""
        while self._running:
            if self._paused:
                self._current_stage = "paused"
                await asyncio.sleep(1)
                continue

            cycle_start = time.time()
            self._cycle_count += 1
            _log.info("event=cycle_start cycle=%d", self._cycle_count)

            try:
                # ─── STAGE 1: Market Intelligence ───
                self._current_stage = "market_intelligence"
                _log.info("event=stage_mi_start cycle=%d", self._cycle_count)

                mi_result = await self._run_market_intelligence()

                _log.info(
                    "event=stage_mi_complete cycle=%d status=%s",
                    self._cycle_count,
                    mi_result.get("phase") if mi_result else "unknown",
                )

                if not self._running:
                    break

                # ─── STAGE 2: TMC Full Refresh (market hours only) ───
                if is_market_open():
                    self._current_stage = "tmc_stock_options_active"
                    _log.info("event=stage_tmc_start cycle=%d", self._cycle_count)

                    tmc_result = await self._run_tmc_full_refresh()

                    _log.info("event=stage_tmc_complete cycle=%d", self._cycle_count)
                else:
                    now_et = _to_et()
                    _log.info(
                        "event=tmc_skipped_market_closed cycle=%d time=%s",
                        self._cycle_count,
                        now_et.strftime("%A %I:%M %p ET"),
                    )
                    self._current_stage = "market_closed"

                if not self._running:
                    break

                # ─── Cycle complete ───
                cycle_duration = time.time() - cycle_start
                self._last_cycle_completed = datetime.now(timezone.utc).isoformat()
                self._last_cycle_duration_ms = int(cycle_duration * 1000)

                _log.info(
                    "event=cycle_complete cycle=%d duration_s=%.1f delay_s=%.1f",
                    self._cycle_count,
                    cycle_duration,
                    self._delay_seconds,
                )

                # ─── Delay before next cycle ───
                # When market is closed, both stages complete instantly;
                # use a longer sleep to avoid a tight CPU spin.
                if not is_market_open():
                    self._current_stage = "market_closed"
                    await asyncio.sleep(60)  # check once per minute outside hours
                elif self._delay_seconds > 0:
                    self._current_stage = "delay"
                    await asyncio.sleep(self._delay_seconds)
                else:
                    # Minimum yield to prevent tight spin even during market hours
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                _log.info("event=cycle_cancelled cycle=%d", self._cycle_count)
                break
            except Exception as exc:
                _log.error(
                    "event=cycle_error cycle=%d error=%s",
                    self._cycle_count,
                    exc,
                    exc_info=True,
                )
                # Wait before retrying to avoid tight error loops
                self._current_stage = "error_cooldown"
                await asyncio.sleep(30)

        self._current_stage = "stopped"

    # ── Stage 1: Market Intelligence ─────────────────────────────

    async def _run_market_intelligence(self) -> dict[str, Any]:
        """Run one full MI cycle (same as DataPopulationService._run_once).

        Delegates to the existing DataPopulationService which handles:
          Phase 1 (market_data): MI workflow — collect, engines, assemble, publish.
          Phase 2 (model_analysis): 6 per-engine LLM model analysis calls.
        """
        dps = self._app.state.data_population_service

        # Trigger a run and wait for it to complete by polling status.
        # The service internally deduplicates (skips if already running).
        await dps.trigger()

        # Poll until the cycle finishes (completed/failed).
        # The trigger kicks off a background task; we wait for it.
        max_wait = 600  # 10 min hard ceiling
        poll_interval = 2
        waited = 0
        while waited < max_wait:
            phase = dps.status.phase
            if phase in ("completed", "failed", "idle"):
                return dps.status.to_dict()
            await asyncio.sleep(poll_interval)
            waited += poll_interval

        _log.warning("event=mi_timeout waited_s=%d", waited)
        return {"phase": "timeout", "error": f"MI did not complete within {max_wait}s"}

    # ── Stage 2: TMC Full Refresh ────────────────────────────────

    async def _run_tmc_full_refresh(self) -> dict[str, Any]:
        """Run Full Refresh: Stock + Options + Active Trades parallel → Portfolio Balance.

        Mirrors the frontend handleFullRefresh() logic using the same
        service layer the API routes use.
        """
        account_mode = self._account_mode
        results: dict[str, Any] = {}

        # ── 2a: Stock + Options + Active Trades in parallel ──
        stock_task = asyncio.create_task(self._run_stock())
        options_task = asyncio.create_task(self._run_options())
        active_task = asyncio.create_task(self._run_active_trades(account_mode))

        gathered = await asyncio.gather(
            stock_task, options_task, active_task,
            return_exceptions=True,
        )

        stock_res = gathered[0] if not isinstance(gathered[0], BaseException) else None
        options_res = gathered[1] if not isinstance(gathered[1], BaseException) else None
        active_res = gathered[2] if not isinstance(gathered[2], BaseException) else None

        for i, (label, res) in enumerate([("stock", gathered[0]), ("options", gathered[1]), ("active_trades", gathered[2])]):
            if isinstance(res, BaseException):
                _log.error("event=%s_workflow_failed error=%s", label, res)

        results["stock"] = _safe_summary(stock_res)
        results["options"] = _safe_summary(options_res)
        results["active_trades"] = _safe_summary(active_res)

        # ── Check for BUY/EXECUTE notifications ──
        self._check_notifications(stock_res, options_res)

        if not self._running:
            return results

        # ── 2b: Portfolio Balance ──
        self._current_stage = "tmc_portfolio_balance"
        try:
            balance_res = await self._run_portfolio_balance(
                account_mode=account_mode,
                stock_results=stock_res,
                options_results=options_res,
                active_trade_results=active_res,
            )
            results["portfolio_balance"] = _safe_summary(balance_res)
        except Exception as exc:
            _log.error("event=portfolio_balance_failed error=%s", exc)
            results["portfolio_balance"] = {"status": "error", "error": str(exc)}

        return results

    # ── Notification check ────────────────────────────────────────

    def _check_notifications(
        self,
        stock_res: dict[str, Any] | None,
        options_res: dict[str, Any] | None,
    ) -> None:
        """Load full results from disk and feed into notification service.

        TMCExecutionResult.to_dict() only has candidate_count (no actual
        candidates), so we load the full output.json via the ReadModel
        loaders which contain the complete candidate list.
        """
        from app.services.notification_service import get_notification_service
        from app.workflows.tmc_service import (
            load_latest_stock_output,
            load_latest_options_output,
        )

        try:
            notif = get_notification_service()
            data_dir = str(self._app.state.backend_dir / "data")

            if stock_res:
                stock_full = load_latest_stock_output(data_dir)
                if stock_full:
                    notif.check_stock_results(stock_full.to_dict())
                    _log.info(
                        "event=notifications_checked_stock candidates=%d",
                        len(stock_full.candidates),
                    )

            if options_res:
                options_full = load_latest_options_output(data_dir)
                if options_full:
                    notif.check_options_results(options_full.to_dict())
                    _log.info(
                        "event=notifications_checked_options candidates=%d",
                        len(options_full.candidates),
                    )

            _log.info(
                "event=notifications_check_complete unread=%d",
                notif.get_unread_count(),
            )
        except Exception as exc:
            _log.warning("event=notification_check_failed error=%s", exc)

    # ── Individual workflow runners ──────────────────────────────

    async def _run_stock(self) -> dict[str, Any] | None:
        """Run stock opportunity workflow."""
        from app.workflows.tmc_service import TMCExecutionService

        tmc = TMCExecutionService(
            data_dir=str(self._app.state.backend_dir / "data"),
            stock_deps=getattr(self._app.state, "tmc_stock_deps", None),
            options_deps=getattr(self._app.state, "tmc_options_deps", None),
        )
        result = await tmc.run_stock_opportunities()
        return result.to_dict() if hasattr(result, "to_dict") else {"status": "completed"}

    async def _run_options(self) -> dict[str, Any] | None:
        """Run options opportunity workflow."""
        from app.workflows.tmc_service import TMCExecutionService

        tmc = TMCExecutionService(
            data_dir=str(self._app.state.backend_dir / "data"),
            stock_deps=getattr(self._app.state, "tmc_stock_deps", None),
            options_deps=getattr(self._app.state, "tmc_options_deps", None),
        )
        result = await tmc.run_options_opportunities()
        return result.to_dict() if hasattr(result, "to_dict") else {"status": "completed"}

    async def _run_active_trades(self, account_mode: str) -> dict[str, Any] | None:
        """Run active trade pipeline using the same pattern as the API route."""
        from app.api.routes_active_trades import _build_active_payload
        from app.api.routes_active_trade_pipeline import _store_result as _store_pipeline_result
        from app.services.active_trade_pipeline import run_active_trade_pipeline

        # _build_active_payload expects request.app.state — create a shim
        app_shim = _AppShim(self._app)

        payload = await _build_active_payload(app_shim, account_mode=account_mode)

        if not payload.get("ok"):
            _log.warning(
                "event=active_trades_no_positions reason=%s",
                payload.get("error", {}).get("message", "unknown"),
            )
            return {"status": "no_positions", "trade_count": 0}

        trades = payload.get("active_trades") or []
        if not trades:
            return {"status": "no_positions", "trade_count": 0}

        monitor_service = getattr(self._app.state, "active_trade_monitor_service", None)
        regime_service = getattr(self._app.state, "regime_service", None)
        base_data_service = getattr(self._app.state, "base_data_service", None)

        if not monitor_service or not regime_service or not base_data_service:
            _log.warning("event=active_trades_missing_services")
            return {"status": "services_unavailable"}

        result = await run_active_trade_pipeline(
            trades,
            monitor_service,
            regime_service,
            base_data_service,
            skip_model=False,
            positions_metadata={
                "source": "orchestrator",
                "account_mode": account_mode,
                "positions_fetched": len(trades),
            },
        )
        # Store result so GET /api/active-trade-pipeline/results can find it
        # (mirrors what the POST /run API route does).
        result.setdefault("ok", True)
        result.setdefault("account_mode", account_mode)
        _store_pipeline_result(result)
        return result

    async def _run_portfolio_balance(
        self,
        *,
        account_mode: str,
        stock_results: dict[str, Any] | None,
        options_results: dict[str, Any] | None,
        active_trade_results: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Run portfolio balance workflow."""
        from app.workflows.portfolio_balancing_runner import run_portfolio_balance_workflow

        return await run_portfolio_balance_workflow(
            request=_AppShim(self._app),
            account_mode=account_mode,
            stock_results=stock_results,
            options_results=options_results,
            active_trade_results=active_trade_results,
            skip_model=False,
        )


class _AppShim:
    """Minimal shim so functions expecting ``request.app.state`` work with the raw app."""

    def __init__(self, app: Any) -> None:
        self.app = app


def _safe_summary(result: Any) -> dict[str, Any]:
    """Extract a safe summary dict from a workflow result."""
    if result is None:
        return {"status": "skipped"}
    if isinstance(result, dict):
        return {
            "status": result.get("status", "completed"),
            "run_id": result.get("run_id"),
            "candidate_count": result.get("candidate_count"),
            "trade_count": result.get("trade_count"),
        }
    return {"status": "completed"}


# ── Singleton ────────────────────────────────────────────────────

_orchestrator: ContinuousWorkflowOrchestrator | None = None


def get_orchestrator(app: Any = None) -> ContinuousWorkflowOrchestrator:
    """Get or create the singleton orchestrator instance.

    Must be called with ``app`` the first time (during app startup).
    Subsequent calls can omit ``app``.
    """
    global _orchestrator
    if _orchestrator is None:
        if app is None:
            raise RuntimeError("Orchestrator not initialised — call get_orchestrator(app) first")
        _orchestrator = ContinuousWorkflowOrchestrator(app)
    return _orchestrator
