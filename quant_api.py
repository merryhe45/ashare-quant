import json
import subprocess
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


APP_DIR = Path("/app")
CONFIG_PATH = "/app/config/ashare-lake.toml"
LATEST_RESULT = APP_DIR / "results" / "latest.json"

run_lock = threading.Lock()

app = FastAPI(
    title="A-Share Quant API",
    version="2.0",
)


class QuantRunRequest(BaseModel):
    scan_date: str | None = None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ashare-quant-v2",
    }


@app.post("/quant/run")
def run_quant(request: QuantRunRequest):
    if request.scan_date:
        try:
            date.fromisoformat(request.scan_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="scan_date must use YYYY-MM-DD format",
            )

    command = [
        "python",
        "/app/run_pipeline_v2.py",
        "--config",
        CONFIG_PATH,
    ]

    if request.scan_date:
        command.extend(
            [
                "--scan-date",
                request.scan_date,
            ]
        )

    with run_lock:
        try:
            result = subprocess.run(
                command,
                cwd=APP_DIR,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=504,
                detail="Quant pipeline timed out",
            )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Quant pipeline failed",
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                },
            )

        if not LATEST_RESULT.exists():
            raise HTTPException(
                status_code=500,
                detail="Quant pipeline completed but latest.json was not found",
            )

        try:
            with LATEST_RESULT.open(
                "r",
                encoding="utf-8",
            ) as f:
                payload = json.load(f)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Unable to read quant result: {exc}",
            )

    return payload
