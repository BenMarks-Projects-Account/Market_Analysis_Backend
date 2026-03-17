"""BenTrade Workflow Architecture — Greenfield (Workflow Pivot)

This package defines the split workflow architecture introduced after
removal of the old trade-building pipeline UI/runtime orchestration.

Workflow domains:
    A. Market Intelligence Producer  — scheduled, shared infrastructure
    B. Stock Opportunity Workflow    — independent, consumes market state
    C. Options Opportunity Workflow  — independent, consumes market state
    D. Active Trade Workflow         — unchanged / out of scope for rebuild

See ``definitions.py`` for workflow constants and stage maps.
See ``architecture.py`` for source-of-truth boundaries and design rules.
See ``market_state_contract.py`` for the canonical market_state.json shape.
See ``market_state_discovery.py`` for latest-valid artifact discovery rules.
See ``artifact_strategy.py`` for the file-backed artifact strategy across all workflows.
See ``market_intelligence_runner.py`` for the scheduled MI workflow runner (Prompt 4).
See ``market_state_consumer.py`` for the reusable market-state consumer loading seam (Prompt 5).
See ``stock_opportunity_runner.py`` for the stock opportunity workflow runner (Prompt 5).
See ``options_opportunity_runner.py`` for the options opportunity workflow runner (Prompt 6).
See ``tmc_service.py`` for the TMC execution seam and compact read models (Prompt 7).

IMPORTANT:
    Do NOT import from ``app.services._deprecated_pipeline``.
    Do NOT replicate old pipeline orchestration patterns.
    All new workflow code in this package is greenfield.
"""
