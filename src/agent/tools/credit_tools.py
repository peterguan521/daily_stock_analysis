# -*- coding: utf-8 -*-
"""
Credit Analysis Tools — financial statement and credit ratio retrieval
for the Agent-based credit analysis workflow.

Tools:
- get_financials:     Full financial snapshot (income + balance sheet + cashflow + ratios)
- batch_financials:   Same for multiple stocks (peer comparison)
- get_credit_ratios:  Focused credit metrics (DSO, CEI, interest coverage, liquidity gap)
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from src.agent.tools.registry import ToolParameter, ToolDefinition

logger = logging.getLogger(__name__)

# ── Lazy Tushare Pro ────────────────────────────────────
_TS_PRO = None


def _get_ts():
    """Initialize and return Tushare Pro client (lazy singleton)."""
    global _TS_PRO
    if _TS_PRO is not None:
        return _TS_PRO
    try:
        import os
        import tushare as ts

        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            logger.warning("TUSHARE_TOKEN not set in environment")
            _TS_PRO = None
            return None
        ts.set_token(token)
        _TS_PRO = ts.pro_api()
        logger.info("Tushare Pro initialized successfully")
    except Exception as e:
        logger.warning("Tushare init failed: %s", e)
        _TS_PRO = None
    return _TS_PRO


# ── Helpers ─────────────────────────────────────────────

def _to_billion(yuan: Optional[float], ndigits: int = 2) -> Optional[float]:
    if yuan is None:
        return None
    return round(yuan / 1e8, ndigits)


def _pct(raw: Optional[float], ndigits: int = 2) -> Optional[float]:
    """Convert decimal (e.g. 0.1834) to percentage (e.g. 18.34)."""
    if raw is None:
        return None
    return round(raw * 100, ndigits)


def _calc_growth(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 2)


def _ts_code(code: str) -> str:
    """Normalize to Tushare ts_code format (e.g. '600887' -> '600887.SH')."""
    code = code.strip().upper()
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "3", "2")):
        return f"{code}.SZ"
    return code


def _latest_fiscal_year() -> int:
    """Return the most recently completed fiscal year."""
    now = datetime.now()
    return now.year - 1 if now.month < 6 else now.year - 1


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ── Internal data fetching ─────────────────────────────

def _fetch_annual_income(pro, ts_code: str, year: int) -> Tuple[Optional[dict], Optional[dict]]:
    """Fetch annual income statement for year and year-1. Returns (current_row, prev_row)."""
    df = pro.income(ts_code=ts_code, start_date="20100101", end_date="20261231")
    if df is None or len(df) == 0:
        return None, None
    df_annual = df[df["end_type"] == "4"].copy() if "end_type" in df.columns else df.copy()
    if len(df_annual) == 0:
        df_annual = df.copy()
    df_annual.sort_values("end_date", ascending=False, inplace=True)
    # Find year and year-1
    cur = df_annual[df_annual["end_date"].str.startswith(str(year))]
    prv = df_annual[df_annual["end_date"].str.startswith(str(year - 1))]
    return (
        cur.iloc[0] if len(cur) > 0 else (df_annual.iloc[0] if len(df_annual) > 0 else None),
        prv.iloc[0] if len(prv) > 0 else None,
    )


def _fetch_annual_bs(pro, ts_code: str, year: int) -> Optional[dict]:
    """Fetch annual balance sheet."""
    df = pro.balancesheet(ts_code=ts_code, start_date=f"{year}0101", end_date=f"{year}1231")
    if df is not None and "end_type" in df.columns:
        df = df[df["end_type"] == "4"]
    if df is not None and len(df) > 0:
        return df.iloc[0]
    return None


def _fetch_annual_cf(pro, ts_code: str, year: int) -> Optional[dict]:
    """Fetch annual cashflow statement."""
    df = pro.cashflow(ts_code=ts_code, start_date=f"{year}0101", end_date=f"{year}1231")
    if df is not None and "end_type" in df.columns:
        df = df[df["end_type"] == "4"]
    if df is not None and len(df) > 0:
        return df.iloc[0]
    return None


def _fetch_indicators(pro, ts_code: str, year: int) -> Optional[dict]:
    """Fetch pre-calculated financial indicators for the given year."""
    try:
        df = pro.fina_indicator(ts_code=ts_code, start_date=f"{year}0101", end_date=f"{year}1231")
        if df is not None:
            # Filter to annual (end_date ends with 1231)
            annual = df[df["end_date"].str.endswith("1231")] if "end_date" in df.columns else df
            if len(annual) > 0:
                return annual.iloc[0]
    except Exception:
        pass
    return None


def _fetch_company_name(pro, ts_code: str) -> str:
    """Resolve company name from stock basic info."""
    try:
        df = pro.stock_basic(ts_code=ts_code)
        if df is not None and len(df) > 0:
            return str(df.iloc[0].get("name", ts_code))
    except Exception:
        pass
    return ts_code


# ============================================================
# get_financials
# ============================================================

def _handle_get_financials(stock_code: str, year: Optional[int] = None) -> dict:
    """Fetch comprehensive financial data: income + balance sheet + cashflow + ratios."""
    pro = _get_ts()
    if pro is None:
        return {"ok": False, "error": "Tushare unavailable (check TUSHARE_TOKEN env)", "data": {}}

    if year is None:
        year = _latest_fiscal_year()

    ts_code = _ts_code(stock_code)
    result: Dict[str, Any] = {"stock_code": stock_code, "ts_code": ts_code, "year": year}
    warnings: List[str] = []

    # 1. Income Statement
    cur_inc, prv_inc = _fetch_annual_income(pro, ts_code, year)
    if cur_inc is not None:
        revenue = _to_billion(_safe_float(cur_inc.get("total_revenue")))
        net_profit = _to_billion(_safe_float(cur_inc.get("n_income_attr_p")))
        operate_profit = _to_billion(_safe_float(cur_inc.get("operate_profit")))
        total_profit = _to_billion(_safe_float(cur_inc.get("total_profit")))
        fin_exp = _to_billion(_safe_float(cur_inc.get("fin_exp")))

        rev_prev = _to_billion(_safe_float(prv_inc.get("total_revenue"))) if prv_inc is not None else None
        np_prev = _to_billion(_safe_float(prv_inc.get("n_income_attr_p"))) if prv_inc is not None else None

        # Interest expense approximation: use financial expense (fin_exp)
        interest_expense = _to_billion(_safe_float(cur_inc.get("fin_exp")))

        result.update({
            "revenue": revenue,
            "revenue_yuan": _safe_float(cur_inc.get("total_revenue")),
            "revenue_growth": _calc_growth(revenue, rev_prev),
            "net_profit": net_profit,
            "net_profit_yuan": _safe_float(cur_inc.get("n_income_attr_p")),
            "net_profit_growth": _calc_growth(net_profit, np_prev),
            "operate_profit": operate_profit,
            "total_profit": total_profit,
            "fin_expense": fin_exp,
            "interest_expense": interest_expense,
        })
        if revenue and net_profit:
            result["net_margin"] = round(net_profit / revenue * 100, 2)
    else:
        warnings.append("Income statement data not available")

    # 2. Balance Sheet
    bs = _fetch_annual_bs(pro, ts_code, year)
    if bs is not None:
        total_assets = _to_billion(_safe_float(bs.get("total_assets")))
        total_liab = _to_billion(_safe_float(bs.get("total_liab")))
        equity = _to_billion(_safe_float(bs.get("total_hldr_eqy_exc_min_int")))
        curr_assets = _to_billion(_safe_float(bs.get("total_curr_assets")))
        curr_liab = _to_billion(_safe_float(bs.get("total_curr_liab")))

        result.update({
            "total_assets": total_assets,
            "total_liabilities": total_liab,
            "equity": equity,
            "current_assets": curr_assets,
            "current_liabilities": curr_liab,
            "monetary_capital": _to_billion(_safe_float(bs.get("monetary_cap"))),
            "accounts_receivable": _to_billion(_safe_float(bs.get("accounts_receiv"))),
            "inventories": _to_billion(_safe_float(bs.get("inventories"))),
            "short_term_loans": _to_billion(_safe_float(bs.get("short_term_loan"))),
            "long_term_loans": _to_billion(_safe_float(bs.get("long_term_loan"))),
            "notes_receivable": _to_billion(_safe_float(bs.get("notes_receiv"))),
            "notes_payable": _to_billion(_safe_float(bs.get("notes_payable"))),
        })

        # Calculated ratios
        if total_assets and total_assets > 0:
            result["debt_to_assets_pct"] = round(total_liab / total_assets * 100, 2) if total_liab else 0.0
            result["equity_ratio_pct"] = round(equity / total_assets * 100, 2) if equity else 0.0
        if curr_liab and curr_liab > 0:
            result["current_ratio"] = round(curr_assets / curr_liab, 2) if curr_assets else 0.0
            # Quick ratio = (Current Assets - Inventories) / Current Liabilities
            inv = _to_billion(_safe_float(bs.get("inventories")))
            quick_assets = (curr_assets - inv) if inv else curr_assets
            result["quick_ratio"] = round(quick_assets / curr_liab, 2)

        # Short-term debt coverage: current assets / short-term loans
        st_loans = _to_billion(_safe_float(bs.get("short_term_loan")))
        if st_loans and st_loans > 0 and curr_assets:
            result["short_term_coverage"] = round(curr_assets / st_loans, 2)
    else:
        warnings.append("Balance sheet data not available")

    # 3. Cashflow
    cf = _fetch_annual_cf(pro, ts_code, year)
    if cf is not None:
        ocf = _to_billion(_safe_float(cf.get("n_cashflow_act")))
        icf = _to_billion(_safe_float(cf.get("n_cashflow_inv_act")))
        fcf = _to_billion(_safe_float(cf.get("n_cashflow_fnc_act")))
        free_cf = _to_billion(_safe_float(cf.get("free_cashflow")))

        result.update({
            "operating_cashflow": ocf,
            "investing_cashflow": icf,
            "financing_cashflow": fcf,
            "free_cashflow": free_cf,
        })

        # OCF / Net Income quality ratio
        ni = result.get("net_profit_yuan") or result.get("net_profit")
        ni_val = _safe_float(cf.get("n_cashflow_act"))
        if ocf is not None and ni is not None:
            result["ocf_to_net_profit"] = round(ocf / ni, 2) if ni != 0 else None

        # OCF / Revenue
        rev = result.get("revenue")
        if ocf is not None and rev is not None:
            result["ocf_to_revenue_pct"] = round(ocf / rev * 100, 2) if rev != 0 else None
    else:
        warnings.append("Cashflow data not available")

    # 4. Pre-calculated indicators (fina_indicator)
    ind = _fetch_indicators(pro, ts_code, year)
    if ind is not None:
        result.update({
            "roe_pct": _safe_float(ind.get("roe")),
            "gross_margin_pct": _safe_float(ind.get("grossprofit_margin")),
            "eps": _safe_float(ind.get("eps")),
            "bps": _safe_float(ind.get("bps")),
            "ocf_per_share": _safe_float(ind.get("ocfps")),
            "interest_coverage": _safe_float(ind.get("interest_coverage")),
        })
        # If we didn't calculate net_margin from income, get it from indicator
        if result.get("net_margin") is None:
            result["net_margin_pct"] = _safe_float(ind.get("profit_to_gr"))
    else:
        # If fina_indicator not available, try to calculate interest coverage
        op = result.get("operate_profit")
        ie = result.get("interest_expense")
        if op is not None and ie is not None and ie != 0:
            result["interest_coverage"] = round(op / ie, 2)

    # 5. DSO (Days Sales Outstanding)
    ar = result.get("accounts_receivable")  # in billions
    rev = result.get("revenue")  # in billions
    if ar is not None and rev is not None and rev > 0:
        result["dso_days"] = round(ar / rev * 365, 1)

    # 6. Company name
    result["company_name"] = _fetch_company_name(pro, ts_code)

    result["warnings"] = warnings
    result["ok"] = len(warnings) == 0 or len(warnings) < 3  # partial ok if some data available
    return result


get_financials_tool = ToolDefinition(
    name="get_financials",
    description=(
        "Get complete financial data for a stock, including income statement, "
        "balance sheet, cashflow statement, and calculated credit ratios. "
        "Returns: revenue, net profit, total assets, liabilities, current ratio, "
        "debt-to-assets ratio, DSO (days), operating cashflow, interest coverage, "
        "ROE, gross margin, and more. Use this as the primary data source for "
        "credit analysis of a company."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g. '600887' for Yili, '600519' for Maotai, '2319.HK' for Mengniu",
        ),
        ToolParameter(
            name="year",
            type="integer",
            description="Fiscal year (e.g., 2024). Defaults to the most recent completed fiscal year.",
            required=False,
            default=None,
        ),
    ],
    handler=_handle_get_financials,
    category="data",
)


# ============================================================
# batch_financials
# ============================================================

def _handle_batch_financials(stock_codes: str, year: Optional[int] = None) -> dict:
    """Fetch financial data for multiple stocks (comma-separated)."""
    codes = [c.strip() for c in stock_codes.split(",") if c.strip()]
    if not codes:
        return {"ok": False, "error": "No stock codes provided", "results": {}}

    results: Dict[str, Any] = {}
    errors: List[str] = []

    for code in codes:
        try:
            data = _handle_get_financials(code, year=year)
            results[code] = data
            if not data.get("ok"):
                errors.append(f"{code}: {data.get('error', 'partial data')}")
        except Exception as e:
            errors.append(f"{code}: {e}")
            results[code] = {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "results": results,
        "success_count": sum(1 for r in results.values() if r.get("ok")),
        "total_count": len(codes),
        "errors": errors,
    }


batch_financials_tool = ToolDefinition(
    name="batch_financials",
    description=(
        "Fetch financial data for multiple stocks at once. Useful for peer comparison "
        "and industry benchmarking. Call this instead of calling get_financials "
        "multiple times when you need to compare companies."
    ),
    parameters=[
        ToolParameter(
            name="stock_codes",
            type="string",
            description="Comma-separated stock codes, e.g. '600887,600519,2319.HK'",
        ),
        ToolParameter(
            name="year",
            type="integer",
            description="Fiscal year (e.g., 2024). Defaults to the most recent completed fiscal year.",
            required=False,
            default=None,
        ),
    ],
    handler=_handle_batch_financials,
    category="data",
)


# ============================================================
# get_credit_ratios
# ============================================================

def _handle_get_credit_ratios(stock_code: str, year: Optional[int] = None) -> dict:
    """Fetch focused credit-specific ratios for a stock."""
    # Reuse get_financials and extract credit-specific metrics
    full = _handle_get_financials(stock_code, year=year)
    if not full.get("ok") and not full.get("data"):
        return full

    d = full  # the full result dict
    # Extract and reorganize only credit-relevant fields
    credit = {
        "stock_code": d.get("stock_code"),
        "company_name": d.get("company_name"),
        "year": d.get("year"),
        # Leverage
        "debt_to_assets_pct": d.get("debt_to_assets_pct"),
        "equity_ratio_pct": d.get("equity_ratio_pct"),
        "interest_coverage": d.get("interest_coverage"),
        # Liquidity
        "current_ratio": d.get("current_ratio"),
        "quick_ratio": d.get("quick_ratio"),
        "short_term_coverage": d.get("short_term_coverage"),
        "monetary_capital": d.get("monetary_capital"),
        "short_term_loans": d.get("short_term_loans"),
        "long_term_loans": d.get("long_term_loans"),
        # Receivables
        "accounts_receivable": d.get("accounts_receivable"),
        "dso_days": d.get("dso_days"),
        # Cashflow quality
        "operating_cashflow": d.get("operating_cashflow"),
        "free_cashflow": d.get("free_cashflow"),
        "ocf_to_net_profit": d.get("ocf_to_net_profit"),
        "ocf_to_revenue_pct": d.get("ocf_to_revenue_pct"),
        # Profitability
        "roe_pct": d.get("roe_pct"),
        "gross_margin_pct": d.get("gross_margin_pct"),
        "net_margin_pct": d.get("net_margin_pct") or d.get("net_margin"),
        # Scale
        "revenue": d.get("revenue"),
        "net_profit": d.get("net_profit"),
        "total_assets": d.get("total_assets"),
        "total_liabilities": d.get("total_liabilities"),
        "equity": d.get("equity"),
    }
    return {"ok": True, "credit_ratios": credit, "warnings": d.get("warnings", [])}


get_credit_ratios_tool = ToolDefinition(
    name="get_credit_ratios",
    description=(
        "Get a focused credit-ratio snapshot for a stock. Returns only the metrics "
        "most relevant to credit risk assessment: leverage, liquidity, DSO, "
        "cashflow quality, and profitability. A lighter alternative to get_financials "
        "when you only need the credit score inputs."
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g. '600887' or '600887.SH'",
        ),
        ToolParameter(
            name="year",
            type="integer",
            description="Fiscal year. Defaults to latest completed year.",
            required=False,
            default=None,
        ),
    ],
    handler=_handle_get_credit_ratios,
    category="analysis",
)


# ============================================================
# Export all credit tools
# ============================================================

ALL_CREDIT_TOOLS = [
    get_financials_tool,
    batch_financials_tool,
    get_credit_ratios_tool,
]
