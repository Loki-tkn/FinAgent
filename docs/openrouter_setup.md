# OpenRouter LLM Setup Guide for FinAgent Copilot

This guide explains how to enable and configure external LLM reasoning in the FinAgent Copilot using **[OpenRouter](https://openrouter.ai/)**.

---

## 1. Overview & Architecture

FinAgent Copilot operates with a **hybrid AI architecture**:
- **Default / Offline Mode**: When `OPENROUTER_API_KEY` is not set, the system uses a **100% deterministic, rule-based engine** with zero external API dependencies.
- **OpenRouter LLM Mode**: When `OPENROUTER_API_KEY` is configured, queries sent to `POST /api/copilot/chat` or the WebSocket stream dynamically pull live multi-lane context snapshots (`agent_cot`, `risk_metric`, `exec_log`, `market_intel`, budget stats, and health score) from `CopilotContextEngine`, inject them into the system prompt, and query your selected LLM on OpenRouter.
- **Automatic Fallback**: If the OpenRouter API encounters a rate limit, network timeout, or invalid key, FinAgent **automatically and seamlessly falls back** to the local rule-based classifier without throwing a 500 error or disrupting user operations.
- **Guardrails & Disclaimers**: All LLM outputs strictly pass through the compliance sanitizers in `app/copilot/guardrails.py`, removing dangerous absolutist promises and appending regulatory financial disclaimers.

---

## 2. Obtaining an OpenRouter API Key

1. Navigate to **[https://openrouter.ai/](https://openrouter.ai/)** and sign up or log in.
2. Go to **[Keys / API Keys](https://openrouter.ai/keys)**.
3. Click **Create Key**.
4. Give your key a name (e.g. `FinAgent Copilot`) and configure any optional credit limits.
5. Copy your newly created API key (starts with `sk-or-v1-...`).

---

## 3. Environment Configuration

Copy the provided [`.env.example`](file:///Users/phamgiahung/FinAgent/.env.example) to `.env`:

```bash
cp .env.example .env
```

Add your OpenRouter credentials into `.env`:

```bash
# ------------------------------------------------------------------------------
# OpenRouter LLM Configuration
# ------------------------------------------------------------------------------
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet

# Optional settings (defaults shown):
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_REFERER=https://github.com/FinAgent
OPENROUTER_TITLE=FinAgent Copilot
```

---

## 4. Recommended Models for FinAgent

OpenRouter gives you unified access to hundreds of models. For financial reasoning, anomaly auditing, and Monte Carlo interpretation, we recommend:

| Model Identifier | Provider | Strengths & Use Cases | Pricing / Speed |
|---|---|---|---|
| `anthropic/claude-3.5-sonnet` | Anthropic | **Top Recommendation**: Exceptional financial reasoning, structured table formatting, and nuanced chain-of-thought analysis. | Moderate cost, high quality |
| `google/gemini-2.0-flash-001` | Google | **Best for Low Latency**: Ultra-fast responses, ideal for real-time WebSocket streaming and interactive queries. | Low cost, ultra-fast |
| `deepseek/deepseek-r1` | DeepSeek | **Mathematical & Quantitative**: Exceptional mathematical step-by-step reasoning for Cash-Flow-at-Risk and Monte Carlo distributions. | Low cost, high reasoning |
| `meta-llama/llama-3.3-70b-instruct` | Meta | **Open-Weights Powerhouse**: Strong general intelligence, reliable adherence to financial formatting instructions. | Very affordable |

To change your active model, simply set `OPENROUTER_MODEL=<model_id>` in your `.env` or system environment without modifying any Python code.

---

## 5. Testing & Verifying LLM Responses

### A. Via cURL / HTTP
Send a natural-language query to `/api/copilot/chat`:

```bash
curl -X POST http://127.0.0.1:8000/api/copilot/chat \
  -H "Content-Type: application/json" \
  -H "X-Role: User" \
  -d '{"message": "Why was my spending flagged as an anomaly?"}'
```

**Expected Response with OpenRouter Active:**
```json
{
  "reply": "Based on the recent anomaly scan in your transactions, your dining expense of $145.00 exceeded the category median of $35.00 by 4.1x (Robust Z-Score: +4.62). The agent flagged this as a HIGH severity spike to protect your monthly savings rate target.\n\n---\n*Disclaimer: FinAgent Copilot provides educational analytics and simulations only. It does not constitute certified investment or financial advice. Market forecasts and model trajectories are hypothetical and not guaranteed.*",
  "intent": "openrouter_llm",
  "confidence": 0.95,
  "sources": [
    {
      "event_id": 42,
      "lane": "agent_cot",
      "timestamp": "2026-10-16T15:20:00Z",
      "summary": "{'step': 'spending_anomaly_scan', 'anomalies_detected': 1}"
    }
  ],
  "session_id": "default",
  "timestamp": "2026-10-16T15:25:00Z",
  "disclaimer": "DISCLAIMER: FinAgent Copilot is an AI decision-support and educational tool..."
}
```

### B. Via Python Interactive Test
Run the following script to verify live context injection:

```python
import os
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
response = client.post(
    "/api/copilot/chat",
    json={"message": "Can I afford to increase my savings goal by $200 per month?"},
    headers={"X-Role": "User"}
)
print("Intent:", response.json()["intent"])
print("Reply:\n", response.json()["reply"])
```

### C. Via Real-Time WebSocket (`/api/copilot/ws`)
Connect via WebSocket client (e.g. Postman, browser, or wscat):

```bash
# Connect
wscat -c "ws://127.0.0.1:8000/api/copilot/ws"

# Send query message
> {"action": "chat", "message": "Give me an overview of my current risk metrics and CFaR."}

# Receive real-time LLM reply with disclaimer
< {"type": "chat_reply", "data": {...}}
```

---

## 6. Verifying Fallback Behavior

You can verify that fallback to the offline rule-based engine works properly at any time by temporarily unsetting `OPENROUTER_API_KEY`:

```bash
unset OPENROUTER_API_KEY
python -m pytest tests/test_copilot_openrouter.py -v
```

All tests will pass, proving that FinAgent Copilot maintains uninterrupted 24/7 availability regardless of external API connectivity.
