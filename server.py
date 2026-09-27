import sys
import io
import os
import asyncio
import uvicorn

# Windows: use SelectorEventLoop to avoid ProactorEventLoop's socketpair()
# which exhausts kernel socket buffers under heavy connection load.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Force UTF-8 I/O so Chinese log messages don't break on Windows consoles
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.api.app import create_app   # noqa: E402 — import after env setup

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
