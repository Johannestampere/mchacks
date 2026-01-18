#!/usr/bin/env python3
"""
HTTP server for the LAM device bridge.
Receives tasks from the backend and executes them using the LAM.

Run: python server.py
Listens on: http://localhost:8001
"""

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn

from LAM import execute_goal
from data_shapes import GoalResult

app = FastAPI(title="LAM Device Bridge")

# Allow CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TaskRequest(BaseModel):
    goal: str
    type: str = "laptop_task"
    task_id: Optional[str] = None

class TaskResponse(BaseModel):
    success: bool
    result: str
    steps: int

# Track running tasks
current_task: Optional[str] = None

def run_task(goal: str):
    global current_task
    current_task = goal
    print(f"[LAM] Starting task: {goal}")

    def on_step(step_num, action, screenshot_b64):
        print(f"[LAM] Step {step_num}: {action}")

    try:
        result = execute_goal(goal, max_steps=20, on_step=on_step)
        print(f"[LAM] Task completed: success={result.success}, result={result.result}")
    except Exception as e:
        print(f"[LAM] Task failed with error: {e}")
    finally:
        current_task = None

@app.post("/task")
async def create_task(request: TaskRequest, background_tasks: BackgroundTasks):
    print(f"[SERVER] Received task: {request.goal}")

    if current_task:
        return {"status": "busy", "message": f"Already running task: {current_task}"}

    # Run the task in background
    background_tasks.add_task(run_task, request.goal)

    return {"status": "started", "goal": request.goal}

@app.post("/task/sync")
async def create_task_sync(request: TaskRequest) -> TaskResponse:
    """
    Execute a task synchronously and return the result.
    Use this for testing or when you need the result immediately.
    """
    print(f"[SERVER] Received sync task: {request.goal}")

    def on_step(step_num, action, screenshot_b64):
        print(f"[LAM] Step {step_num}: {action}")

    result = execute_goal(request.goal, max_steps=20, on_step=on_step)

    return TaskResponse(
        success=result.success,
        result=result.result,
        steps=result.steps
    )

@app.get("/health")
def health():
    return {"status": "ok", "current_task": current_task}

@app.get("/")
def root():
    return {
        "service": "LAM Device Bridge",
        "endpoints": {
            "POST /task": "Submit a task (async)",
            "POST /task/sync": "Submit a task (sync, waits for result)",
            "GET /health": "Health check",
        }
    }

if __name__ == "__main__":
    print("=" * 50)
    print("LAM Device Bridge Server")
    print("Listening on http://localhost:8001")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8001)