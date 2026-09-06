"""Entry point the Android service calls into.

The desktop app configures itself from environment variables and a `.env`
file; on Android there is neither a home directory in the usual sense nor a
browser to open, so this module sets the environment the backend expects
before `pitwall` is imported, then hands over to the ordinary `run()`.

Everything in `pitwall` is shared with the desktop build unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

_configured = False


def configure(files_dir: str, static_dir: str, microphone: bool = False) -> None:
    """Point the backend at the app's private storage. Call before start().

    `microphone` says whether RECORD_AUDIO is granted: the wake word and
    push-to-talk are enabled only then, so a refused permission never leaves
    the voice layer retrying against a microphone it cannot open.
    """
    global _configured
    files = Path(files_dir)
    data = files / "PitWallData"
    data.mkdir(parents=True, exist_ok=True)
    # Chaquopy does not set HOME; Path.home() would otherwise fail.
    os.environ.setdefault("HOME", str(files))
    os.environ["PITWALL_DATA_DIR"] = str(data)
    os.environ["PITWALL_STATIC_DIR"] = static_dir
    # The dashboard lives in the activity's WebView, not a browser tab.
    os.environ["PITWALL_OPEN_BROWSER"] = "false"
    os.environ["PITWALL_WEB_HOST"] = "127.0.0.1"
    # sounddevice.py and soundfile.py beside this file supply the audio layer
    # on top of AudioRecord and AudioTrack; the wake word only makes sense
    # with a microphone the app is allowed to open.
    os.environ["PITWALL_WAKE_ENABLED"] = "true" if microphone else "false"
    os.chdir(str(files))
    from pitwall.networking import register_interface_source

    register_interface_source(android_ipv4_interfaces)
    _configured = True


def _java_list(values) -> list:
    """A java.util.List (or Enumeration) as a Python list, by index."""
    from java import jclass

    if not hasattr(values, "size"):
        values = jclass("java.util.Collections").list(values)
    return [values.get(index) for index in range(values.size())]


def _default_route_address() -> str | None:
    """The source address the kernel would send from, without sending."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return str(probe.getsockname()[0])
    except OSError:
        return None


def android_ipv4_interfaces():
    """Every IPv4 address the phone has, with the interface it belongs to.

    The backend's own fallback learns one address from the default route,
    which on a phone sharing its connection as a hotspot is the mobile
    network, not the hotspot the console is on. Android lets an app list its
    interfaces through java.net.NetworkInterface (wlan0 for Wi-Fi, ap0 or
    swlan0 for the hotspot, rmnet_data* for mobile data), so CONNECTION can
    show every address and rank the one the game should send to.
    """
    from java import jclass

    from pitwall.networking import IPv4Interface, classify_adapter_kind

    NetworkInterface = jclass("java.net.NetworkInterface")
    default_route = _default_route_address()
    result = []
    for nic in _java_list(NetworkInterface.getNetworkInterfaces()):
        try:
            name = str(nic.getName())
            display = str(nic.getDisplayName() or name)
            is_up = bool(nic.isUp())
            loopback = bool(nic.isLoopback())
            bindings = _java_list(nic.getInterfaceAddresses())
        except Exception:  # noqa: BLE001 - an interface that vanished mid-listing
            continue
        for binding in bindings:
            try:
                address = str(binding.getAddress().getHostAddress())
                if ":" in address:
                    continue  # IPv6
                prefix = int(binding.getNetworkPrefixLength())
                result.append(
                    IPv4Interface(
                        adapter_id=f"android:{name}",
                        name=name,
                        description=display if display != name else "",
                        address=address,
                        prefix_length=prefix if 0 <= prefix <= 32 else 24,
                        is_up=is_up,
                        has_default_gateway=address == default_route,
                        kind=classify_adapter_kind("loopback" if loopback else name, display),
                    )
                )
            except Exception:  # noqa: BLE001 - skip one unusable binding, keep the rest
                continue
    return tuple(result)


def dashboard_url() -> str:
    from pitwall.config import settings
    from pitwall.main import local_dashboard_url

    return local_dashboard_url(settings.web_host, settings.web_port)


def start() -> None:
    """Run the server on the calling thread until it is asked to stop."""
    if not _configured:
        raise RuntimeError("configure() must be called before start()")
    from pitwall.main import run

    run()


def stop() -> None:
    """Ask the running server to exit; start() then returns."""
    from pitwall.app import app

    server = getattr(app.state, "server", None)
    if server is not None:
        server.should_exit = True
