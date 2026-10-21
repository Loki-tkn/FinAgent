# AI Personal Financial Agent

AI-powered personal finance dashboard and planning assistant for budgeting, saving goals, anomaly detection, and what-if financial simulations.

## Overview
This project is a lightweight fintech-style web application designed to help users:

- track income and spending
- review financial health using a scoring model
- set and monitor savings goals
- detect unusual spending patterns
- simulate financial scenarios before making decisions
- use an AI-assisted finance copilot grounded in processed app data

> Disclaimer: This project uses simulated demo financial data for educational and competition purposes. It is not financial advice.

---

## Features

- Financial dashboard with income, expense, surplus, and trend analysis
- Financial Health Score based on savings, spending, and emergency runway
- Goal planner with multiple strategy modes
- Spending anomaly detection using statistical analysis
- What-if simulation for purchase, salary, and budget change scenarios
- Sandboxed financial actions and approval flow
- Audit log tracking
- AI copilot/chat assistant (optional, only when OpenRouter key is configured)

---

## Tech stack

- Backend: Python, FastAPI
- Database: SQLite
- Frontend: HTML, CSS, JavaScript
- Validation: Pydantic
- Visualization: Chart.js
- Testing: pytest

---

## Prerequisites

- Python 3.10+
- pip
- Git
- A terminal such as PowerShell, Command Prompt, or bash

---

## Quick start

### 1. Clone the project

```bash
git clone <https://github.com/trumbuffban/FinAgent_clone>
cd FinAgent_clone
```

### 2. Create and activate a virtual environment

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables (optional but recommended)

A sample environment file is included:

- `.env.example`

Copy it to `.env` if you want to enable the AI copilot with OpenRouter:

```bash
copy .env.example .env
```

Then edit `.env` and set your OpenRouter API key if needed:

```env
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=google/gemini-2.0-flash-001
```

If you leave the API key empty, the app still runs in deterministic local mode without external AI calls.

### 5. Run the app

From the project root:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Or:

```bash
python -m app.main
```

### 6. Open the app

Go to:

```text
http://127.0.0.1:8000
```

---

## AI copilot setup

This project includes an AI finance assistant with optional OpenRouter integration.

### If you want AI chat enabled

1. Create `.env` from `.env.example`
2. Add your `OPENROUTER_API_KEY`
3. Restart the application

### If you do not want AI chat enabled

- Leave `OPENROUTER_API_KEY` blank
- Or remove the line completely from `.env`
- Or comment it out:

```env
# OPENROUTER_API_KEY=your_key_here
```

Then restart the server:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

When the key is empty, the app automatically runs in deterministic local mode without external AI calls.

### Disable API usage completely

If you want to fully disable external AI access:

1. Open `.env`
2. Change:

```env
OPENROUTER_API_KEY=your_key_here
```

to:

```env
OPENROUTER_API_KEY=
```

3. Save the file
4. Restart the app

This is the recommended option for local demo or offline testing.

---

## Database behavior

The project uses SQLite and creates/uses a local database file named `finances.db` automatically when the app starts.

If the DB does not exist yet, the app will initialize it on startup.

---

## Testing

Run tests with:

```bash
pytest -q
```

Or for full verbose output:

```bash
pytest -v
```

---

## Project structure

```text
FinAgent_clone/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   ├── schemas.py
│   ├── security.py
│   ├── copilot/
│   │   ├── __init__.py
│   │   ├── bus.py
│   │   ├── guardrails.py
│   │   ├── hooks.py
│   │   ├── nl_engine.py
│   │   ├── schemas.py
│   │   └── state.py
│   ├── routers/
│   │   ├── admin.py
│   │   ├── audit.py
│   │   ├── budgets.py
│   │   ├── copilot.py
│   │   ├── dashboard.py
│   │   ├── goals.py
│   │   ├── profile.py
│   │   ├── quant.py
│   │   ├── recurring.py
│   │   ├── sandbox.py
│   │   ├── simulation.py
│   │   └── transactions.py
│   ├── services/
│   │   ├── anomaly.py
│   │   ├── assumptions.py
│   │   ├── audit.py
│   │   ├── budget_check.py
│   │   ├── goal_prob.py
│   │   ├── health_score.py
│   │   ├── metrics.py
│   │   ├── planner.py
│   │   ├── quant.py
│   │   ├── recurring.py
│   │   ├── sandbox.py
│   │   ├── simulator.py
│   │   └── tracker.py
│   └── templates/ (if present)
├── static/
│   ├── css/
│   ├── js/
│   └── index.html
├── docs/
│   ├── ALGORITHMS.md
│   └── SECURITY.md
├── .env
├── .env.example
├── finances.db
├── requirements.txt
├── schema.sql
├── sample_sandbox_logs.json
├── README.md
├── tests/
│   ├── test_algorithms.py
│   ├── test_api.py
│   ├── test_health.py
│   ├── test_planner.py
│   └── test_quant.py
└── .git/
```

---

## Notes

- This project is built for local demo and evaluation.
- The app is meant to be educational and simulated, not an investment recommendation engine.
- The AI assistant can operate in local deterministic mode or with OpenRouter when configured.

---

## Troubleshooting

### Port already in use

If port 8000 is occupied, run:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

### Module not found

Make sure your virtual environment is active and dependencies are installed:

```bash
pip install -r requirements.txt
```

### AI chat is slow or unavailable

- Check whether `OPENROUTER_API_KEY` is set in `.env`
- Confirm the internet connection is available
- If the key is missing, the app will run without external AI

---

## License

This project is provided as-is for educational and hackathon/demo use.

