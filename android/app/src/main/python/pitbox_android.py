"""Entry point the Android service calls into.

The desktop app configures itself from environment variables and a `.env`
file; on Android there is neither a home directory in the usual sense nor a
browser to open, so this module sets the environment the backend expects
before `pitwall` is imported, then hands over to the ordinary `run()`.

Everything in `pitwall` is shared with the desktop build unchanged.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
import threading
from pathlib import Path

_configured = False
_stop_requested = threading.Event()
_run_finished = threading.Event()


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

    if values is None:
        return []
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

    from pitwall.networking import AdapterKind, IPv4Interface, classify_adapter_kind

    default_route = _default_route_address()
    result = {}
    # LinkProperties survives Android restrictions on Java interface enumeration
    # and identifies Wi-Fi even when OEM interface names are unfamiliar.
    for network in android_network_status().get("networks", []):
        if not isinstance(network, dict):
            continue
        name = str(network.get("interface_name") or "")
        if not name:
            continue
        kind_name = str(network.get("transport", "unknown"))
        try:
            kind = AdapterKind(kind_name)
        except ValueError:
            kind = AdapterKind.UNKNOWN
        for binding in network.get("addresses", []):
            try:
                address = str(ipaddress.IPv4Address(binding["address"]))
                prefix = int(binding["prefix_length"])
                if not 0 <= prefix <= 32:
                    continue
                result[(name, address)] = IPv4Interface(
                    adapter_id=f"android:{name}",
                    name=name,
                    address=address,
                    prefix_length=prefix,
                    is_up=True,
                    has_default_gateway=bool(network.get("is_default")),
                    kind=kind,
                )
            except (KeyError, TypeError, ValueError):
                continue
    # Hotspot/tether interfaces are not upstream Networks; keep enumerating
    # them. Failure of this supplementary source must not hide LinkProperties.
    try:
        NetworkInterface = jclass("java.net.NetworkInterface")
        interfaces = _java_list(NetworkInterface.getNetworkInterfaces())
    except Exception:  # noqa: BLE001 - restricted or unavailable on this device
        interfaces = []
    for nic in interfaces:
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
                result.setdefault(
                    (name, address),
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
    return tuple(result.values())


def android_network_status() -> dict:
    """Local diagnostic snapshot; no SSIDs, MACs, credentials or route changes."""
    try:
        from java import jclass

        service = jclass("com.yourpitbox.app.PitBoxService")
        status = json.loads(str(service.getNetworkStatusJson()))
        if isinstance(status, dict):
            return status
    except Exception:  # noqa: BLE001 - diagnostics must not disable telemetry
        pass
    return {"platform": "android", "available": False, "networks": [], "warnings": []}


def bind_lan_socket(sock, peer_ip: str) -> bool:
    """Route a new transfer socket over its peer's Wi-Fi/Ethernet network.

    Call before connect/TLS. The native method duplicates this file descriptor
    while marking its underlying socket; ownership stays with the Python
    caller. False means ordinary routing is needed, including a phone hotspot
    which is not represented by an Android upstream Network object.
    """
    peer = ipaddress.IPv4Address(peer_ip)
    if peer.is_loopback:
        return False
    if peer.is_unspecified or peer.is_multicast or int(peer) == 0xFFFFFFFF:
        raise ValueError("A local device IPv4 address is required")
    try:
        from java import jclass

        service = jclass("com.yourpitbox.app.PitBoxService")
        return bool(service.bindLocalSocket(sock.fileno(), str(peer)))
    except Exception as error:
        raise OSError(
            "Could not route this transfer over the local network. Reconnect "
            "both devices to the same Wi-Fi, check their addresses, and try again."
        ) from error


def dashboard_url() -> str:
    # Import applies saved settings, including the actual web port. Reading
    # config before this left the Android waiting screen probing port 8000
    # forever after a user saved a different dashboard port.
    import pitwall.app  # noqa: F401

    from pitwall.config import settings
    from pitwall.main import local_dashboard_url

    return local_dashboard_url(settings.web_host, settings.web_port)


def start() -> None:
    """Run the server on the calling thread until it is asked to stop."""
    if not _configured:
        raise RuntimeError("configure() must be called before start()")
    if _stop_requested.is_set():
        return
    from pitwall.main import run

    # A Stop tap during cold imports must not import pitwall.app prematurely,
    # or be lost before main.run publishes its server. Watch only while this
    # invocation is alive and forward the request as soon as the server exists.
    def watch_stop() -> None:
        while not _run_finished.wait(0.05):
            if _stop_requested.is_set() and _stop_server_if_ready():
                return

    threading.Thread(target=watch_stop, name="pitbox-stop", daemon=True).start()
    try:
        run()
    finally:
        _run_finished.set()


def _stop_server_if_ready() -> bool:
    module = sys.modules.get("pitwall.app")
    app = getattr(module, "app", None)
    server = getattr(getattr(app, "state", None), "server", None)
    if server is None:
        return False
    server.should_exit = True
    return True


def stop() -> None:
    """Ask the running server to exit; start() then returns."""
    _stop_requested.set()
    _stop_server_if_ready()
