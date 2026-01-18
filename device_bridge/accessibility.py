"""
macOS Accessibility API module for extracting UI elements.
Uses pyobjc for direct access to the Accessibility API.
Requires accessibility permissions in System Preferences > Security & Privacy > Accessibility.
"""

import subprocess
from dataclasses import dataclass
from typing import Optional

# Try to import pyobjc modules
try:
    from ApplicationServices import (
        AXUIElementCreateSystemWide,
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        kAXFocusedApplicationAttribute,
        kAXWindowsAttribute,
        kAXChildrenAttribute,
        kAXRoleAttribute,
        kAXTitleAttribute,
        kAXDescriptionAttribute,
        kAXValueAttribute,
        kAXPositionAttribute,
        kAXSizeAttribute,
        kAXEnabledAttribute,
        kAXFocusedAttribute,
    )
    from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    import Quartz
    PYOBJC_AVAILABLE = True
except ImportError:
    PYOBJC_AVAILABLE = False
    print("[ACCESSIBILITY] pyobjc not available. Install with: pip install pyobjc-framework-ApplicationServices pyobjc-framework-Quartz")


@dataclass
class UIElement:
    """Represents a UI element extracted from the accessibility tree."""
    index: int
    role: str
    title: Optional[str]
    description: Optional[str]
    value: Optional[str]
    x: int
    y: int
    width: int
    height: int
    enabled: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "role": self.role.replace("AX", ""),
            "title": self.title,
            "description": self.description,
            "value": self.value[:50] if self.value and len(self.value) > 50 else self.value,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "enabled": self.enabled,
        }

    def summary(self) -> str:
        """Short summary for the model."""
        label = self.title or self.description or self.value or "(no label)"
        if len(label) > 40:
            label = label[:37] + "..."
        role = self.role.replace("AX", "")
        return f"[{self.index}] {role}: {label}"


def _get_ax_attribute(element, attribute):
    """Get an accessibility attribute from an element."""
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
        if err == 0:
            return value
    except Exception:
        pass
    return None


