"""On-Demand Evaluator PDF export service (Phase 1).

Renders a deterministic PDF from:
    * The cached CE analysis result (re-fetched via the existing proxy
      ``GET /api/company-evaluator/on-demand/jobs/{job_id}/result``).
      NO new CE run, NO polling.
    * Browser-side state sent in the OnDemandPdfPayload (appended analyses,
      display context).

Pipeline:
    render_on_demand_pdf(payload)
        → _fetch_ce_result(job_id)          # httpx GET, 30s timeout
        → _build_document_model(ce, pl)     # pure function (testable)
        → _render_pdf(document)             # fpdf2 imperative renderer
        → _enforce_size_cap(bytes)          # 20MB hard cap

Phase 1 scope:
    * Minimal styling (fpdf2 defaults), Helvetica only, no page numbering,
      no headers/footers (that is Phase 2).
    * No Jinja template — the document is produced imperatively from the
      DocumentModel. This keeps the dep surface tiny (no WeasyPrint/GTK).
    * Markdown in appended analyses is flattened to plain text with
      paragraph breaks preserved (no bold/italic/headings). Phase 2 adds
      real markdown rendering.
    * No glossary, no chart image, no raw JSON dumps.

CE result shape (top-level keys consumed):
    company, evaluation, dcf, eva, comps, entry_analysis, price_targets,
    llm_recommendation, raw_financials.company_data.financials_annual.
    statements[] (flat per-period objects — income/balance/cashflow fields
    are all co-mingled and partitioned here into three tables).

Phase 2.2 (table-pagination hotfix):
    * _DocPDF.header() now calls a registered per-table callback so the
      year header row repeats on auto-page-break as well as on
      manually-triggered page breaks. Fixes Balance Sheet / Cash Flow
      rendering without year headers.
    * _render_financials no longer force-breaks between statements; a
      keep-together check replaces the unconditional pdf.add_page()
      pair that was creating huge blank gaps.
    * _kv_table guards each row against overflow to prevent label/value
      orphaning across pages.
"""
from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
from fpdf import FPDF

from app.models.on_demand_pdf_payload import OnDemandPdfPayload

logger = logging.getLogger(__name__)


# ── Error taxonomy ────────────────────────────────────────────────────
class CEJobNotFoundError(Exception):
    """CE returned 404 for the requested job_id."""


class CEUnreachableError(Exception):
    """CE service could not be reached or returned a non-404 error."""


class PDFTooLargeError(Exception):
    """Rendered PDF exceeded the hard size cap."""


# ── Config ────────────────────────────────────────────────────────────
MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB
CE_FETCH_TIMEOUT_S = 30.0

# Through the BenTrade CE proxy — the proxy handles base-URL resolution
# and connection-error translation for us.
_CE_PROXY_URL = "http://localhost:5000/api/company-evaluator/on-demand/jobs/{job_id}/result"

# Statement field partitioning — must mirror the frontend's
# INCOME_STATEMENT_FIELDS / BALANCE_SHEET_FIELDS / CASH_FLOW_FIELDS in
# frontend/assets/js/pages/on_demand_evaluator.js around line 1665-1684.
INCOME_STATEMENT_FIELDS: tuple[str, ...] = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_expenses",
    "research_and_development",
    "selling_general_administrative",
    "operating_income",
    "income_before_tax",
    "income_tax",
    "net_income",
    "eps_basic",
    "eps_diluted",
    "basic_avg_shares",
    "diluted_avg_shares",
)
BALANCE_SHEET_FIELDS: tuple[str, ...] = (
    "total_assets",
    "current_assets",
    "noncurrent_assets",
    "fixed_assets",
    "inventory",
    "accounts_payable",
    "total_liabilities",
    "current_liabilities",
    "noncurrent_liabilities",
    "long_term_debt",
    "total_equity",
    "equity_parent",
)
CASH_FLOW_FIELDS: tuple[str, ...] = (
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "net_cash_flow",
    "free_cash_flow",
)


# ── DocumentModel (internal) ──────────────────────────────────────────
@dataclass
class StatementTable:
    """One of income/balance/cashflow — periods are columns, fields rows."""
    periods: list[str] = field(default_factory=list)          # e.g. ["2024-12-31", "2023-12-31"]
    rows: list[tuple[str, list[Any]]] = field(default_factory=list)  # (field_name, [v_period1, v_period2, ...])


@dataclass
class FinancialStatements:
    income_statement: Optional[StatementTable] = None
    balance_sheet: Optional[StatementTable] = None
    cash_flow: Optional[StatementTable] = None


