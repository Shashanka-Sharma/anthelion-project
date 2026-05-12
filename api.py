import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent import DEFAULT_TASK, run_agent

app = FastAPI(title="Anthelion Agent API")


class AnalyzeRequest(BaseModel):
    task: str = DEFAULT_TASK


class AnalyzeResponse(BaseModel):
    task: str
    response: str
    elapsed_seconds: float


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    if not req.task.strip():
        raise HTTPException(status_code=400, detail="task must not be empty")
    t0 = time.time()
    response = await run_agent(req.task)
    return AnalyzeResponse(
        task=req.task,
        response=response,
        elapsed_seconds=round(time.time() - t0, 2),
    )
