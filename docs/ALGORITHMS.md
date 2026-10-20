# Core Algorithms & Mathematical Formulations
## AI Personal Financial Agent (Fintechathon)

This system uses a **deterministic, rule-based, and statistical AI-ready architecture** designed for high transparency, zero hallucination risk, and instant auditability.

---

### 1. Financial Health Score Algorithm (0 - 100 Points)

The Financial Health Score provides a holistic snapshot of a user's financial resilience across 4 core financial pillars:

$$\text{Health Score} = S_{\text{savings}} + S_{\text{essential}} + S_{\text{discretionary}} + S_{\text{runway}}$$

#### Pillar 1: Savings Ratio (Max 30 pts)
$$\text{Savings Rate} = \frac{\text{Monthly Income} - \text{Total Expenses}}{\text{Monthly Income}} \times 100\%$$
- $\text{Savings Rate} \ge 30\% \implies 30\text{ pts}$
- $20\% \le \text{Savings Rate} < 30\% \implies 25\text{ pts}$
- $10\% \le \text{Savings Rate} < 20\% \implies 18\text{ pts}$
- $0\% < \text{Savings Rate} < 10\% \implies 10\text{ pts}$
- $\text{Savings Rate} \le 0\% \implies 0\text{ pts}$

#### Pillar 2: Essential Spending Adherence (50/30/20 Rule) (Max 25 pts)
$$\text{Essential Ratio} = \frac{\text{Essential Expenses (Needs)}}{\text{Monthly Income}} \times 100\%$$
- $\text{Essential Ratio} \le 50\% \implies 25\text{ pts}$
- $50\% < \text{Essential Ratio} \le 60\% \implies 20\text{ pts}$
- $60\% < \text{Essential Ratio} \le 75\% \implies 12\text{ pts}$
- $\text{Essential Ratio} > 75\% \implies 5\text{ pts}$

#### Pillar 3: Discretionary Spending Control (Max 20 pts)
$$\text{Discretionary Ratio} = \frac{\text{Discretionary Expenses (Wants)}}{\text{Monthly Income}} \times 100\%$$
- $\text{Discretionary Ratio} \le 20\% \implies 20\text{ pts}$
- $20\% < \text{Discretionary Ratio} \le 30\% \implies 15\text{ pts}$
- $30\% < \text{Discretionary Ratio} \le 40\% \implies 10\text{ pts}$
- $\text{Discretionary Ratio} > 40\% \implies 3\text{ pts}$

#### Pillar 4: Emergency Runway Buffer (Max 25 pts)
$$\text{Runway (Months)} = \frac{\text{Current Savings}}{\text{Total Monthly Expenses}}$$
- $\text{Runway} \ge 6.0\text{ months} \implies 25\text{ pts}$
- $3.0 \le \text{Runway} < 6.0\text{ months} \implies 20\text{ pts}$
- $1.0 \le \text{Runway} < 3.0\text{ months} \implies 12\text{ pts}$
- $\text{Runway} < 1.0\text{ month} \implies 4\text{ pts}$

---

### 2. Spending Anomaly Detection (Moving Average & Z-Score)

To identify unusual impulse purchases or fraudulent spikes, transactions are evaluated within their spending category:

#### Category Mean & Sample Standard Deviation:
$$\mu_{\text{cat}} = \frac{1}{N} \sum_{i=1}^{N} x_i, \quad \sigma_{\text{cat}} = \sqrt{\frac{1}{N-1}\sum_{i=1}^{N}(x_i - \mu_{\text{cat}})^2}$$

#### Standardized Z-Score:
$$Z = \frac{x - \mu_{\text{cat}}}{\sigma_{\text{cat}}}$$

#### Severity Thresholds:
- **HIGH Severity Anomaly**: $Z \ge +3.0$ OR ($x \ge 4.0 \times \mu_{\text{cat}}$ with $x > 1,000,000\text{ ₫}$)
- **MEDIUM Severity Anomaly**: $+2.0 \le Z < +3.0$ OR ($x \ge 2.5 \times \mu_{\text{cat}}$ with $x > 500,000\text{ ₫}$)
- **Normal Transaction**: $Z < 2.0$

---

### 3. Goal Planner & 3-Tier Strategy Engine

Given:
- Target savings: $T$
- Current savings: $S_0$
- Remaining months: $M = (Y_{\text{target}} - Y_{\text{current}}) \times 12 + (M_{\text{target}} - M_{\text{current}})$
- Required monthly savings: $R = \frac{T - S_0}{M}$

The engine calculates 3 distinct optimization strategies:

| Strategy | Essential Cut | Non-Essential Cut | Objective | Feasibility |
| :--- | :--- | :--- | :--- | :--- |
| **Conservative** | 0% | 12% | Minimal lifestyle disturbance | Easy / High Comfort |
| **Balanced (Recommended)** | 5% | 28% | 50/30/20 equilibrium | Moderate / Optimal |
| **Aggressive** | 10% | 48% | Sprint to target | Challenging / Fast |

For each strategy $k$:
$$\text{Total Monthly Savings}_k = \text{Baseline Surplus} + \sum_{c \in \text{Categories}} (\text{Spending}_c \times \text{CutRate}_{k,c})$$
$$\text{Months to Target}_k = \frac{T - S_0}{\text{Total Monthly Savings}_k}$$

---

### 4. What-If Financial Decision Simulator

Simulates the prospective impact of purchases, expense changes, or income variations:

#### One-off Purchase ($P$):
$$\text{New Savings} = \max(0, S_0 - P)$$
$$\Delta \text{Timeline (Months)} = \frac{T - \text{New Savings}}{\text{Surplus}} - \frac{T - S_0}{\text{Surplus}} = \frac{P}{\text{Surplus}}$$

#### Recurring Expense Shift ($\Delta E$):
$$\text{New Surplus} = \text{Income} - (\text{Expense} + \Delta E)$$
$$\Delta \text{Timeline (Months)} = \frac{T - S_0}{\text{New Surplus}} - \frac{T - S_0}{\text{Surplus}}$$