@dataclass
class CompanyOverview:
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    price: Optional[float] = None


@dataclass
class DocumentModel:
    header_symbol: str
    generated_at_iso: str
    account_mode: Optional[str]
    ce_job_id: str
    company_overview: CompanyOverview
    quality_signals: Optional[dict[str, Any]] = None
    dcf: Optional[dict[str, Any]] = None
    eva: Optional[dict[str, Any]] = None
    comps: Optional[dict[str, Any]] = None
    pillar_breakdown: Optional[dict[str, Any]] = None
    entry_price_targets: Optional[dict[str, Any]] = None
    financial_statements: FinancialStatements = field(default_factory=FinancialStatements)
    ai_thesis: Optional[dict[str, Any]] = None
    appended_analyses: list[dict[str, Any]] = field(default_factory=list)
    user_notes: Optional[str] = None
    # Phase 2 Fix 6: client-captured chart PNG (raw base64, no data: prefix).
    chart_png_base64: Optional[str] = None


# ── Pure helpers ──────────────────────────────────────────────────────
def _safe_get(d: Any, *keys: str) -> Any:
    """Traverse a nested dict with dotted-path safety. Returns None on miss."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _extract_year_from_date(value: Any) -> Optional[int]:
    """Phase 2.1 Fix A: pull a 4-digit year out of an ISO-ish date string.

    Handles "2024-09-30", "2024/09/30", "09/30/2024", "2024" — anything
    where a 4-digit substring 1900-2099 appears. Returns None on no match.
    """
    if value is None:
        return None
    s = str(value)
    m = re.search(r"(19|20)\d{2}", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _partition_statements(
    statements: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> Optional[StatementTable]:
    """Slice a flat per-period statements list into a StatementTable for
    the given field set. Returns None if statements is empty.

    Period label priority (Phase 2.1 Fix A — four-tier fallback so the
    column header is always meaningful):
        1. fiscal_year + fiscal_period (frontend pattern)
        2. year parsed from period / start_date / end_date string
        3. raw period / start_date / end_date string as-is
        4. em-dash placeholder
    """
    if not statements:
        return None
    periods: list[str] = []
    for s in statements:
        yr = s.get("fiscal_year")
        fp = s.get("fiscal_period") or ""
        # Tier 1: explicit fiscal_year (+ optional fiscal_period)
        if yr:
            label = f"FY{yr}" if (fp == "FY" or not fp) else f"{fp} {yr}"
        else:
            # Tier 2: parse year from any available date field
            raw_date = s.get("period") or s.get("start_date") or s.get("end_date")
            parsed_yr = _extract_year_from_date(raw_date)
            if parsed_yr is not None:
                label = f"FY{parsed_yr}" if (fp == "FY" or not fp) else f"{fp} {parsed_yr}"
            elif raw_date:
                # Tier 3: raw date string as-is
                label = str(raw_date)
            else:
                # Tier 4: nothing usable
                label = "—"
        periods.append(label)
    rows: list[tuple[str, list[Any]]] = []
    for fname in fields:
        values = [s.get(fname) for s in statements]
        # Keep all rows including all-None (so sparse-data cases render
        # "—" instead of silently hiding rows). Phase 2 may filter.
        rows.append((fname, values))
    return StatementTable(periods=periods, rows=rows)


def _build_document_model(
    ce_result: dict[str, Any],
    payload: OnDemandPdfPayload,
) -> DocumentModel:
    """Pure function: (CE result dict, payload) → DocumentModel.

    Missing CE sections → None fields. Never raises on missing data.
    """
    company = ce_result.get("company") or {}
    # Price fallback chain mirrors the frontend (on_demand_evaluator.js L378-L382)
    price = company.get("price")
    if price is None:
        price = _safe_get(ce_result, "dcf", "current_price")
    if price is None:
        price = _safe_get(ce_result, "entry_analysis", "current_price")
    # Sector fallback mirrors L385
    sector = _safe_get(ce_result, "comps", "subject", "sector") or company.get("sector")

    overview = CompanyOverview(
        symbol=payload.symbol,
        name=company.get("name") or company.get("company_name"),
        sector=sector,
        industry=company.get("industry"),
        price=price if isinstance(price, (int, float)) else None,
    )

    # Financial statements — partition the flat per-period rows
    statements = (
        _safe_get(ce_result, "raw_financials", "company_data", "financials_annual", "statements")
        or []
    )
    fs = FinancialStatements(
        income_statement=_partition_statements(statements, INCOME_STATEMENT_FIELDS),
        balance_sheet=_partition_statements(statements, BALANCE_SHEET_FIELDS),
        cash_flow=_partition_statements(statements, CASH_FLOW_FIELDS),
    )

    # Appended analyses → list of plain dicts (preserves order)
    appended = [
        {
            "timestamp": a.timestamp.isoformat(),
            "title": a.title,
            "body_md": a.body_md,
        }
        for a in payload.appended_analyses
    ]

    return DocumentModel(
        header_symbol=payload.symbol,
        generated_at_iso=payload.display_context.generated_at_iso.isoformat(),
        account_mode=payload.display_context.account_mode,
        ce_job_id=payload.job_id,
        company_overview=overview,
        quality_signals=ce_result.get("quality_signals"),
        dcf=ce_result.get("dcf"),
        eva=ce_result.get("eva"),
        comps=ce_result.get("comps"),
        pillar_breakdown=ce_result.get("evaluation"),
        entry_price_targets={
            "entry_analysis": ce_result.get("entry_analysis"),
            "price_targets": ce_result.get("price_targets"),
        } if (ce_result.get("entry_analysis") or ce_result.get("price_targets")) else None,
        financial_statements=fs,
        ai_thesis=ce_result.get("llm_recommendation"),
        appended_analyses=appended,
        user_notes=payload.user_notes,
        chart_png_base64=payload.chart_png_base64,
    )


# ── Markdown → plain text (Phase 1: flatten) ──────────────────────────
_MD_LINE_PATTERNS = [
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),          # ATX headings
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), "• "),       # bullets
    (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),         # ordered list markers
    (re.compile(r"^\s*>\s?", re.MULTILINE), ""),             # blockquote
]
_MD_INLINE_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),                   # bold
    (re.compile(r"__(.+?)__"), r"\1"),
    (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"), r"\1"),  # italic
    (re.compile(r"_(.+?)_"), r"\1"),
    (re.compile(r"`([^`]+)`"), r"\1"),                        # inline code
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),            # links
    (re.compile(r"!\[([^\]]*)\]\([^)]+\)"), r"\1"),           # images
]


def _markdown_to_plain(md: str) -> str:
    """Flatten markdown to plain text preserving paragraph breaks.

    Phase 1: no real markdown rendering. Strips formatting syntax and
    leaves bullet markers as "• " so lists remain readable.
    """
    if not md:
        return ""
    text = md.replace("\r\n", "\n").replace("\r", "\n")
    # Fenced code: drop the fence markers, keep contents
    text = re.sub(r"^```[^\n]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```$", "", text, flags=re.MULTILINE)
    for pat, repl in _MD_LINE_PATTERNS:
        text = pat.sub(repl, text)
    for pat, repl in _MD_INLINE_PATTERNS:
        text = pat.sub(repl, text)
    # Collapse 3+ newlines → 2 (preserve paragraph breaks)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Number formatting ─────────────────────────────────────────────────
def _fmt_num(v: Any) -> str:
    """Human-readable formatting for financial statement cells."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        if v != v:  # NaN
            return "—"
        abs_v = abs(v)
        if abs_v >= 1e9:
            return f"{v/1e9:,.2f}B"
        if abs_v >= 1e6:
            return f"{v/1e6:,.2f}M"
        if abs_v >= 1e3:
            return f"{v/1e3:,.1f}K"
        if isinstance(v, float):
            return f"{v:,.2f}"
        return f"{v:,}"
    return str(v)


