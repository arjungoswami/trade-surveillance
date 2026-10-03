"""Rule-based surveillance: turns order events into alerts.

Run:  python -m surveillance.detect      (after generate; processes every day in data/)

These rules are intentionally simple and loose, like many production rule sets:
they catch the planted abuse but also flag plenty of legitimate activity.
Deciding which alerts matter is the agent's job.
"""
import json

import numpy as np
import pandas as pd

from . import config as C

T0 = None   # session start of the day being processed (set in run_day)


def iso(t):
    return (T0 + pd.Timedelta(seconds=float(t))).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(dd):
    o = pd.read_csv(dd / "orders.csv", keep_default_na=False)
    o["t"] = (pd.to_datetime(o["ts"], utc=True) - T0).dt.total_seconds()
    bbo = pd.read_csv(dd / "market_bbo.csv")
    mids = {s: ((g["bid"] + g["ask"]) / 2).to_numpy() for s, g in bbo.groupby("symbol")}
    acc = pd.read_csv(dd / "accounts.csv").set_index("account_id")
    co = pd.read_csv(dd / "client_orders.csv")
    tr = pd.read_csv(dd / "traders.csv", keep_default_na=False).set_index("trader_id")
    return o, mids, acc, co, tr


def order_table(g):
    """One row per order: side, qty, time placed, how it ended and when."""
    new = g[g["event"] == "NEW"].set_index("order_id")[["side", "qty", "price", "t"]]
    end = g[g["event"] != "NEW"].drop_duplicates("order_id", keep="last").set_index("order_id")[["event", "t"]]
    return new.join(end.rename(columns={"event": "end", "t": "t_end"}), how="left")


def mid_at(mids, sym, t):
    a = mids[sym]
    return float(a[int(min(max(t, 0), len(a) - 1))])


def merge_hits(hits, gap):
    """Collapse hits for the same trader/symbol that are within `gap` seconds of each other."""
    out = []
    for h in sorted(hits, key=lambda h: (h["trader_id"], h["symbol"], h["t_start"])):
        last = out[-1] if out else None
        if last and last["trader_id"] == h["trader_id"] and last["symbol"] == h["symbol"] \
                and h["t_start"] - last["t_end"] <= gap:
            last["t_end"] = max(last["t_end"], h["t_end"])
            last["order_ids"] = sorted(set(last["order_ids"]) | set(h["order_ids"]))
            last["n_triggers"] += 1
            for k, v in h["metrics"].items():
                if isinstance(v, (int, float)):
                    last["metrics"][k] = max(last["metrics"][k], v)
        else:
            out.append({**h, "n_triggers": 1})
    return out


# --------------------------------------------------------------------------- rules

def rule_layering(o, mids):
    P = C.RULES["layering"]
    hits = []
    for (tid, acct, sym), g in o.groupby(["trader_id", "account", "symbol"]):
        if acct.startswith("C-"):
            continue
        ords = order_table(g)
        fills = g[(g["event"] == "FILL") & (g["contra_account"] == "EXT")]
        for _, f in fills.iterrows():
            opp = "BUY" if f["side"] == "SELL" else "SELL"
            cand = ords[(ords["side"] == opp) & (ords["t"] >= f["t"] - P["lookback_s"]) & (ords["t"] <= f["t"])]
            if len(cand) < P["min_orders"]:
                continue
            canc = cand[(cand["end"] == "CANCEL") & (cand["t_end"] <= f["t"] + P["cancel_within_s"])]
            frac = len(canc) / len(cand)
            ratio = cand["qty"].sum() / f["qty"]
            if frac < P["min_cancel_frac"] or ratio < P["min_qty_ratio"]:
                continue
            t_start = float(cand["t"].min())
            sign = 1 if f["side"] == "SELL" else -1      # seller benefits from a rise
            move = sign * (mid_at(mids, sym, f["t"]) / mid_at(mids, sym, t_start) - 1) * 1e4
            hits.append(dict(rule="LAYERING", trader_id=tid, account=acct, symbol=sym,
                             t_start=t_start, t_end=float(max(f["t"], canc["t_end"].max())),
                             order_ids=sorted(list(cand.index) + [f["order_id"]]),
                             metrics=dict(opposite_orders=len(cand), opposite_qty=round(float(cand["qty"].sum()), 3),
                                          exec_side=f["side"], exec_qty=float(f["qty"]),
                                          qty_ratio=round(float(ratio), 2), cancel_fraction=round(frac, 2),
                                          favourable_move_bps=round(move, 2))))
    return merge_hits(hits, P["merge_gap_s"])


