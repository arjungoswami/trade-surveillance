"""Score the agent's decisions against the planted answer key.

  python -m surveillance.evaluate                 # demo day
  python -m surveillance.evaluate --all           # every generated day pooled
  python -m surveillance.evaluate --all --roc-csv roc.csv

Answer key: an alert raised on a planted ABUSIVE episode (same trader, symbol, account, overlapping time)
should be escalated. Everything else (benign look-alikes and background noise) should be closed.

Reports:
  * confusion counts on decided alerts (positive = escalate)
  * recall on abuse, accuracy on the hard benign look-alikes, false-escalation rate on everything else
  * ROC AUC of the agent's suspicion score vs. the rule engine's own score (the baseline)
"""
import argparse
import json

import pandas as pd

from . import config as C


def auc(scores, labels):
    """Probability a random positive outranks a random negative (ties count half)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def roc_points(scores, labels, thresholds=range(0, 101, 10)):
    P = sum(labels)
    Nn = len(labels) - P
    out = []
    for th in thresholds:
        tp = sum(1 for s, y in zip(scores, labels) if y and s >= th)
        fp = sum(1 for s, y in zip(scores, labels) if not y and s >= th)
        out.append({"threshold": th, "tpr": tp / P if P else None, "fpr": fp / Nn if Nn else None,
                    "tp": tp, "fp": fp})
    return out


def load_day(day):
    alerts = pd.read_csv(C.data_dir(day) / "alerts.csv")
    gt = json.loads((C.eval_dir(day) / "ground_truth.json").read_text())
    disp = {}
    path = C.out_dir(day) / "dispositions.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                disp[d["alert_id"]] = d
    rows = []
    for r in alerts.itertuples():
        accts = r.account.split("<->")
        g = next((g for g in gt if r.trader_id == g["trader_id"] and r.symbol == g["symbol"]
                  and any(a in accts for a in g["accounts"])
                  and r.window_start <= g["end"] and r.window_end >= g["start"]), None)
        d = disp.get(r.alert_id, {})
        rows.append({"day": day, "alert_id": r.alert_id, "rule": r.rule, "trader": r.trader_name,
                     "rule_score": int(r.score), "episode": g["id"] if g else "",
                     "kind": ("abuse" if g["abusive"] else "look-alike") if g else "background",
                     "y": bool(g and g["abusive"]), "disguise": (g or {}).get("disguise", ""),
                     "did": d.get("decision", "open"),
                     "suspicion": d.get("suspicion")})
    return pd.DataFrame(rows), gt


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--day", default=C.day())
    p.add_argument("--all", action="store_true", help="pool every generated day")
    p.add_argument("--roc-csv", help="write ROC points for the agent's suspicion score to this CSV")
    args = p.parse_args(argv)
    days = sorted(x.name for x in C.DATA_ROOT.iterdir() if x.is_dir()) if args.all else [args.day]

    frames, gts = [], {}
    for d in days:
        df, gt = load_day(d)
        frames.append(df)
        gts[d] = gt
    df = pd.concat(frames, ignore_index=True)

    n_abuse, n_look = int((df["kind"] == "abuse").sum()), int((df["kind"] == "look-alike").sum())
    print(f"Days: {', '.join(days)}")
    print(f"Alerts: {len(df)}  =  {n_abuse} real abuse ({int((df['disguise'] != '').sum())} disguised)  +  "
          f"{n_look} benign look-alikes  +  {len(df) - n_abuse - n_look} background noise")
    print(f"Rule-engine precision (share of alerts that are real abuse): {n_abuse / len(df):.1%}")

    base = auc(df["rule_score"].tolist(), df["y"].tolist())
    if base is not None:
        print(f"Baseline: rule-score ROC AUC = {base:.3f} (0.5 = random)")
        ranks = df["rule_score"].rank(ascending=False, method="min")
        worst = int(ranks[df["y"]].max())
        print(f"          sorting by rule score, the last real case is at position {worst} of {len(df)}")

    dec = df[df["did"] != "open"]
    print(f"\nAgent decided {len(dec)} of {len(df)} alerts ({(df['did'] == 'open').sum()} still open)")
    if len(dec):
        tp = int(((dec["did"] == "escalated") & dec["y"]).sum())
        fn = int(((dec["did"] == "closed") & dec["y"]).sum())
        fp = int(((dec["did"] == "escalated") & ~dec["y"]).sum())
        tn = int(((dec["did"] == "closed") & ~dec["y"]).sum())
        print(f"  Escalated real abuse (TP): {tp}   Closed real abuse (FN, the costly error): {fn}")
        print(f"  Escalated benign (FP):     {fp}   Closed benign (TN): {tn}")
        if tp + fn:
            print(f"  Recall on abuse: {tp / (tp + fn):.0%}")
        dis = dec[dec["disguise"] != ""]
        if len(dis):
            caught = int((dis["did"] == "escalated").sum())
            kinds = ", ".join(f"{k}: {int((g['did'] == 'escalated').sum())}/{len(g)}" for k, g in dis.groupby("disguise"))
            print(f"  Disguised abuse escalated: {caught}/{len(dis)}  ({kinds})")
        look = dec[dec["kind"] == "look-alike"]
        if len(look):
            print(f"  Hard look-alikes correctly closed: {(look['did'] == 'closed').sum()}/{len(look)}")
        bg = dec[dec["kind"] == "background"]
        if len(bg):
            print(f"  Background false-escalation rate: {(bg['did'] == 'escalated').mean():.1%}")
        if tp + fp:
            print(f"  Precision of escalations: {tp / (tp + fp):.0%}")

        scored = dec[dec["suspicion"].notna()]
        a = auc(scored["suspicion"].tolist(), scored["y"].tolist())
        b = auc(scored["rule_score"].tolist(), scored["y"].tolist())
        if a is not None:
            print(f"\n  ROC AUC on decided alerts: agent suspicion = {a:.3f}   vs rule score = {b:.3f}")
            pts = roc_points(scored["suspicion"].tolist(), scored["y"].tolist())
            print("  threshold  TPR    FPR")
            for pt in pts:
                print(f"     {pt['threshold']:>3}    {pt['tpr']:.2f}   {pt['fpr']:.3f}")
            if args.roc_csv:
                pd.DataFrame(pts).to_csv(args.roc_csv, index=False)
                print(f"  ROC points written to {args.roc_csv}")
        elif len(scored):
            print("\n  ROC AUC needs at least one real-abuse and one benign alert among the decided ones.")

    print("\nPlanted episodes:")
    for d in days:
        for g in gts[d]:
            hit = df[(df["day"] == d) & (df["episode"] == g["id"])]
            want = "escalated" if g["abusive"] else "closed"
            got = ", ".join(f"{r.alert_id}={r.did}" + (f"({int(r.suspicion)})" if pd.notna(r.suspicion) else "")
                            for r in hit.itertuples()) or "NO ALERT"
            ok = len(hit) and (hit["did"] == want).all()
            tag = f"[{g['disguise']}] " if g.get("disguise") else ""
            print(f"  {'OK' if ok else '--'}  {d} {g['id']:<5} {g['pattern']:<14} {g['trader_id']} {g['symbol']:<9} {tag}"
                  f"want {want:<9} got {got}")


if __name__ == "__main__":
    main()
