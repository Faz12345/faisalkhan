"""
Entry point — run from repo root:
    python run.py
or:
    uvicorn run:app --reload --port 8000
"""
import sys
import os

# Make app/backend the working directory so relative imports work
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "app", "backend")
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from server import app  # noqa: F401 — re-export for uvicorn

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[BACKEND_DIR],
    )
