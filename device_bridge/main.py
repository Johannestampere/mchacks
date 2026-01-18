import asyncio
import websockets
import platform
from LAM import execute_goal
from data_shapes import DeviceRegistration, StatusUpdate, PongMessage, LaptopTask

# Configuration
BACKEND_URL = "ws://localhost:8000/ws/device"
DEVICE_ID = "macbook-1"
RECONNECT_DELAY = 5

async def send_status(ws, status: str, message: str = "", screenshot: str = None):
    update = StatusUpdate(
        device_id=DEVICE_ID,
        status=status,
        message=message,
        screenshot=screenshot,
    )
    await ws.send(update.to_json())

# Handle incoming laptop_task
async def handle_task(ws, task: LaptopTask):
    print(f"[TASK] Received goal: {task.goal}")

    await send_status(ws, "started", f"Starting task: {task.goal}")

    try:
        # Progress callback to stream updates back to backend
        def on_step(step_num, action, screenshot_b64):
            asyncio.create_task(
                send_status(ws, "in_progress", f"Step {step_num}: {action.get('action', 'unknown')}", screenshot_b64)
            )

        # Run the LAM in a thread to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: execute_goal(task.goal, max_steps=20, on_step=on_step))

        if result.success:
            await send_status(ws, "completed", result.result)
        else:
            await send_status(ws, "failed", result.result)

    except Exception as e:
        await send_status(ws, "failed", f"Error: {str(e)}")

# Register device with backend
async def register_device(ws):
    registration = DeviceRegistration(
        device_id=DEVICE_ID,
        device_type="laptop",
        platform=platform.system(),
        capabilities=["mouse", "keyboard", "screenshot"],
    )
    await ws.send(registration.to_json())
    print(f"[REGISTER] Registered as device: {DEVICE_ID}")

async def listen(ws):
    import json
    async for message in ws:
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "laptop_task":
                task = LaptopTask.from_dict(data)
                await handle_task(ws, task)
            elif msg_type == "cancel":
                print("[CANCEL] Task cancellation requested")
            elif msg_type == "ping":
                pong = PongMessage(device_id=DEVICE_ID)
                await ws.send(pong.to_json())
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