def _humanize_field(name: str) -> str:
    """total_equity → Total Equity"""
    return name.replace("_", " ").title()


# ── fpdf2-safe text helper ────────────────────────────────────────────
# fpdf2 default core fonts (Helvetica) are Latin-1 only. Transliterate or
# drop anything outside.
def _safe_text(s: str) -> str:
    if s is None:
        return ""
    # Common typographic chars → ASCII fallbacks
    trans = {
        "\u2014": "-",   # em-dash
        "\u2013": "-",   # en-dash
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "*",   # bullet
        "\u00b7": "-",
        "\u2026": "...",
    }
    out = []
    for ch in str(s):
        if ch in trans:
            out.append(trans[ch])
            continue
        try:
            ch.encode("latin-1")
            out.append(ch)
        except UnicodeEncodeError:
            out.append("?")
    return "".join(out)


# ── PDF renderer (imperative, fpdf2) ──────────────────────────────────
class _DocPDF(FPDF):
    """FPDF subclass with a registerable per-page header callback.

    Phase 2.2 fix: fpdf2's auto-page-break fires silently inside
    cell()/multi_cell() overflow. The only hook exposed is FPDF.header(),
    which fpdf2 invokes on EVERY new page (both manual and auto). By
    letting callers register a callback during table rendering, we get
    the year header row to repeat on auto-page-break instead of only on
    manually-triggered breaks.

    Callers MUST reset the callback to None in a try/finally after the
    table finishes, or the stale header will render on unrelated later
    pages (e.g. AI Thesis getting a Balance Sheet header).
    """

    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(left=12, top=12, right=12)
        # Registered repeat-header emitter. Set during table rendering,
        # cleared immediately after. Must accept (pdf) as sole arg.
        self._repeat_header_fn: Optional[Callable[["_DocPDF"], None]] = None

    def header(self) -> None:  # fpdf2 override, invoked on every new page
        if self._repeat_header_fn is not None:
            self._repeat_header_fn(self)


