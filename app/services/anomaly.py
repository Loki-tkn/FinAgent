import numpy as np
from typing import List, Dict, Any
from app.database import get_db_connection
from app.services.quant import robust_anomalies

def detect_spending_anomalies(user_id: int = 1, threshold_z: float = 3.5) -> Dict[str, Any]:
    """
    Detects unusual expenses using robust Modified Z-Score (Median / MAD on log amounts).
    Flags expenses with modified Z-score > threshold_z (default 3.5).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, category, amount, description, date, is_essential, is_anomaly
        FROM transactions
        WHERE user_id = ? AND type = 'expense'
        ORDER BY date ASC, id ASC
    """, (user_id,))
    expenses = cursor.fetchall()
    conn.close()

    if not expenses:
        return {
            "total_scanned": 0,
            "anomalies_detected": 0,
            "anomalies": []
        }

    # Group transactions by category
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
    conn = get_db_connection()
    cursor = conn.cursor()

    for cat, tx_list in cat_txs.items():
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

            if z_score >= 4.5 or (med_amt > 0 and amt >= 4.0 * med_amt and amt > 500):
                is_anomaly = True
                severity = "HIGH"
                reason = f"Critical spike! Robust Z: +{z_score:.2f}. Spend of ${amt:,.0f} is {amt/med_amt:.1f}x the category median (${med_amt:,.0f})."
            elif z_score >= threshold_z or (med_amt > 0 and amt >= 2.5 * med_amt and amt > 300):
                is_anomaly = True
                severity = "MEDIUM"
                reason = f"Unusual expense. Robust Z: +{z_score:.2f}. Higher than typical {cat} median (${med_amt:,.0f})."

            if is_anomaly:
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
                cursor.execute("""
                    UPDATE transactions 
                    SET is_anomaly = 1, anomaly_reason = ? 
                    WHERE id = ?
                """, (reason, tx_id))
            else:
                cursor.execute("""
                    UPDATE transactions 
                    SET is_anomaly = 0, anomaly_reason = NULL 
                    WHERE id = ?
                """, (tx_id,))

    conn.commit()
    conn.close()

    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    anomalies.sort(key=lambda x: (severity_rank.get(x["severity"], 3), x["date"]), reverse=False)

    return {
        "total_scanned": len(expenses),
        "anomalies_detected": len(anomalies),
        "anomalies": anomalies
    }
