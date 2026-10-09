import os
from contextlib import asynccontextmanager
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
    quant
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
app.include_router(simulation.router)
app.include_router(sandbox.router)
app.include_router(audit.router)
app.include_router(admin.router)

# Mount static files
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