def rule_cancels(o):
    P = C.RULES["cancels"]
    o = o[~o["account"].str.startswith("C-")].copy()
    o["bucket"] = (o["t"] // (P["bucket_min"] * 60)).astype(int)
    hits = []
    for (tid, acct, sym, b), g in o.groupby(["trader_id", "account", "symbol", "bucket"]):
        n_new = int((g["event"] == "NEW").sum())
        n_cxl = int((g["event"] == "CANCEL").sum())
        n_fill = int((g["event"] == "FILL").sum())
        if n_new >= P["min_new"] and n_cxl / n_new >= P["min_cancel_ratio"] and n_fill <= P["max_fills"]:
            t0 = b * P["bucket_min"] * 60
            hits.append(dict(rule="EXCESSIVE_CANCELS", trader_id=tid, account=acct, symbol=sym,
                             t_start=float(t0), t_end=float(t0 + P["bucket_min"] * 60), order_ids=[],
                             n_triggers=1,
                             metrics=dict(new_orders=n_new, cancels=n_cxl, fills=n_fill,
                                          cancel_ratio=round(n_cxl / n_new, 3))))
    return hits


def rule_wash(o, acc):
    P = C.RULES["wash"]
    ctrl = acc["controlled_by"].to_dict()
    trader_by_acct = o.drop_duplicates("account").set_index("account")["trader_id"].to_dict()
    f = o[(o["event"] == "FILL") & (o["side"] == "SELL") & (o["contra_account"] != "EXT")].copy()
    f = f[f["account"].map(ctrl) == f["contra_account"].map(ctrl)]
    hits = []
    for _, r in f.iterrows():
        # attribute to the firm trader, not the client DMA session
        tid = r["trader_id"] if not str(r["trader_id"]).startswith("CLIENT") \
            else trader_by_acct.get(r["contra_account"], r["trader_id"])
        hits.append(dict(rule="WASH_TRADE", trader_id=tid, account=f"{r['account']}<->{r['contra_account']}",
                         symbol=r["symbol"], t_start=float(r["t"]), t_end=float(r["t"]),
                         order_ids=[r["order_id"]],
                         metrics=dict(crosses=1, qty=float(r["qty"]), controlled_by=ctrl[r["account"]])))
    # merge by symbol + account pair regardless of which side sold
    for h in hits:
        h["_pair"] = "|".join(sorted(h["account"].split("<->")))
    out = []
    for h in sorted(hits, key=lambda h: (h["_pair"], h["symbol"], h["t_start"])):
        last = out[-1] if out else None
        if last and last["_pair"] == h["_pair"] and last["symbol"] == h["symbol"] and h["t_start"] - last["t_end"] <= P["merge_gap_s"]:
            last["t_end"] = h["t_end"]
            last["order_ids"] += h["order_ids"]
            last["metrics"]["crosses"] += 1
            last["metrics"]["qty"] = round(last["metrics"]["qty"] + h["metrics"]["qty"], 3)
            last["n_triggers"] += 1
        else:
            out.append({**h, "n_triggers": 1})
    for h in out:
        h["account"] = h.pop("_pair").replace("|", "<->")
    return out


def rule_front_running(o, co, tr):
    P = C.RULES["front_running"]
    desk = tr["desk"].to_dict()
    hits = []
    for _, c in co.iterrows():
        rx = (pd.Timestamp(c["received_at"]) - T0).total_seconds()
        last = (pd.Timestamp(c["last_fill_at"]) - T0).total_seconds()
        end = min(rx + P["max_window_s"], last)
        desk_traders = [t for t, d in desk.items() if d == desk.get(c["handling_trader"])]
        f = o[(o["event"] == "FILL") & (o["symbol"] == c["symbol"]) & (o["side"] == c["side"])
              & o["trader_id"].isin(desk_traders) & ~o["account"].str.startswith("C-")
              & (o["t"] >= rx) & (o["t"] <= end)]
        for (tid, acct), g in f.groupby(["trader_id", "account"]):
            first = float(g["t"].min())
            hits.append(dict(rule="FRONT_RUNNING", trader_id=tid, account=acct, symbol=c["symbol"],
                             t_start=rx, t_end=float(g["t"].max()), order_ids=list(g["order_id"]),
                             n_triggers=len(g),
                             metrics=dict(client_order_id=c["client_order_id"], client=c["client"],
                                          client_side=c["side"], client_qty=float(c["qty"]),
                                          handling_trader=c["handling_trader"],
                                          own_fills=len(g), own_qty=round(float(g["qty"].sum()), 3),
                                          seconds_after_receipt=round(first - rx, 1))))
    return hits


# --------------------------------------------------------------------------- scoring + output

def score(h):
    m = h["metrics"]
    if h["rule"] == "LAYERING":
        s = 25 + 8 * min(m["qty_ratio"], 6) + 3 * max(min(m["favourable_move_bps"], 10), 0)
    elif h["rule"] == "EXCESSIVE_CANCELS":
        s = 20 + 100 * (m["cancel_ratio"] - 0.95)
    elif h["rule"] == "WASH_TRADE":
        s = 50 + 5 * min(m["crosses"], 6)
    else:
        s = 45 + max(0, 30 - m["seconds_after_receipt"] / 60)
    return int(min(round(s), 99))


def summarise(h, names):
    m, who = h["metrics"], names.get(h["trader_id"], h["trader_id"])
    if h["rule"] == "LAYERING":
        return (f"{who} placed {m['opposite_orders']} {('BUY' if m['exec_side'] == 'SELL' else 'SELL')} orders "
                f"({m['opposite_qty']} total) then {m['exec_side']} {m['exec_qty']}; "
                f"{int(m['cancel_fraction'] * 100)}% of the opposite orders cancelled")
    if h["rule"] == "EXCESSIVE_CANCELS":
        return f"{who}: {m['cancels']}/{m['new_orders']} orders cancelled with {m['fills']} fills in 15 min"
    if h["rule"] == "WASH_TRADE":
        return f"{m['crosses']} crosses between {h['account']} (both controlled by {m['controlled_by']}), {m['qty']} total"
    return (f"{who} traded {m['own_qty']} {m['client_side']} for {h['account']} starting "
            f"{m['seconds_after_receipt']}s after {m['client']} order {m['client_order_id']} was received")


def run_day(day):
    global T0
    T0 = pd.Timestamp(f"{day}T{C.SESSION_START}", tz="UTC")
    dd = C.data_dir(day)
    o, mids, acc, co, tr = load(dd)
    hits = rule_layering(o, mids) + rule_cancels(o) + rule_wash(o, acc) + rule_front_running(o, co, tr)
    names = tr["name"].to_dict()
    hits.sort(key=lambda h: (h["t_end"], h["rule"]))
    rows = []
    for i, h in enumerate(hits, 1):
        rows.append({"alert_id": f"A-{i:04d}", "rule": h["rule"], "trader_id": h["trader_id"],
                     "trader_name": names.get(h["trader_id"], h["trader_id"]), "account": h["account"],
                     "symbol": h["symbol"], "window_start": iso(h["t_start"]), "window_end": iso(h["t_end"]),
                     "raised_at": iso(h["t_end"] + 60), "score": score(h), "summary": summarise(h, names),
                     "n_triggers": h["n_triggers"],
                     "metrics": json.dumps(h["metrics"]), "order_ids": json.dumps(h["order_ids"][:50])})
    df = pd.DataFrame(rows)
    df.to_csv(dd / "alerts.csv", index=False)
    print(f"{day}: {len(df)} alerts  " + "  ".join(f"{k}={v}" for k, v in df["rule"].value_counts().items()))


def main():
    for d in sorted(p.name for p in C.DATA_ROOT.iterdir() if p.is_dir()):
        run_day(d)


if __name__ == "__main__":
    main()
