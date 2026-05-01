"""Render a PDF from a real (or saved) CE result without the Flask hop.

Usage:
    # Live (backend running):
    python scripts/test_pdf_render.py --job-id ondemand_2026-04-15T00:11:53_MSFT_88a4

    # From a saved JSON snapshot:
    python scripts/test_pdf_render.py --job-id MOCK --from-file docs/pdf_audit/ce_result_sample.json

    # Custom outputs:
    python scripts/test_pdf_render.py --job-id <ID> --pdf-out C:/tmp/test_render.pdf \\
        --model-out C:/tmp/test_render_model.json

What it does:
    1. Fetches CE result JSON (live via proxy OR --from-file).
    2. Calls `_build_document_model` to produce the internal DocumentModel.
    3. Calls `_render_pdf` to produce PDF bytes.
    4. Writes the PDF and a JSON dump of the DocumentModel to disk so you
       can diff what the renderer is *seeing* vs. what the source result
       actually contains.

This bypasses the Flask blueprint and the CE proxy redirect chain — it
exercises ONLY `on_demand_pdf_service.py`. Ideal for fast iteration on
fixes against real captured data.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# Resolve the backend package (BenTrade/backend) so imports work regardless of cwd.
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parent.parent
_BACKEND = _REPO_ROOT / "BenTrade" / "backend"
sys.path.insert(0, str(_BACKEND))

from app.models.on_demand_pdf_payload import (  # noqa: E402
    DisplayContext,
    OnDemandPdfPayload,
)
from app.services.on_demand_pdf_service import (  # noqa: E402
    DocumentModel,
    _build_document_model,
    _render_pdf,
)


def _fetch(job_id: str, base: str, timeout: float) -> dict:
    url = (
        base.rstrip("/")
        + "/api/company-evaluator/on-demand/jobs/"
        + urllib.parse.quote(job_id, safe="")
        + "/result"
    )
    print(f"GET {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _model_to_jsonable(doc: DocumentModel) -> dict:
    """dataclasses.asdict() handles nested dataclasses; everything else
    is already JSON-friendly because the CE result is plain JSON dicts.
    """
    return dataclasses.asdict(doc)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a PDF from a CE result.")
    ap.add_argument("--job-id", required=True, help="CE job ID (or sentinel like MOCK if --from-file)")
    ap.add_argument("--from-file", default=None, help="Load CE result from a JSON file instead of HTTP")
    ap.add_argument("--base", default="http://localhost:5000", help="Backend base URL")
    ap.add_argument("--pdf-out", default="C:/tmp/test_render.pdf", help="Output PDF path")
    ap.add_argument("--model-out", default="C:/tmp/test_render_model.json", help="Output DocumentModel JSON path")
    ap.add_argument("--symbol", default=None, help="Override symbol (defaults to ce_result.company.symbol)")
    ap.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout (s)")
    args = ap.parse_args()

    if args.from_file:
        ce_result = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    else:
        try:
            ce_result = _fetch(args.job_id, args.base, args.timeout)
        except Exception as exc:
            print(f"ERROR: fetch failed: {exc}", file=sys.stderr)
            return 2

    symbol = (
        args.symbol
        or (ce_result.get("company") or {}).get("symbol")
        or args.job_id.split("_")[-2]
        if "_" in args.job_id
        else "UNKNOWN"
    )

    # Minimal payload — chart/notes/appended omitted for the harness.
    payload = OnDemandPdfPayload(
        job_id=args.job_id,
        symbol=str(symbol).upper(),
        appended_analyses=[],
        user_notes=None,
        chart_png_base64=None,
        display_context=DisplayContext(
            account_mode="paper",
            generated_at_iso=dt.datetime.now(dt.timezone.utc),
        ),
    )

    doc = _build_document_model(ce_result, payload)
    pdf_bytes = _render_pdf(doc)

    pdf_path = Path(args.pdf_out)
    model_path = Path(args.model_out)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)
    model_path.write_text(
        json.dumps(_model_to_jsonable(doc), indent=2, default=str), encoding="utf-8"
    )
    print(f"Wrote PDF: {pdf_path} ({len(pdf_bytes)} bytes)", file=sys.stderr)
    print(f"Wrote DocumentModel: {model_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
