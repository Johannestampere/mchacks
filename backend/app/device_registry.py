"""Registry of available devices that can be controlled."""

from backend.app.brain import Device

DEVICES: list[Device] = [
    Device(device_id="laptop-1", name="My Laptop", device_type="laptop"),
]