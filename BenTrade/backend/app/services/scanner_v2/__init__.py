"""Options Scanner Core V2 — public API surface.

Import the key types and runner from here:

    from app.services.scanner_v2 import (
        V2Candidate,
        V2Diagnostics,
        V2Leg,
        V2RecomputedMath,
        V2CheckResult,
        V2ScanResult,
        SCANNER_V2_CONTRACT_VERSION,
        # Diagnostics framework (Prompt 5)
        V2DiagnosticItem,
        DiagnosticsBuilder,
    )
"""

from app.services.scanner_v2.contracts import (  # noqa: F401
    SCANNER_V2_CONTRACT_VERSION,
    V2Candidate,
    V2CheckResult,
    V2Diagnostics,
    V2Leg,
    V2RecomputedMath,
    V2ScanResult,
)
from app.services.scanner_v2.diagnostics import (  # noqa: F401
    DiagnosticsBuilder,
    V2DiagnosticItem,
)
