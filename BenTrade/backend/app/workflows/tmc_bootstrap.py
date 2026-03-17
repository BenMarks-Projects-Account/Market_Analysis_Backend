"""TMC workflow bootstrap — dependency builders for app startup.

Constructs the dependency bundles that TMCExecutionService needs to
actually execute Stock and Options workflow runners.

Usage in app startup::

    from app.workflows.tmc_bootstrap import build_tmc_stock_deps, build_tmc_options_deps

    app.state.tmc_stock_deps = build_tmc_stock_deps(
        stock_engine_service=stock_engine_service,
    )
    app.state.tmc_options_deps = build_tmc_options_deps(
        base_data_service=base_data_service,
    )

This module is the single place that constructs TMC workflow deps.
No scattered inline construction elsewhere.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("bentrade.tmc_bootstrap")


def build_tmc_stock_deps(*, stock_engine_service: Any, model_request_fn: Any = None) -> Any:
    """Build the StockOpportunityDeps bundle for TMC stock workflow execution.

    Parameters
    ----------
    stock_engine_service
        The ``StockEngineService`` instance that orchestrates the four
        stock scanners (pullback_swing, momentum_breakout, mean_reversion,
        volatility_expansion).  Already constructed during app startup.
    model_request_fn
        Optional callable for synchronous LLM model requests.  When
        provided, the runner's model-analysis stage will call the LLM for
        each selected candidate.  When ``None``, model analysis degrades
        gracefully.  Signature: ``(payload: dict) -> dict``.

    Returns
    -------
    StockOpportunityDeps
        Ready-to-use dependency bundle for ``run_stock_opportunity()``.
    """
    from app.workflows.stock_opportunity_runner import StockOpportunityDeps

    deps = StockOpportunityDeps(
        stock_engine_service=stock_engine_service,
        model_request_fn=model_request_fn,
    )
    _log.info("event=tmc_stock_deps_built model_analysis=%s", "enabled" if model_request_fn else "disabled")
    return deps


def build_tmc_options_deps(*, base_data_service: Any) -> Any:
    """Build the OptionsOpportunityDeps bundle for TMC options workflow execution.

    Parameters
    ----------
    base_data_service
        The ``BaseDataService`` instance that provides Tradier chain access.
        Used to construct the ``OptionsScannerService`` adapter.

    Returns
    -------
    OptionsOpportunityDeps
        Ready-to-use dependency bundle for ``run_options_opportunity()``.
    """
    from app.services.options_scanner_service import OptionsScannerService
    from app.workflows.options_opportunity_runner import OptionsOpportunityDeps

    options_scanner_service = OptionsScannerService(base_data_service=base_data_service)
    deps = OptionsOpportunityDeps(options_scanner_service=options_scanner_service)
    _log.info("event=tmc_options_deps_built")
    return deps
