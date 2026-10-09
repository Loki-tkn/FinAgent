# AI Personal Financial Agent
### Fintechathon MVP | Intelligent Personal Wealth & Savings Planner

A lightweight, high-performance web application designed to help users analyze personal cash flow, establish achievable savings goals, simulate financial decisions, detect irregular spending anomalies, and stage budget changes inside a secure sandbox before execution.

> **Disclaimer:** All financial figures, bank categories, and transactions within this application are strictly **simulated demo data** formatted in Vietnamese Dong (VND). This is an educational budgeting and financial optimization agent, **not** an investment advice platform.

---

## 🌟 Key Features

1. **Intelligent Financial Dashboard:**
   - Real-time income, expense, cash flow surplus, and savings rate calculation.
   - **0–100 Financial Health Score:** Multi-pillar diagnostic assessing Savings Rate, Essential Needs (50/30/20 rule), Discretionary Wants, and Emergency Runway Buffer.
   - Interactive Chart.js charts: Monthly Income vs Expense trends and Expense Category donut distribution.
2. **AI Goal Planner (3 Concrete Strategies):**
   - Calculates exact required monthly savings and timeline to reach targets.
   - Automatically generates 3 personalized plans:
     - **Conservative:** Minimal lifestyle impact (12% non-essential cut).
     - **Balanced (Recommended):** Optimal 50/30/20 lifestyle optimization (28% non-essential cut + 5% smart savings).
     - **Aggressive:** Rapid milestone sprint (48% non-essential cut + 10% strict trimming).
   - Provides concrete itemized category cuts and optimization rationales.
3. **Statistical Spending Anomaly Detection:**
   - Evaluates transactions against category rolling moving averages and statistical Z-Scores ($Z = \frac{x - \mu}{\sigma}$).
   - Classifies anomalies into `HIGH` and `MEDIUM` risk tiers with descriptive alerts for impulse spending.
4. **What-If Decision Simulator:**
   - Simulates prospective financial decisions: one-off major purchases (e.g., iPhone, laptop, vacation), recurring expense increases (e.g., rent increase), and salary promotions.
   - Visualizes exact delays or accelerations in achieving savings targets.
5. **Milestone Progress Tracking:**
   - Compares accumulated savings against projected milestone trajectories.
   - Provides real-time status labels (`On Track`, `Ahead of Schedule`, `Behind Schedule`) and dynamic catch-up recommendations.
6. **Sandbox Mode & Approval Workflow:**
   - Staged financial proposals require explicit user authorization (`APPROVE` / `REJECT`) before updating active budgets.
7. **Immutable Audit Trail:**
   - Permanent log of timestamps, role tiers, actions, payloads, client IPs, and status outcomes.

---

## 🛠️ Technology Stack

- **Backend:** Python 3.10+ / FastAPI (high-speed asynchronous REST API)
- **Database:** SQLite 3 (lightweight, zero-config local persistence with foreign key constraints)
- **Validation:** Pydantic v2 schemas
- **Frontend:** Vanilla HTML5, Modern CSS3 (Dark Fintech Design System with Glassmorphism), JavaScript (ES6+ SPA)
- **Data Visualization:** Chart.js & Lucide Icons
- **Currency Engine:** Vietnamese Dong (VND: `xx.xxx.xxx ₫`)

---

## 🚀 Windows Setup & Run Instructions

### Prerequisites
- Python 3.10 or higher installed on Windows.
- PowerShell or Command Prompt.

### Step 1: Clone or Navigate to Directory
```powershell
cd c:\Users\trann\OneDrive\Desktop\Fintechathon
```

### Step 2: Install Dependencies
```powershell
pip install -r requirements.txt
```

### Step 3: Launch the Application
Run the local FastAPI server:
```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Or simply run:
```powershell
python -m app.main
```

### Step 4: Open in Web Browser
Open your browser and navigate to:
```
http://127.0.0.1:8000
```

---

## 🛡️ Security & Role-Based Access Control (RBAC)

The application implements three permission tiers, switchable directly from the top-right header for easy evaluator testing:

| Tier | Capabilities |
| :--- | :--- |
| **Viewer** | Read-only inspection of dashboards, goal trajectories, anomaly logs, and audit trails. |
| **User** | Full access to add/delete transactions, generate AI plans, execute What-If simulations, and authorize Sandbox proposals. |
| **Admin** | System management, audit trail inspection, and one-click database reset to initial demo state. |

For full security self-assessment, risk matrix, and credential isolation policies, see [docs/SECURITY.md](file:///c:/Users/trann/OneDrive/Desktop/Fintechathon/docs/SECURITY.md).

---

## 📐 Mathematical Models & Core Algorithms

For the detailed mathematical formulations of:
1. Financial Health Score (4-Pillar Formulation)
2. Statistical Z-Score Anomaly Detector
3. 3-Plan Goal Generation & Optimization Logic
4. What-If Simulation Calculus

Please refer to the technical documentation in [docs/ALGORITHMS.md](file:///c:/Users/trann/OneDrive/Desktop/Fintechathon/docs/ALGORITHMS.md).

---

## 🧪 Running Automated Tests

Run the complete test suite with 100% pass rate:
```powershell
python -m pytest -v
```

### Test Coverage Summary:
- `tests/test_algorithms.py`: Validates Health Score calculation, Anomaly Z-Score detection, 3-tier Goal Planner feasibility, and What-If timeline impact.
- `tests/test_api.py`: Validates FastAPI REST endpoints, RBAC permission tiers (403 forbidden vs 200 ok), Sandbox approval workflow, and database resets.

---

## 📂 Project Structure

```
Fintechathon/
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI entrypoint, lifespan, CORS, and routing
│   ├── database.py           # SQLite connection & realistic VND demo data seeder
│   ├── schemas.py            # Pydantic request & response validation models
│   ├── security.py           # RBAC permission hierarchy & header verification
│   ├── services/
│   │   ├── health_score.py   # 0-100 Financial Health Score engine
│   │   ├── planner.py        # 3-Tier savings goal generator
│   │   ├── anomaly.py        # Moving average & Z-Score anomaly detector
│   │   ├── simulator.py      # What-If decision simulation engine
│   │   ├── tracker.py        # Milestone trajectory tracker
│   │   ├── sandbox.py        # Staged proposal and approval lifecycle
│   │   └── audit.py          # Immutable audit logging service
│   └── routers/
│       ├── dashboard.py      # Dashboard metrics & chart data
│       ├── transactions.py   # Transactions CRUD & anomaly scanning
│       ├── goals.py          # Goal management & plan calculators
│       ├── simulation.py     # What-If simulation endpoint
│       ├── sandbox.py        # Sandbox proposals & approvals
│       ├── audit.py          # Audit trail inspection
│       └── admin.py          # Database reset & system status
├── static/
│   ├── index.html            # Responsive single-page web app
│   ├── css/style.css         # Modern fintech dark theme & design system
│   └── js/
│       ├── app.js            # SPA controller & API client
│       └── charts.js         # Chart.js rendering helpers
├── docs/
│   ├── ALGORITHMS.md         # Mathematical models & formulas
│   └── SECURITY.md           # Security self-assessment & RBAC
├── tests/
│   ├── test_algorithms.py    # Unit tests for core algorithms
│   └── test_api.py           # API integration and security tests
├── schema.sql                # Complete SQLite database schema
├── sample_sandbox_logs.json  # Competition sample sandbox execution logs
├── requirements.txt          # Python dependencies
└── README.md                 # Project documentation & run guide
```
