"""Local web dashboard: the investigation workspace that sits beside the Telegram channel.

Telegram is where the agent posts and the analyst gives commands; this page is where a
human sees the whole case - the reconstructed order flow, the evidence, what the agent
actually did, and the close/escalate decision.

    python -m surveillance.web                      # http://127.0.0.1:8800
    python -m surveillance.web --port 9000 --day 2026-10-03
    python -m surveillance.web --model qwen3:4b     # enables the "Run agent" button
    python -m surveillance.web --base-url http://localhost:8000/v1 --model <served-name>

Standard library only (no new dependencies). It binds to 127.0.0.1 by default: the data is
synthetic, but the decision endpoints write to output/<day>/, so keep it off the network
unless you mean to share it.
"""
import argparse
import json
import mimetypes
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config as C

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PAD_S = 90          # seconds of context either side of the alert window
PRICE_POINTS = 180  # sampled points on the price path

# model settings for the "Run agent" button; filled in by main()
MODEL = {"base_url": "http://localhost:11434/v1", "model": None, "api_key": "none", "max_steps": 12}
JOBS = {}
JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- helpers

def _tools():
    """Imported lazily so SURV_DAY is already set when tools.py reads the day."""
    from . import tools
    return tools


def _secs(hms):
    h, m, s = (hms.split(":") + ["0", "0"])[:3]
    return int(h) * 3600 + int(m) * 60 + int(float(s))


def _hms(secs):
    secs = max(0, min(86399, int(secs)))
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _padded_window(alert):
    s, e = _secs(alert["window_start"]), _secs(alert["window_end"])
    session_start = _secs(C.SESSION_START)
    session_end = session_start + C.SESSION_HOURS * 3600 - 1
    return _hms(max(session_start, s - PAD_S)), _hms(min(session_end, e + PAD_S))


def _brief(name, out):
    """One short line describing what a tool returned - what the pipeline strip shows."""
    if not isinstance(out, dict):
        return ""
    if "error" in out:
        return str(out["error"])[:70]
    if name == "get_alert":
        return f"{out['rule']} / score {out['score']}"
    if name == "get_trader_profile":
        return f"{out['desk']} / {len(out['accounts_controlled'])} accounts"
    if name == "get_alert_history":
        return f"{out['prior_alerts_total']} prior alerts"
    if name == "get_order_activity":
        return f"{out['events_total']} events"
    if name == "get_market_context":
        return f"{out['move_bps']:+.1f} bps / {len(out['path'])} pts"
    if name == "search_chats":
        return f"{out.get('returned', 0)} messages"
    if name == "get_options_trades":
        return f"{len(out.get('trades', []))} option trades"
    if name == "get_client_orders":
        return f"{len(out.get('client_orders', []))} client orders"
    if name == "get_algo_orders":
        return f"{len(out.get('algo_orders', []))} algos"
    if name == "get_preclearance":
        return f"{len(out.get('requests', []))} requests"
    if name == "get_account":
        return f"{out.get('type', '?')} / {out.get('beneficial_owner', '?')}"
    return "ok"


class _Run:
    """Calls tools and records name, arguments, elapsed time and a one-line result."""

    def __init__(self):
        self.steps = []

    def __call__(self, name, **kwargs):
        fn = _tools().TOOLS[name]
        t0 = time.time()
        out = fn(**kwargs)
        self.steps.append({"tool": name, "args": kwargs, "ms": round((time.time() - t0) * 1000, 1),
                           "ok": not (isinstance(out, dict) and "error" in out), "returned": _brief(name, out)})
        return out


# --------------------------------------------------------------------------- case assembly

def _hedge_check(options, orders):
    """A claimed hedge has to match the options position in sign and in size."""
    trades = options.get("trades") or []
    if not trades:
        return None
    delta = sum(float(t.get("delta_change_in_underlying") or 0) for t in trades)
    net = 0.0
    for side, s in (orders.get("by_side") or {}).items():
        q = float(s.get("filled_qty") or 0)
        net += q if side == "BUY" else -q
    need = -delta                                     # hedge offsets the option delta
    reconciles = bool(need) and (need > 0) == (net > 0) and abs(net) <= max(abs(need) * 3, abs(need) + 1)
    return {"option_trades": len(trades), "option_delta": round(delta, 3),
            "hedge_required_side": "BUY" if need > 0 else "SELL", "hedge_required_qty": round(abs(need), 3),
            "observed_side": "BUY" if net > 0 else "SELL", "observed_qty": round(abs(net), 3),
            "ratio": round(abs(net) / abs(need), 1) if need else None,
            "same_direction": bool(need) and (need > 0) == (net > 0),
            "reconciles": reconciles}


