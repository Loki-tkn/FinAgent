import sqlite3
import numpy as np
from typing import List, Dict, Any
from app.database import get_db_connection
from app.services.quant import robust_anomalies
from app.copilot.hooks import emit_risk_metric
from app.copilot.state import ContextSeverity

MIN_SAMPLES = 5


def detect_spending_anomalies(user_id: int = 1, threshold_z: float = 3.5, persist: bool = True) -> Dict[str, Any]:
    """
    Detects unusual expenses using robust Modified Z-Score (Median / MAD on log amounts).
    - Skips source='summary' rows (never flagged, not counted in total_scanned).
    - Requires n >= MIN_SAMPLES per category for z-score flagging.
    - Never flags amount < 50.
    - persist=True updates DB; persist=False only computes.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, category, amount, description, date, is_essential, is_anomaly, COALESCE(source, 'manual') as source
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
        ORDER BY date ASC, id ASC
    """, (user_id,))
    all_expenses = cursor.fetchall()
    conn.close()

    # Exclude summary rows from scanning
    expenses = [r for r in all_expenses if r["source"] != "summary"]

    if not expenses:
        return {
            "total_scanned": 0,
            "anomalies_detected": 0,
            "anomalies": [],
            "eligible_categories": 0,
            "skipped_categories": [],
            "min_samples": MIN_SAMPLES,
            "message": "No transactions to scan yet."
        }

    # Group by category
    cat_txs: Dict[str, List[Dict[str, Any]]] = {}
    for row in expenses:
        cat = row["category"]
        if cat not in cat_txs:
            cat_txs[cat] = []
        cat_txs[cat].append({
            "id": row["id"],
            "amount": float(row["amount"]),
            "description": row["description"],
            "date": row["date"],
            "is_essential": row["is_essential"],
            "is_anomaly": bool(row["is_anomaly"])
        })

    anomalies: List[Dict[str, Any]] = []
    skipped_categories = []
    eligible_count = 0
    flagged_ids = set()
    clear_ids = set()

    for cat, tx_list in cat_txs.items():
        if len(tx_list) < MIN_SAMPLES:
            skipped_categories.append({"category": cat, "count": len(tx_list)})
            # ratio-only rules for small categories
            amounts = [t["amount"] for t in tx_list]
            med_amt = float(np.median(amounts)) if amounts else 0.0
            for t in tx_list:
                amt = t["amount"]
                if amt < 50:
                    clear_ids.add(t["id"])
                    continue
                is_anomaly = False
                severity = "LOW"
                reason = ""
                if med_amt > 0 and amt >= 4.0 * med_amt and amt > 500:
                    is_anomaly = True
                    severity = "HIGH"
                    reason = f"Critical spike! ${amt:,.0f} is {amt/med_amt:.1f}x the category median (${med_amt:,.0f})."
                elif med_amt > 0 and amt >= 2.5 * med_amt and amt > 300:
                    is_anomaly = True
                    severity = "MEDIUM"
                    reason = f"Unusual expense. ${amt:,.0f} higher than typical {cat} median (${med_amt:,.0f})."
                if is_anomaly:
                    flagged_ids.add(t["id"])
                    anomalies.append({
                        "id": t["id"],
                        "date": t["date"],
                        "category": cat,
                        "amount": amt,
                        "description": t["description"],
                        "z_score": 0.0,
                        "moving_average": round(med_amt, 2),
                        "std_dev": 0.0,
                        "severity": severity,
                        "reason": reason
                    })
                else:
                    clear_ids.add(t["id"])
            continue

        eligible_count += 1
        amounts = [t["amount"] for t in tx_list]
        mod_z_scores = robust_anomalies(amounts)
        med_amt = float(np.median(amounts)) if amounts else 0.0

        for t, z_val in zip(tx_list, mod_z_scores):
            tx_id = t["id"]
            amt = t["amount"]
            z_score = round(float(z_val), 2)
            is_anomaly = False
            severity = "LOW"
            reason = ""

            if amt < 50:
                clear_ids.add(tx_id)
                continue

            if z_score >= 4.5 or (med_amt > 0 and amt >= 4.0 * med_amt and amt > 500):
                is_anomaly = True
                severity = "HIGH"
                reason = f"Critical spike! Robust Z: +{z_score:.2f}. Spend of ${amt:,.0f} is {amt/med_amt:.1f}x the category median (${med_amt:,.0f})."
            elif z_score >= threshold_z or (med_amt > 0 and amt >= 2.5 * med_amt and amt > 300):
                is_anomaly = True
                severity = "MEDIUM"
                reason = f"Unusual expense. Robust Z: +{z_score:.2f}. Higher than typical {cat} median (${med_amt:,.0f})."

            if is_anomaly:
                flagged_ids.add(tx_id)
                anomalies.append({
                    "id": tx_id,
                    "date": t["date"],
                    "category": cat,
                    "amount": amt,
                    "description": t["description"],
                    "z_score": z_score,
                    "moving_average": round(med_amt, 2),
                    "std_dev": 0.0,
                    "severity": severity,
                    "reason": reason
                })
            else:
                clear_ids.add(tx_id)

    if persist:
        conn2 = get_db_connection()
        cursor2 = conn2.cursor()
        for anm in anomalies:
            cursor2.execute(
                "UPDATE transactions SET is_anomaly = 1, anomaly_reason = ? WHERE id = ?",
                (anm["reason"], anm["id"])
            )
        for tid in clear_ids:
            cursor2.execute(
                "UPDATE transactions SET is_anomaly = 0, anomaly_reason = NULL WHERE id = ?",
                (tid,)
            )
        conn2.commit()
        conn2.close()

    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    anomalies.sort(key=lambda x: (severity_rank.get(x["severity"], 3), x["date"]))

    if anomalies:
        has_high = any(a.get("severity") == "HIGH" for a in anomalies)
        emit_risk_metric(
            metrics={
                "metric_type": "SPENDING_ANOMALIES_EVALUATION",
                "user_id": user_id,
                "total_scanned": len(expenses),
                "anomalies_detected": len(anomalies),
                "high_severity_count": sum(1 for a in anomalies if a.get("severity") == "HIGH"),
                "top_anomalies": anomalies[:3],
            },
            severity=ContextSeverity.WARNING if has_high else ContextSeverity.INFO,
            source="agent",
        )

    if eligible_count == 0 and len(expenses) > 0:
        msg = f"Scanned {len(expenses)} transactions. No category has {MIN_SAMPLES}+ samples for z-score analysis."
    elif len(anomalies) == 0 and eligible_count > 0:
        msg = "All expenses match baseline — no anomalies detected."
    else:
        msg = f"Scanned {len(expenses)} transactions across {eligible_count} eligible categories."

    return {
        "total_scanned": len(expenses),
        "anomalies_detected": len(anomalies),
        "anomalies": anomalies,
        "eligible_categories": eligible_count,
        "skipped_categories": skipped_categories,
        "min_samples": MIN_SAMPLES,
        "message": msg
    }
