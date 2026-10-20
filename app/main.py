import os
from contextlib import asynccontextmanager

try:
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY"):
        load_dotenv(".env.example")
except Exception:
    pass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers import (
    dashboard,
    transactions,
    goals,
    simulation,
    sandbox,
    audit,
    admin,
    profile,
    quant,
    budgets,
    recurring
)
from app.routers.copilot import router as copilot_router
from app.copilot.state import context_engine
from app.copilot.nl_engine import is_openrouter_configured, get_openrouter_model

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Warm-up copilot context from persisted DB events (non-blocking best-effort)
    try:
        loaded = context_engine.load_recent_from_db(n_per_lane=50)
        print(f"[Copilot] Context engine warmed up — {loaded} event(s) loaded from DB.")
    except Exception as exc:
        print(f"[Copilot] Context engine warm-up skipped: {exc}")

    if is_openrouter_configured():
        print(f"[Copilot] 🧠 Neural LLM ACTIVE via OpenRouter — Model: '{get_openrouter_model()}'")
    else:
        print("[Copilot] ⚙️ OpenRouter unconfigured — running in deterministic rule-based mode.")
    yield

app = FastAPI(
    title="AI Personal Financial Agent",
    description="Intelligent financial planning, anomaly detection, what-if simulation, and sandbox approval system.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware (allow_credentials=False)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(profile.router)
app.include_router(quant.router)
app.include_router(dashboard.router)
app.include_router(transactions.router)
app.include_router(goals.router)
app.include_router(budgets.router)
app.include_router(recurring.router)
app.include_router(simulation.router)
app.include_router(sandbox.router)
app.include_router(audit.router)
app.include_router(admin.router)
app.include_router(copilot_router)

# Mount static files
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
# Development static files with no-cache
class NoCacheStaticFiles(StaticFiles):
    async def __call__(self, scope, receive, send):
        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = dict(message.get("headers", []))
                # Add Cache-Control no-store header
                headers[b"cache-control"] = b"no-store"
                # Rebuild headers list
                message["headers"] = list(headers.items())
            await send(message)
        await super().__call__(scope, receive, send_wrapper)

app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index():
    """Serves the main single page dashboard application."""
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "AI Personal Financial Agent API is online."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