def _order_stats(orders):
    by = orders.get("by_side") or {}
    typical = orders.get("trader_typical_order_qty_today")
    out = {"typical_order_qty": typical, "cancelled_order_median_life_s": orders.get("cancelled_order_median_life_s")}
    for side, s in by.items():
        new_qty, new_n, cxl = s.get("new_qty") or 0, s.get("new_orders") or 0, s.get("cancels") or 0
        out[side] = {"new_orders": new_n, "new_qty": new_qty, "median_new_qty": s.get("median_new_qty"),
                     "cancels": cxl, "cancel_fraction": round(cxl / new_n, 2) if new_n else None,
                     "fills": s.get("fills"), "filled_qty": s.get("filled_qty"), "vwap": s.get("vwap"),
                     "size_vs_typical": round((s.get("median_new_qty") or 0) / typical, 1) if typical else None}
    return out


def build_case(alert_id):
    T = _tools()
    run = _Run()
    alert = run("get_alert", alert_id=alert_id)
    if "error" in alert:
        return {"error": alert["error"]}
    start, end = _padded_window(alert)
    tid, sym = alert["trader_id"], alert["symbol"]

    profile = run("get_trader_profile", trader_id=tid)
    history = run("get_alert_history", trader_id=tid)
    orders = run("get_order_activity", start=start, end=end, trader_id=tid, symbol=sym, max_events=300)
    market = run("get_market_context", symbol=sym, start=start, end=end, points=PRICE_POINTS)
    # Reach past the window on both sides: the rationale is usually given before the
    # orders, and an attempt to steer the review comes after them.
    chat_start = _hms(_secs(start) - 10 * 60)
    chat_end = _hms(min(_secs(C.SESSION_START) + C.SESSION_HOURS * 3600 - 1, _secs(end) + 20 * 60))
    chats = run("search_chats", participant=tid, start=chat_start, end=chat_end, limit=30)

    extras = {}
    rule = alert["rule"]
    if rule in ("LAYERING", "EXCESSIVE_CANCELS"):
        # an options hedge can be booked well before the orders it is meant to cover
        extras["options"] = run("get_options_trades", trader_id=tid, underlying=sym,
                                start=_hms(_secs(start) - 60 * 60), end=end)
    if rule == "FRONT_RUNNING":
        extras["client_orders"] = run("get_client_orders", symbol=sym)
        extras["algos"] = run("get_algo_orders", trader_id=tid)
        if str(alert.get("account", "")).startswith("PA-"):
            extras["preclearance"] = run("get_preclearance", employee_id=tid)
    if rule == "WASH_TRADE":
        # a wash-trade alert names both sides, e.g. "AFF-0731<->LP-NOVA"
        pair = [p for p in str(alert["account"]).replace("<->", "|").split("|") if p]
        pair += [e["contra"] for e in orders.get("events", []) if e.get("contra") and e["contra"] not in pair]
        for key, acct in zip(("account", "contra_account"), dict.fromkeys(pair)):
            extras[key] = run("get_account", account_id=acct)

    disp = T._dispositions().get(alert_id)
    return {
        "alert": alert, "profile": profile, "history": history, "orders": orders, "market": market,
        "chats": chats, "extras": extras,
        "window": {"start": start, "end": end,
                   "alert_start": alert["window_start"], "alert_end": alert["window_end"]},
        "derived": {"order_stats": _order_stats(orders),
                    "hedge": _hedge_check(extras.get("options") or {}, orders)},
        "disposition": disp,
        "pipeline": run.steps,
        "elapsed_ms": round(sum(s["ms"] for s in run.steps), 1),
    }


def bootstrap():
    T = _tools()
    rows, offset = [], 0
    while True:                                   # list_alerts caps a page at 50
        page = T.list_alerts(status="all", limit=50, offset=offset)
        rows += page["alerts"]
        offset += len(page["alerts"])
        if len(rows) >= page["total_matching"] or not page["alerts"]:
            break
    alerts = {"alerts": rows, "total_matching": len(rows)}
    return {"day": C.day(),
            "days": sorted(p.name for p in C.DATA_ROOT.iterdir() if p.is_dir()) if C.DATA_ROOT.exists() else [],
            "firm": C.FIRM, "venue": C.VENUE,
            "session": {"start": C.SESSION_START, "hours": C.SESSION_HOURS},
            "model": {"name": MODEL["model"], "base_url": MODEL["base_url"], "enabled": bool(MODEL["model"])},
            "summary": T.triage_summary(), "alerts": alerts["alerts"], "total_alerts": alerts["total_matching"]}


# --------------------------------------------------------------------------- agent job

