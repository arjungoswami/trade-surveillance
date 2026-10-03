"""Static configuration for the synthetic Harborline Capital dataset.

Everything here is fictional: firm, people, clients, token issuer, venue and prices.

Harborline Capital is a crypto trading firm with four businesses:
  * Options / market making  - quotes two-sided markets, hedges an options book
  * Delta One                - proprietary trading with the firm's own capital
  * Token liquidity          - market-making agreements with token issuers (volume/spread KPIs)
  * Execution                - works institutional client orders; small facilitation books
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data"          # data/<day>/   what the agent's tools read
EVAL_ROOT = ROOT / "eval_data"     # eval_data/<day>/ground_truth.json  - the agent must never see this
OUT_ROOT = ROOT / "output"         # output/<day>/ dispositions + audit log written by the tools

FIRM = "Harborline Capital"
VENUE = "XCH"
DEFAULT_DAY = "2026-10-03"         # the scripted demo day
DEFAULT_SEED = 13
SESSION_START = "13:00:00"         # UTC
SESSION_HOURS = 8
N_SECONDS = SESSION_HOURS * 3600


def day():
    """The trading day the tools operate on (set SURV_DAY to switch)."""
    return os.environ.get("SURV_DAY", DEFAULT_DAY)


def data_dir(d=None):
    return DATA_ROOT / (d or day())


def eval_dir(d=None):
    return EVAL_ROOT / (d or day())


def out_dir(d=None):
    return OUT_ROOT / (d or day())


SYMBOLS = {
    # px: starting mid, tick, vol: annualised vol, lot: typical order size range
    "BTC-PERP": {"px": 64000.0, "tick": 0.5, "vol": 0.45, "lot": (0.05, 2.0)},
    "ETH-PERP": {"px": 2500.0, "tick": 0.05, "vol": 0.55, "lot": (0.5, 15.0)},
    "SOL-PERP": {"px": 150.0, "tick": 0.01, "vol": 0.75, "lot": (5.0, 120.0)},
    "NOVA-USDT": {"px": 0.42, "tick": 0.0001, "vol": 1.2, "lot": (2000.0, 40000.0)},   # thin issuer token, spot
}

# rate = background orders per minute per symbol
TRADERS = [
    {"trader_id": "T01", "name": "Dan Kessler", "desk": "Delta One", "role": "prop_trader",
     "accounts": ["P-KESSLER"], "symbols": ["ETH-PERP", "BTC-PERP"], "rate": 0.5},
    {"trader_id": "T02", "name": "Maya Ortiz", "desk": "Options", "role": "market_maker",
     "accounts": ["MM-ORTIZ"], "symbols": ["ETH-PERP", "BTC-PERP"], "rate": 3.0},
    {"trader_id": "T03", "name": "Tom Becker", "desk": "Token Liquidity", "role": "liquidity_provider",
     "accounts": ["LP-NOVA"], "symbols": ["NOVA-USDT"], "rate": 1.5},
    {"trader_id": "T04", "name": "Leo Brandt", "desk": "Execution", "role": "execution_trader",
     "accounts": ["FAC-BRANDT", "PA-BRANDT"], "symbols": ["ETH-PERP", "SOL-PERP"], "rate": 0.15},
    {"trader_id": "T05", "name": "Sara Lind", "desk": "Execution", "role": "execution_trader",
     "accounts": ["FAC-LIND", "PA-LIND"], "symbols": ["BTC-PERP"], "rate": 0.1},
    {"trader_id": "T07", "name": "Omar Haddad", "desk": "Options", "role": "market_maker",
     "accounts": ["MM-HADDAD"], "symbols": ["ETH-PERP", "SOL-PERP"], "rate": 3.0},
    {"trader_id": "T08", "name": "Grace Liu", "desk": "Delta One", "role": "prop_trader",
     "accounts": ["P-LIU"], "symbols": ["BTC-PERP", "ETH-PERP"], "rate": 0.5},
    {"trader_id": "T09", "name": "Ben Okafor", "desk": "Delta One", "role": "prop_trader",
     "accounts": ["P-OKAFOR"], "symbols": ["ETH-PERP", "SOL-PERP"], "rate": 0.5},
    {"trader_id": "T10", "name": "Nina Rossi", "desk": "Options", "role": "market_maker",
     "accounts": ["MM-ROSSI"], "symbols": ["BTC-PERP", "SOL-PERP"], "rate": 3.0},
]

# People who appear in chat but don't trade
CONTACTS = [
    {"trader_id": "X-NOVA", "name": "Jonas Weller", "desk": "External - Nova Foundation", "role": "external_contact",
     "accounts": "", "role_description": "Business development lead at Nova Foundation, the issuer of NOVA. "
                                          "Counterparty to Harborline's NOVA market-making agreement."},
]

ROLE_DESCRIPTIONS = {
    "prop_trader": "Trades the firm's proprietary capital directionally within risk limits. Pay is tied to the "
                   "P&L of their book.",
    "market_maker": "Quotes two-sided markets continuously and hedges the options book; high cancel rates and "
                    "rapid re-quoting are normal.",
    "liquidity_provider": "Runs liquidity provision for token issuers under market-making agreements. The NOVA "
                          "agreement with Nova Foundation pays a monthly retainer and token options only if 30-day "
                          "volume and spread KPIs on XCH are met; the desk's bonus pool is tied to these fees.",
    "execution_trader": "Works client orders. Runs a small facilitation book. Has an employee personal account "
                        "held with the firm; every personal trade needs compliance pre-clearance, and trading ahead "
                        "of client orders is prohibited.",
}

ACCOUNTS = [
    # account_id, type, beneficial_owner, controlled_by (who directs trading), notes
    ("P-KESSLER", "prop", "Harborline Capital", "Dan Kessler", "Delta One prop book"),
    ("MM-ORTIZ", "market_making", "Harborline Capital", "Maya Ortiz", "Options desk MM/hedging book"),
    ("LP-NOVA", "token_liquidity", "Harborline Capital", "Tom Becker",
     "Book for the Nova Foundation market-making agreement (NOVA-USDT on XCH)"),
    ("FAC-BRANDT", "facilitation", "Harborline Capital", "Leo Brandt", "Execution desk facilitation book"),
    ("PA-BRANDT", "employee_personal", "Leo Brandt", "Leo Brandt",
     "Employee personal account held with the firm. Every trade requires compliance pre-clearance."),
    ("FAC-LIND", "facilitation", "Harborline Capital", "Sara Lind", "Execution desk facilitation book"),
    ("PA-LIND", "employee_personal", "Sara Lind", "Sara Lind",
     "Employee personal account held with the firm. Every trade requires compliance pre-clearance."),
    ("MM-HADDAD", "market_making", "Harborline Capital", "Omar Haddad", "Options desk MM book"),
    ("P-LIU", "prop", "Harborline Capital", "Grace Liu", "Delta One prop book"),
    ("P-OKAFOR", "prop", "Harborline Capital", "Ben Okafor", "Delta One prop book"),
    ("MM-ROSSI", "market_making", "Harborline Capital", "Nina Rossi", "Options desk MM book"),
    ("AFF-0731", "client_dma", "Becker Family LLC", "Tom Becker",
     "Direct-market-access client account opened 2026-06 by the onboarding team. KYC lists Becker Family LLC, "
     "controlling person Tom Becker. NOT listed on T. Becker's personal-account declaration to Compliance."),
    ("C-CALDER-I", "client", "Calder Global Macro Fund I", "Calder Asset Management", "Institutional client fund"),
    ("C-CALDER-II", "client", "Calder Multi-Strategy Fund II", "Calder Asset Management", "Institutional client fund"),
    ("C-WESTBROOK", "client", "Westbrook Pension Fund", "Westbrook Pension Fund", "Institutional client"),
    ("C-KITERIDGE", "client", "Kite Ridge Partners", "Kite Ridge Partners", "Institutional client"),
]

# Rule thresholds (deliberately loose: real rule sets over-alert)
RULES = {
    "layering": {"lookback_s": 60, "cancel_within_s": 30, "min_orders": 4, "min_qty_ratio": 3.0,
                 "min_cancel_frac": 0.8, "merge_gap_s": 120},
    "cancels": {"bucket_min": 15, "min_new": 40, "min_cancel_ratio": 0.96, "max_fills": 1},
    "wash": {"merge_gap_s": 900},
    "front_running": {"max_window_s": 1800},
}
