"""Dump a sanitized CE on-demand result to disk for the PDF audit.

Usage:
    python scripts/dump_ce_result.py --job-id ondemand_2026-04-15T00:11:53_MSFT_88a4
    python scripts/dump_ce_result.py --job-id <ID> --out docs/pdf_audit/ce_result_sample.json
    python scripts/dump_ce_result.py --job-id <ID> --base http://localhost:5000

Notes:
- Requires the BenTrade backend (Flask) to be running on `--base` (default
  http://localhost:5000) so the CE proxy can resolve.
- Redacts only obvious key-like strings (anything matching /api[_-]?key/i
  or "secret"/"token" in keys). Keep the output local; do not commit it
  to git unless you've reviewed it yourself.
- Pretty-prints with sort_keys=False to preserve the wire ordering, which
  helps when correlating to the audit key-tree.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from typing import Any

REDACT_KEY_SUBSTRINGS = ("api_key", "apikey", "secret", "token", "password")
REDACT_PLACEHOLDER = "***REDACTED***"


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(s in kl for s in REDACT_KEY_SUBSTRINGS):
                out[k] = REDACT_PLACEHOLDER
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump a CE on-demand result JSON.")
    ap.add_argument("--job-id", required=True, help="CE on-demand job ID")
    ap.add_argument("--base", default="http://localhost:5000", help="Backend base URL")
    ap.add_argument("--out", default="-", help="Output path or '-' for stdout")
    ap.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout (s)")
    args = ap.parse_args()

    url = (
        args.base.rstrip("/")
        + "/api/company-evaluator/on-demand/jobs/"
        + urllib.parse.quote(args.job_id, safe="")
        + "/result"
    )
    print(f"GET {url}", file=sys.stderr)
    try:
        with urllib.request.urlopen(url, timeout=args.timeout) as resp:
            raw = resp.read()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        data = json.loads(raw)
    except Exception as exc:
        print(f"ERROR: response is not JSON: {exc}", file=sys.stderr)
        return 3

    redacted = _redact(data)
    text = json.dumps(redacted, indent=2, sort_keys=False, default=str)

    if args.out == "-":
        sys.stdout.write(text)
        sys.stdout.write("\n")
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
        print(f"Wrote {len(text)} bytes to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