def start_triage(alert_id):
    """Run the real tool-calling loop for one alert, recording each tool call as it happens."""
    if not MODEL["model"]:
        return {"error": "no model configured; start with --model <name> (and --base-url for vLLM)"}
    from . import agent as A

    job_id = uuid.uuid4().hex[:12]
    job = {"job_id": job_id, "alert_id": alert_id, "state": "running", "steps": [],
           "started": time.time(), "model": MODEL["model"]}
    with JOBS_LOCK:
        JOBS[job_id] = job

    args = argparse.Namespace(base_url=MODEL["base_url"], model=MODEL["model"], api_key=MODEL["api_key"],
                              day=C.day(), max_steps=MODEL["max_steps"])

    def chat_fn(base_url, model, messages, tools, api_key="none", timeout=300):
        # every tool result already in the conversation = one completed step
        done = [m["name"] for m in messages if m.get("role") == "tool"]
        with JOBS_LOCK:
            job["steps"] = [{"tool": n, "state": "done"} for n in done] + [{"tool": "thinking", "state": "running"}]
            job["elapsed_s"] = round(time.time() - job["started"], 1)
        return A.chat(base_url, model, messages, tools, api_key, timeout)

    def work():
        try:
            out = A.triage(alert_id, args, chat_fn=chat_fn)
            with JOBS_LOCK:
                job.update(state="done", result=out,
                           steps=[{"tool": n, "state": "done"} for n in out["tool_calls"]],
                           elapsed_s=out["seconds"])
        except Exception as e:                                   # the endpoint may be down
            with JOBS_LOCK:
                job.update(state="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": job_id}


def job_state(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else {"error": "unknown job"}


# --------------------------------------------------------------------------- decisions

def record_decision(body):
    """A human decision from the dashboard. Goes through the same tools (so the audit log and
    dispositions stay the single source of truth) plus a reviews.jsonl note of who decided."""
    T = _tools()
    action = body.get("action")
    alert_id = body.get("alert_id")
    suspicion = body.get("suspicion", 50)
    if action == "close":
        out = T.close_alert(alert_id=alert_id, rationale=body.get("rationale", ""), suspicion=suspicion)
    elif action == "escalate":
        out = T.escalate_alert(alert_id=alert_id, title=body.get("title", ""), summary=body.get("summary", ""),
                               evidence=body.get("evidence") or [], suspicion=suspicion,
                               recommended_action=body.get("recommended_action",
                                                           "Review and decide on further action."))
    elif action == "reinvestigate":
        out = {"ok": True, "alert_id": alert_id, "status": "sent back for more investigation"}
    else:
        return {"error": "action must be close, escalate or reinvestigate"}
    if out.get("ok"):
        C.out_dir().mkdir(parents=True, exist_ok=True)
        with open(C.out_dir() / "reviews.jsonl", "a") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "alert_id": alert_id, "action": action, "suspicion": suspicion,
                                 "decided_by": "human (dashboard)",
                                 "note": body.get("rationale") or body.get("summary", "")}) + "\n")
    return out


# --------------------------------------------------------------------------- http

ROUTES_GET = {
    "/api/bootstrap": lambda q: bootstrap(),
    "/api/case": lambda q: build_case(q.get("alert_id", [""])[0]),
    "/api/triage": lambda q: job_state(q.get("job", [""])[0]),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "surveillance-web"

    def log_message(self, fmt, *a):
        if not self.path.startswith("/api/triage"):      # polling would flood the log
            print(f"  {self.command} {self.path}")

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ROUTES_GET:
            try:
                return self._send(200, ROUTES_GET[u.path](parse_qs(u.query)))
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._static(u.path)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": f"bad request body: {e}"})
        try:
            if u.path == "/api/decision":
                return self._send(200, record_decision(body))
            if u.path == "/api/triage":
                return self._send(200, start_triage(body.get("alert_id", "")))
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._send(404, {"error": "not found"})
        ctype, _ = mimetypes.guess_type(target.name)
        self._send(200, target.read_bytes(), ctype or "application/octet-stream")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--day", default=None, help="trading day to serve (default: demo day / SURV_DAY)")
    p.add_argument("--base-url", default="http://localhost:11434/v1", help="OpenAI-compatible endpoint")
    p.add_argument("--model", default=None, help="model name; without it the Run agent button is disabled")
    p.add_argument("--api-key", default="none")
    a = p.parse_args(argv)
    if a.day:
        os.environ["SURV_DAY"] = a.day
    MODEL.update(base_url=a.base_url, model=a.model, api_key=a.api_key)

    if not C.data_dir().exists():
        raise SystemExit(f"no data for {C.day()}; run: python -m surveillance.generate && python -m surveillance.detect")

    srv = ThreadingHTTPServer((a.host, a.port), partial(Handler))
    print(f"Trade surveillance dashboard   http://{a.host}:{a.port}")
    print(f"  day    {C.day()}   data {C.data_dir()}")
    print(f"  model  {a.model or '(none - Run agent disabled)'}  {a.base_url if a.model else ''}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