def _h1(pdf: _DocPDF, text: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", style="B", size=18)
    pdf.multi_cell(0, 9, _safe_text(text))
    pdf.ln(1)


def _h2(pdf: _DocPDF, text: str) -> None:
    pdf.ln(3)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", style="B", size=13)
    pdf.set_draw_color(120, 120, 120)
    pdf.multi_cell(0, 7, _safe_text(text), border="B")
    pdf.ln(1)


def _h3(pdf: _DocPDF, text: str) -> None:
    pdf.ln(1)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", style="B", size=11)
    pdf.multi_cell(0, 6, _safe_text(text))


def _para(pdf: _DocPDF, text: str, size: int = 10) -> None:
    if not text:
        return
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", size=size)
    pdf.multi_cell(0, 5, _safe_text(text))
    pdf.ln(1)


def _meta(pdf: _DocPDF, text: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", style="I", size=8)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 4, _safe_text(text))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(0.5)


def _kv_table(pdf: _DocPDF, rows: list[tuple[str, str]]) -> None:
    """Two-column key/value table.

    Phase 2.2 fix: guard each row against mid-row page break. The
    multi_cell pair can auto-break between the label and value if a
    row happens to land at the page bottom, orphaning the label. Pre-
    flight each row and force a fresh page when there isn't room for
    at least one row height.
    """
    if not rows:
        return
    pdf.set_font("Helvetica", size=9)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    k_w = page_w * 0.42
    v_w = page_w - k_w
    min_row_h = 5  # single-line row height; multi-line rows taller
    for k, v in rows:
        # Preflight: if even a one-line row wouldn't fit, start fresh page.
        if pdf.get_y() + min_row_h > pdf.h - pdf.b_margin:
            pdf.add_page()
        y0 = pdf.get_y()
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", style="B", size=9)
        pdf.multi_cell(k_w, 5, _safe_text(k), border=1)
        y_after_k = pdf.get_y()
        pdf.set_xy(pdf.l_margin + k_w, y0)
        pdf.set_font("Helvetica", size=9)
        pdf.multi_cell(v_w, 5, _safe_text(v), border=1)
        y_after_v = pdf.get_y()
        pdf.set_y(max(y_after_k, y_after_v))
    pdf.ln(1)


def _statement_table(pdf: _DocPDF, label: str, tbl: Optional[StatementTable]) -> None:
    """Render one financial statement as a period-columned table.

    Phase 2.2 fix: the period header row (FY2025 / FY2024 / ...) is
    now repeated on BOTH manual and auto-triggered page breaks, via
    the _DocPDF.header() callback. Before this fix, auto-page-break
    (triggered silently by cell() overflow) skipped the header emit,
    which is why Balance Sheet and Cash Flow rendered their data rows
    with no year labels at the top.
    """
    _h3(pdf, label)
    if tbl is None or not tbl.periods:
        _para(pdf, "Not available", size=9)
        return

    pdf.set_font("Helvetica", size=9)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    n_periods = len(tbl.periods)
    # field-name column gets 42%, remaining split across periods
    field_col_w = page_w * 0.42
    period_col_w = (page_w - field_col_w) / max(n_periods, 1)
    row_h = 5
    # Phase 2.1 Fix A: header row gets a slightly taller cell and a
    # darker fill so the period/year band is visually distinct from
    # the data rows below it.
    header_row_h = 6
    header_fill = (200, 200, 200)

    def _emit_header_row(p: _DocPDF = pdf) -> None:
        # First column is intentionally blank (the row labels below are
        # self-describing). Phase 2 fix: drop the "Item" stub label.
        p.set_font("Helvetica", style="B", size=9)
        p.set_fill_color(*header_fill)
        p.set_x(p.l_margin)
        p.cell(field_col_w, header_row_h, "", border=1, fill=True)
        for per in tbl.periods:
            p.cell(period_col_w, header_row_h, _safe_text(per), border=1, align="R", fill=True)
        p.ln(header_row_h)

    # Register the callback BEFORE emitting the first header, so if the
    # initial _h3() pushed us near the page bottom and the first cell()
    # triggers an auto-break, the new page still gets a header. Clear
    # it in finally so later unrelated sections don't inherit it.
    pdf._repeat_header_fn = _emit_header_row
    try:
        _emit_header_row()
        pdf.set_font("Helvetica", size=9)
        for fname, values in tbl.rows:
            # No manual page-break check needed — auto-page-break +
            # the header() callback handle it uniformly.
            pdf.set_x(pdf.l_margin)
            pdf.cell(field_col_w, row_h, _safe_text(_humanize_field(fname)), border=1)
            for v in values:
                pdf.cell(period_col_w, row_h, _safe_text(_fmt_num(v)), border=1, align="R")
            pdf.ln(row_h)
    finally:
        pdf._repeat_header_fn = None
    pdf.ln(1)


def _render_header(pdf: _DocPDF, doc: DocumentModel) -> None:
    ov = doc.company_overview
    # Phase 2: centered document-level header identifying the report origin.
    # Plain text only; brand styling is Phase 3.
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", style="B", size=12)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.cell(page_w, 6, _safe_text("BenTrade On Demand Evaluator"), align="C")
    pdf.ln(8)

    title = f"{ov.symbol}"
    if ov.name:
        title = f"{ov.symbol} — {ov.name}"
    _h1(pdf, title)

    meta_parts = [f"Generated: {doc.generated_at_iso}"]
    if doc.account_mode:
        meta_parts.append(f"Account: {doc.account_mode}")
    meta_parts.append(f"CE job: {doc.ce_job_id}")
    _meta(pdf, "   ".join(meta_parts))

    rows: list[tuple[str, str]] = []
    if ov.sector:
        rows.append(("Sector", ov.sector))
    if ov.industry:
        rows.append(("Industry", ov.industry))
    if ov.price is not None:
        rows.append(("Price", f"${ov.price:,.2f}"))
    if rows:
        _kv_table(pdf, rows)


def _render_dict_section(pdf: _DocPDF, title: str, data: Optional[dict[str, Any]]) -> None:
    """Generic shallow-dict renderer (no blocklist filtering — use
    `_render_valuation_section` for that). Phase 2.1 Fix C: routes the
    multi-row kv emission through `_render_kv_block_keep_together` so
    the heading + table never split across pages.

    Note on idempotency: at the top of a fresh page, `pdf.get_y()`
    ≈ `pdf.t_margin`, so `remaining` ≈ full page height and the
    keep-together helper's `remaining < page_height * 0.30` check is
    naturally False — no spurious double page-break occurs after a
    statement's explicit `add_page()` chain.
    """
    if not data:
        _h2(pdf, title)
        _para(pdf, "Not available")
        return
    # Flatten shallow dict into KV rows; nested dicts → skip (Phase 2 problem)
    rows: list[tuple[str, str]] = []
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            continue
        rows.append((_humanize_field(str(k)), _fmt_num(v) if isinstance(v, (int, float)) else _safe_text(str(v))))
    if rows:
        _render_kv_block_keep_together(pdf, title, rows)
    else:
        _h2(pdf, title)
        _para(pdf, "(No scalar fields)")


def _render_pillars(pdf: _DocPDF, evaluation: Optional[dict[str, Any]]) -> None:
    _h2(pdf, "Pillar Breakdown")
    if not evaluation:
        _para(pdf, "Not available")
        return
    top_rows: list[tuple[str, str]] = []
    if "composite_score" in evaluation:
        top_rows.append(("Composite Score", _fmt_num(evaluation.get("composite_score"))))
    if "completeness_pct" in evaluation:
        top_rows.append(("Completeness %", _fmt_num(evaluation.get("completeness_pct"))))
    if top_rows:
        _kv_table(pdf, top_rows)

    breakdowns = evaluation.get("pillar_breakdowns") or {}
    if not isinstance(breakdowns, dict):
        return
    for pname, pdata in breakdowns.items():
        _h3(pdf, _humanize_field(str(pname)))
        if not isinstance(pdata, dict):
            _para(pdf, _safe_text(str(pdata)))
            continue
        rows: list[tuple[str, str]] = []
        score = pdata.get("score")
        if score is not None:
            rows.append(("Score", _fmt_num(score)))
        metrics = pdata.get("metrics") or {}
        if isinstance(metrics, dict):
            for mk, mv in metrics.items():
                if isinstance(mv, (int, float, str)):
                    rows.append((_humanize_field(str(mk)), _fmt_num(mv) if isinstance(mv, (int, float)) else str(mv)))
        if rows:
            _kv_table(pdf, rows)


# Phase 2 (Fix 4) + Phase 2.1 (Fix B): noise fields the app UI does not
# display and the PDF must not display either. Case-insensitive match
# against the snake_case key. If `llm_analysis` is non-empty, it is
# rendered as a paragraph below the table (not as a key/value row).
VALUATION_NOISE_FIELDS: frozenset[str] = frozenset({
    # Phase 2 originals
    "ok",
    "symbol",
    "llm_available",
    "llm_analysis",
    "llm_recommendation",
    "analyzed_at",
    # Phase 2.1 additions (Entry & Price Targets surfaced these)
    "timestamp",
    "llm_conviction",
    "llm_key_levels",
    "llm_agrees_with_engine",
    "price_target_source",
})


def _is_meaningful_scalar(v: Any) -> bool:
    """True if value is worth rendering. Filters None and empty strings."""
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def _filter_valuation_fields(
    data: Optional[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Phase 2.1 Fix B: shared helper that turns a raw section dict into
    a filtered list of (label, value) rows, applying the case-insensitive
    blocklist + None/empty-string suppression + nested-dict skip.
    Returns [] when nothing user-facing remains.
    """
    if not data:
        return []
    rows: list[tuple[str, str]] = []
    for k, v in data.items():
        key_norm = str(k).strip().lower()
        if key_norm in VALUATION_NOISE_FIELDS:
            continue
        if isinstance(v, (dict, list)):
            continue
        if not _is_meaningful_scalar(v):
            continue
        rows.append((
            _humanize_field(str(k)),
            _fmt_num(v) if isinstance(v, (int, float)) else _safe_text(str(v)),
        ))
    return rows


def _render_valuation_section(
    pdf: _DocPDF,
    title: str,
    data: Optional[dict[str, Any]],
    *,
    fallback_text: Optional[str] = "Not available",
) -> None:
    """Phase 2 Fix 4 + Phase 2.1 Fixes B/C/D: filtered renderer used by
    DCF / EVA / Comps / Entry & Price Targets.

    - Blocklist filtering via VALUATION_NOISE_FIELDS (case-insensitive).
    - `llm_analysis` rendered as paragraph below the table when meaningful.
    - `fallback_text`:
        * "Not available" (default) → emit heading + fallback when empty.
          Use for sections that are user-meaningful even when CE skipped them.
        * None → suppress the heading entirely when filtered content is empty
          (Phase 2.1 Fix D — for purely-diagnostic sections).
    - Routes the kv block through `_render_kv_block_keep_together` so the
      heading + table stay on the same page (Phase 2.1 Fix C).
    """
    rows = _filter_valuation_fields(data)
    llm_text = (data or {}).get("llm_analysis")
    has_llm_paragraph = isinstance(llm_text, str) and bool(llm_text.strip())

    if not rows and not has_llm_paragraph:
        # Nothing meaningful to render.
        if fallback_text is None:
            # Phase 2.1 Fix D: suppress heading entirely.
            return
        _h2(pdf, title)
        _para(pdf, fallback_text)
        return

    # Phase 2.1 Fix C: keep-together so the heading doesn't orphan from
    # the kv table when the previous section ended near the page bottom.
    _render_kv_block_keep_together(pdf, title, rows)

    # llm_analysis (if meaningful) → paragraph below the table.
    if has_llm_paragraph:
        _h3(pdf, "Model Analysis")
        _para(pdf, _markdown_to_plain(llm_text))


def _render_financials(pdf: _DocPDF, fs: FinancialStatements) -> None:
    """Phase 2.2 fix: the previous version force-broke to a fresh page
    between each statement with unconditional pdf.add_page() calls. That
    was papering over the missing-header bug in _statement_table (auto-
    page-break didn't re-emit the header row). With the header-callback
    fix in place, statements can flow naturally; if any given statement
    doesn't have room to start on the current page, the keep-together
    helper bumps it to a fresh page instead.
    """
    _h2(pdf, "Financial Statements")
    page_height = pdf.h - pdf.t_margin - pdf.b_margin

    def _fresh_page_if_cramped(threshold_pct: float = 0.30) -> None:
        remaining = pdf.h - pdf.b_margin - pdf.get_y()
        if remaining < page_height * threshold_pct:
            pdf.add_page()

    _statement_table(pdf, "Income Statement", fs.income_statement)
    _fresh_page_if_cramped()
    _statement_table(pdf, "Balance Sheet", fs.balance_sheet)
    _fresh_page_if_cramped()
    _statement_table(pdf, "Cash Flow", fs.cash_flow)


def _render_appended(pdf: _DocPDF, appended: list[dict[str, Any]]) -> None:
    if not appended:
        return
    _h2(pdf, "Appended Analyses")
    for a in appended:
        _h3(pdf, _safe_text(a.get("title") or "Analysis"))
        ts = a.get("timestamp")
        if ts:
            _meta(pdf, f"Added: {ts}")
        body = _markdown_to_plain(a.get("body_md") or "")
        _para(pdf, body, size=10)


def _render_user_notes(pdf: _DocPDF, notes: Optional[str]) -> None:
    if not notes:
        return
    _h2(pdf, "User Notes")
    _para(pdf, notes)


def _render_chart(pdf: _DocPDF, chart_b64: Optional[str]) -> None:
    """Phase 2 Fix 6: embed client-captured price chart PNG.

    Decode + inject. On any failure (bad base64, unreadable image, fpdf2
    rejection), log a warning and continue rendering without the chart —
    the rest of the document is more important than the chart.
    """
    if not chart_b64:
        return
    try:
        png_bytes = base64.b64decode(chart_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=pdf.chart_embed_failed stage=decode err=%s", exc)
        return
    try:
        _h2(pdf, "Price Chart (1Y)")
        page_w = pdf.w - pdf.l_margin - pdf.r_margin
        pdf.image(io.BytesIO(png_bytes), w=page_w)
        pdf.ln(2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=pdf.chart_embed_failed stage=image err=%s", exc)


def _render_kv_block_keep_together(
    pdf: _DocPDF,
    title: str,
    rows: list[tuple[str, str]],
    long_text_after: Optional[tuple[str, str]] = None,
    min_height_needed_pct: float = 0.30,
) -> None:
    """Phase 2 Fix 3: emit a titled key/value block that either fits on
    the current page or starts fresh. Avoids the orphaned-page-break
    that left huge gaps between Summary and Thesis.

    long_text_after: optional (heading, paragraph) rendered below the kv
    table — used by the AI Thesis section for the long thesis body.
    """
    page_height = pdf.h - pdf.t_margin - pdf.b_margin
    remaining = pdf.h - pdf.b_margin - pdf.get_y()
    if remaining < page_height * min_height_needed_pct:
        pdf.add_page()
    _h2(pdf, title)
    if rows:
        _kv_table(pdf, rows)
    if long_text_after is not None:
        heading, body = long_text_after
        if body and body.strip():
            _h3(pdf, heading)
            _para(pdf, body)


def _render_ai_thesis(pdf: _DocPDF, thesis: Optional[dict[str, Any]]) -> None:
    """Phase 2 Fix 3 + Fix 4: dedicated AI Investment Thesis renderer.

    Pulls scalar fields (Rating, Conviction, Summary) into a kv table and
    the long Thesis body into a paragraph. Whole block kept together so
    Summary + Thesis don't split with a blank gap.
    """
    if not thesis:
        # Still emit the section so its absence is explicit.
        _h2(pdf, "AI Investment Thesis")
        _para(pdf, "Not available")
        return

    # Pull the long body out so it can render as a paragraph instead of
    # a kv row that fpdf2 tries to keep on one line.
    body_keys = ("thesis", "thesis_text", "narrative", "rationale")
    long_body = ""
    long_body_label = "Thesis"
    for bk in body_keys:
        v = thesis.get(bk)
        if isinstance(v, str) and v.strip():
            long_body = v
            long_body_label = _humanize_field(bk)
            break

    rows: list[tuple[str, str]] = []
    skip_keys = set(body_keys) | VALUATION_NOISE_FIELDS
    for k, v in thesis.items():
        key_norm = str(k).strip().lower()
        if key_norm in skip_keys:
            continue
        if isinstance(v, (dict, list)):
            continue
        if not _is_meaningful_scalar(v):
            continue
        rows.append((
            _humanize_field(str(k)),
            _fmt_num(v) if isinstance(v, (int, float)) else _safe_text(str(v)),
        ))

    long_text = (long_body_label, _markdown_to_plain(long_body)) if long_body else None
    _render_kv_block_keep_together(
        pdf,
        "AI Investment Thesis",
        rows,
        long_text_after=long_text,
    )

    if not rows and not long_body:
        _para(pdf, "Not available")


def _render_pdf(doc: DocumentModel) -> bytes:
    pdf = _DocPDF()
    pdf.add_page()
    _render_header(pdf, doc)
    _render_chart(pdf, doc.chart_png_base64)
    _render_dict_section(pdf, "Quality Signals", doc.quality_signals)
    _render_valuation_section(pdf, "DCF Valuation", doc.dcf)
    _render_valuation_section(pdf, "EVA Valuation", doc.eva)
    _render_valuation_section(pdf, "Comparable Companies", doc.comps)
    _render_pillars(pdf, doc.pillar_breakdown)
    # Phase 2.1 Fix B: Entry & Price Targets routed through the filtered
    # renderer so noise fields (Ok, Symbol, Timestamp, Llm_*) are dropped.
    _render_valuation_section(
        pdf,
        "Entry & Price Targets",
        (doc.entry_price_targets or {}).get("entry_analysis") if doc.entry_price_targets else None,
        fallback_text="Not available",
    )
    if doc.entry_price_targets and doc.entry_price_targets.get("price_targets"):
        _h3(pdf, "Price Targets")
        _kv_table(
            pdf,
            [
                (_humanize_field(k), _fmt_num(v) if isinstance(v, (int, float)) else str(v))
                for k, v in doc.entry_price_targets["price_targets"].items()
                if not isinstance(v, (dict, list))
            ],
        )
    _render_financials(pdf, doc.financial_statements)
    _render_ai_thesis(pdf, doc.ai_thesis)
    _render_appended(pdf, doc.appended_analyses)
    _render_user_notes(pdf, doc.user_notes)

    raw = pdf.output()
    # fpdf2 ≥2.8 returns bytearray from .output() with no dest
    return bytes(raw)


# ── CE fetch ──────────────────────────────────────────────────────────
async def _fetch_ce_result(job_id: str) -> dict[str, Any]:
    url = _CE_PROXY_URL.format(job_id=job_id)
    try:
        async with httpx.AsyncClient(timeout=CE_FETCH_TIMEOUT_S) as client:
            resp = await client.get(url)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
        logger.error("event=pdf.ce_fetch_failed job_id=%s error=%s", job_id, exc)
        raise CEUnreachableError(f"Company Evaluator unreachable: {exc}") from exc

    if resp.status_code == 404:
        raise CEJobNotFoundError(f"CE job not found: {job_id}")
    if resp.status_code >= 400:
        raise CEUnreachableError(
            f"CE returned {resp.status_code} for job {job_id}: {resp.text[:200]}"
        )
    try:
        return resp.json()
    except Exception as exc:
        raise CEUnreachableError(f"CE result JSON parse failed: {exc}") from exc


def _enforce_size_cap(pdf_bytes: bytes) -> None:
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise PDFTooLargeError(
            f"PDF size {len(pdf_bytes)} bytes exceeds cap {MAX_PDF_BYTES}"
        )


async def render_on_demand_pdf(payload: OnDemandPdfPayload) -> bytes:
    """Public entry point. Fetches cached CE result, renders PDF, returns bytes.

    Raises:
        CEJobNotFoundError: CE returned 404 for the job_id.
        CEUnreachableError: CE connection/timeout/5xx.
        PDFTooLargeError: rendered PDF exceeded MAX_PDF_BYTES.
    """
    logger.info(
        "event=pdf.export.start symbol=%s job_id=%s appended=%d",
        payload.symbol,
        payload.job_id,
        len(payload.appended_analyses),
    )
    ce_result = await _fetch_ce_result(payload.job_id)
    document = _build_document_model(ce_result, payload)
    pdf_bytes = _render_pdf(document)
    _enforce_size_cap(pdf_bytes)
    logger.info(
        "event=pdf.export.success symbol=%s job_id=%s bytes=%d",
        payload.symbol,
        payload.job_id,
        len(pdf_bytes),
    )
    return pdf_bytes