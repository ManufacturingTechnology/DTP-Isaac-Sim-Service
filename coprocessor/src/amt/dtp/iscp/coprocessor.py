import sys
import asyncio
import signal
import argparse
import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

try:
    from . import __version__
except Exception as ex:
    __version__ = "0.0.0"

from .routers import routers

app = FastAPI(
    title="AMT Digital Testbed Platform (DTP) Isaac Sim Co-Processor (ISCP)",
    version=__version__,
#    description="A Simple Co-Processor",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
for router in routers:
    app.include_router(router, prefix="/api")

server: uvicorn.Server | None = None

@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, ex: Exception):
    print(f"Unhandled exception on {request.method} {request.url}, exception: {ex}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
        content={"detail": f"Internal server error: {ex}"}
    )

def handle_signal(signum, frame):
    print(f"Received signal {signum}, shutting down...")
    if server:
        server.should_exit = True

async def main(host: str = "0.0.0.0", port: int = 8013):
    global server
    config = uvicorn.Config(app, host=host, port=port)
    server = uvicorn.Server(config)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print("starting!")

    await server.serve()

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    asyncio.run(main(host=args.host, port=args.port))

if __name__ == "__main__":
    run()
