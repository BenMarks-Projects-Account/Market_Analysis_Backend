"""Options Scanner V2 — legacy-to-V2 comparison harness.

Public API for running side-by-side comparisons between legacy (V1) and
V2 scanner families on the same market data snapshot.

Usage
-----
    from app.services.scanner_v2.comparison import (
        ComparisonReport,
        ComparisonSnapshot,
        CandidateMatch,
        compare_scanner_family,
        build_comparison_key,
        load_snapshot,
    )

Modules
-------
contracts       Report / match / snapshot data shapes.
equivalence     Candidate matching / comparison-key logic.
harness         The comparison runner.
snapshots       Snapshot loading / building utilities.
"""

from app.services.scanner_v2.comparison.contracts import (  # noqa: F401
    CandidateMatch,
    ComparisonReport,
    ComparisonSnapshot,
    MetricDelta,
    DiagnosticsDiff,
    COMPARISON_CONTRACT_VERSION,
)
from app.services.scanner_v2.comparison.equivalence import (  # noqa: F401
    build_comparison_key,
    match_candidates,
)
from app.services.scanner_v2.comparison.harness import (  # noqa: F401
    compare_scanner_family,
)
from app.services.scanner_v2.comparison.snapshots import (  # noqa: F401
    load_snapshot,
    build_snapshot,
)