def _get_frontmost_app_pid() -> Optional[int]:
    """Get the PID of the frontmost application."""
    try:
        # Use NSWorkspace to get frontmost app
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app:
            return app.processIdentifier()
    except Exception:
        pass

    # Fallback: use AppleScript
    try:
        result = subprocess.run(
            ['osascript', '-e', 'tell application "System Events" to unix id of (first application process whose frontmost is true)'],
            capture_output=True,
            text=True,
            timeout=2.0
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return None


def _get_frontmost_app_name() -> str:
    """Get the name of the frontmost application."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app:
            return app.localizedName()
    except Exception:
        pass
    return ""


def _extract_elements_recursive(element, elements: list, index_counter: list, depth: int = 0, max_depth: int = 5, max_elements: int = 100):
    """Recursively extract UI elements from the accessibility tree."""
    if depth > max_depth or len(elements) >= max_elements:
        return

    # Get children
    children = _get_ax_attribute(element, kAXChildrenAttribute)
    if not children:
        return

    for child in children:
        if len(elements) >= max_elements:
            break

        try:
            role = _get_ax_attribute(child, kAXRoleAttribute) or ""
            title = _get_ax_attribute(child, kAXTitleAttribute) or ""
            description = _get_ax_attribute(child, kAXDescriptionAttribute) or ""
            value = _get_ax_attribute(child, kAXValueAttribute)
            enabled = _get_ax_attribute(child, kAXEnabledAttribute)

            # Convert value to string if possible
            if value is not None:
                try:
                    value = str(value)[:100]
                except:
                    value = ""
            else:
                value = ""

            # Get position and size
            pos = _get_ax_attribute(child, kAXPositionAttribute)
            size = _get_ax_attribute(child, kAXSizeAttribute)

            x, y, width, height = 0, 0, 0, 0
            if pos:
                try:
                    x = int(pos.x)
                    y = int(pos.y)
                except:
                    pass
            if size:
                try:
                    width = int(size.width)
                    height = int(size.height)
                except:
                    pass

            # Include element if it has useful info
            has_label = bool(title or description)
            is_interactive = role in [
                "AXButton", "AXTextField", "AXTextArea", "AXLink",
                "AXCheckBox", "AXRadioButton", "AXPopUpButton",
                "AXComboBox", "AXSlider", "AXMenuItem", "AXMenuButton",
                "AXTab", "AXCell", "AXRow"
            ]
            has_size = width > 10 and height > 10

            if has_label and has_size:
                elements.append(UIElement(
                    index=index_counter[0],
                    role=role,
                    title=title if title else None,
                    description=description if description else None,
                    value=value if value else None,
                    x=x + width // 2,  # Center
                    y=y + height // 2,
                    width=width,
                    height=height,
                    enabled=enabled if enabled is not None else True,
                ))
                index_counter[0] += 1

            # Recurse into children
            _extract_elements_recursive(child, elements, index_counter, depth + 1, max_depth, max_elements)

        except Exception as e:
            continue


def get_ui_elements() -> tuple[str, list[UIElement]]:
    """
    Extract UI elements from the frontmost application using macOS Accessibility API.

    Returns:
        Tuple of (app_name, list of UIElement)
    """
    if not PYOBJC_AVAILABLE:
        return "", []

    try:
        app_name = _get_frontmost_app_name()
        pid = _get_frontmost_app_pid()

        if not pid:
            print("[ACCESSIBILITY] Could not get frontmost app PID")
            return app_name, []

        # Create accessibility element for the app
        app_element = AXUIElementCreateApplication(pid)

        # Get windows
        windows = _get_ax_attribute(app_element, kAXWindowsAttribute)
        if not windows:
            print("[ACCESSIBILITY] No windows found")
            return app_name, []

        elements = []
        index_counter = [0]

        # Process each window
        for window in windows[:3]:  # Limit to first 3 windows
            _extract_elements_recursive(window, elements, index_counter, max_depth=5, max_elements=100)

        return app_name, elements

    except Exception as e:
        print(f"[ACCESSIBILITY] Error: {e}")
        return "", []


def format_elements_for_model(elements: list[UIElement], max_elements: int = 50) -> str:
    """Format UI elements into a string for the model prompt."""
    if not elements:
        return "No UI elements detected."

    # Prioritize enabled, interactive elements
    prioritized = sorted(
        elements,
        key=lambda e: (
            not e.enabled,
            e.role not in ["AXButton", "AXLink", "AXTextField", "AXTextArea"],
            e.y,
            e.x,
        )
    )[:max_elements]

    lines = ["Available UI elements (use index to click):"]
    for elem in prioritized:
        lines.append(elem.summary())

    return "\n".join(lines)


def get_element_by_index(elements: list[UIElement], index: int) -> Optional[UIElement]:
    """Get a UI element by its index."""
    for elem in elements:
        if elem.index == index:
            return elem
    return None


def check_accessibility_permission() -> bool:
    """Check if we have accessibility permissions."""
    if not PYOBJC_AVAILABLE:
        return False
    try:
        from ApplicationServices import AXIsProcessTrusted
        return AXIsProcessTrusted()
    except Exception:
        return False


# Quick test
if __name__ == "__main__":
    print("Checking pyobjc availability...")
    if not PYOBJC_AVAILABLE:
        print("Install pyobjc: pip install pyobjc-framework-ApplicationServices pyobjc-framework-Quartz pyobjc-framework-Cocoa")
        exit(1)

    print("Checking accessibility permissions...")
    if not check_accessibility_permission():
        print("\n*** ACCESSIBILITY PERMISSION REQUIRED ***")
        print("Go to: System Settings > Privacy & Security > Accessibility")
        print("Add Terminal (or your Python interpreter) to the list.")
        print("Then restart Terminal and try again.\n")
    else:
        print("Accessibility permissions: OK")

    print("\nGetting frontmost app info...")
    pid = _get_frontmost_app_pid()
    app_name = _get_frontmost_app_name()
    print(f"App: {app_name}, PID: {pid}")

    if pid:
        print("\nCreating app element...")
        app_element = AXUIElementCreateApplication(pid)
        print(f"App element: {app_element}")

        print("\nGetting windows...")
        windows = _get_ax_attribute(app_element, kAXWindowsAttribute)
        print(f"Windows: {windows}, count: {len(windows) if windows else 0}")

        if windows:
            print("\nExploring first window...")
            window = windows[0]

            # Get window title
            title = _get_ax_attribute(window, kAXTitleAttribute)
            print(f"Window title: {title}")

            # Get direct children
            children = _get_ax_attribute(window, kAXChildrenAttribute)
            print(f"Direct children count: {len(children) if children else 0}")

            if children:
                print("\nFirst 10 direct children:")
                for i, child in enumerate(children[:10]):
                    role = _get_ax_attribute(child, kAXRoleAttribute)
                    child_title = _get_ax_attribute(child, kAXTitleAttribute)
                    desc = _get_ax_attribute(child, kAXDescriptionAttribute)
                    print(f"  [{i}] {role}: title='{child_title}', desc='{desc}'")

                    # Check grandchildren
                    grandchildren = _get_ax_attribute(child, kAXChildrenAttribute)
                    if grandchildren:
                        print(f"       -> {len(grandchildren)} children")

    print("\n--- Full extraction ---")
    app_name, elements = get_ui_elements()
    print(f"\nApp: {app_name}")
    print(f"Found {len(elements)} elements:\n")

    if elements:
        print(format_elements_for_model(elements))
    else:
        print("No UI elements detected.")
