import asyncio
import json
import websockets
import platform
import uuid

# Configuration
BACKEND_URL = "ws://localhost:8000/ws/device"
DEVICE_ID = "macbook-1"
RECONNECT_DELAY = 5

# 
async def send_status(ws, status: str, message: str = "", screenshot: str = None):
    payload = {
        "type": "status_update",
        "device_id": DEVICE_ID,
        "status": status,
        "message": message,
    }
    if screenshot:
        payload["screenshot"] = screenshot
    await ws.send(json.dumps(payload))

# Handle incoming laptop_task
async def handle_task(ws, task: dict):
    goal = task.get("goal", "")
    print(f"[TASK] Received goal: {goal}")

    await send_status(ws, "started", f"Starting task: {goal}")

    try:
        # TODO: Use interpreter to break goal into steps
        # TODO: Use controller to execute actions

        await send_status(ws, "in_progress", "Executing actions...")
        await asyncio.sleep(1)  # Placeholder
        await send_status(ws, "completed", f"Finished: {goal}")

    except Exception as e:
        await send_status(ws, "failed", f"Error: {str(e)}")

# Register device with backend
async def register_device(ws):
    await ws.send(json.dumps({
        "type": "device_register",
        "device_id": DEVICE_ID,
        "device_type": "laptop",
        "platform": platform.system(),
        "capabilities": ["mouse", "keyboard", "screenshot"],
    }))
    print(f"[REGISTER] Registered as device: {DEVICE_ID}")

async def listen(ws):
    async for message in ws:
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "laptop_task":
                await handle_task(ws, data)
            elif msg_type == "cancel":
                print("[CANCEL] Task cancellation requested")
            elif msg_type == "ping":
                await ws.send(json.dumps({"type": "pong", "device_id": DEVICE_ID}))
            else:
                print(f"[MSG] Unknown message type: {msg_type}")

        except json.JSONDecodeError:
            print(f"[ERROR] Invalid JSON received: {message}")

# Main connection loop with auto-reconnect
async def connect():
    while True:
        try:
            print(f"[CONNECT] Connecting to {BACKEND_URL}...")
            async with websockets.connect(BACKEND_URL) as ws:
                print("[CONNECT] Connected!")
                await register_device(ws)
                await listen(ws)

        except websockets.ConnectionClosed:
            print(f"[DISCONNECT] Connection closed. Reconnecting in {RECONNECT_DELAY}s...")
        except ConnectionRefusedError:
            print(f"[ERROR] Connection refused. Retrying in {RECONNECT_DELAY}s...")
        except Exception as e:
            print(f"[ERROR] {e}. Reconnecting in {RECONNECT_DELAY}s...")

        await asyncio.sleep(RECONNECT_DELAY)

def main():
    print("=" * 50)
    print(f"Device Bridge Starting - ID: {DEVICE_ID}")
    print("=" * 50)
    asyncio.run(connect())

if __name__ == "__main__":
    main()