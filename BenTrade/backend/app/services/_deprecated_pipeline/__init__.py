"""
DEPRECATED — Pipeline UI/Runtime Orchestration (Workflow Pivot, Prompt 0)

This package contains the quarantined trade-building pipeline runtime modules.
These were removed from the production path as part of the BenTrade workflow pivot.

DO NOT:
  - Import from this package in new code
  - Use these modules as a foundation for new workflow design
  - Re-enable these modules as production entrypoints

The new workflow architecture (file-backed, JSON-inspectable, decoupled) will be
built fresh in subsequent prompts without reference to this deprecated runtime.

Preserved reusable domain logic (scanners, market engines, contracts, validation,
trust hygiene, EV/risk math) remains in their original locations under app/services/,
app/utils/, and common/.
"""
