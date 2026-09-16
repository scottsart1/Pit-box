"""The Android interface listing, driven against a fake java.net.NetworkInterface.

On a phone the backend's stdlib fallback learns one address from the default
route, which is the mobile network when the phone is a hotspot. The Android
entry module lists every interface through java.net.NetworkInterface and
registers that listing with the backend; this drives it with fakes.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import threading
import types
from pathlib import Path

import pytest

ANDROID_PYTHON = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "python"


class _JavaList:
    def __init__(self, items):
        self._items = list(items)

    def size(self):
        return len(self._items)

    def get(self, index):
        return self._items[index]


class _Enumeration:
    """java.util.Enumeration: has no size(), must go through Collections.list."""

    def __init__(self, items):
        self.items = list(items)


class _Inet:
    def __init__(self, host):
        self._host = host

    def getHostAddress(self):
        return self._host


class _Binding:
    def __init__(self, host, prefix):
        self._inet = _Inet(host)
        self._prefix = prefix

    def getAddress(self):
        return self._inet

    def getNetworkPrefixLength(self):
        return self._prefix


class _Nic:
    def __init__(self, name, bindings, *, up=True, loopback=False, display=None):
        self._name = name
        self._bindings = bindings
        self._up = up
        self._loopback = loopback
        self._display = display or name

    def getName(self):
        return self._name

    def getDisplayName(self):
        return self._display

    def isUp(self):
        return self._up

    def isLoopback(self):
        return self._loopback

    def getInterfaceAddresses(self):
        return _JavaList(self._bindings)


class _Collections:
    @staticmethod
    def list(enumeration):
        return _JavaList(enumeration.items)


@pytest.fixture
def android_entry(monkeypatch, tmp_path):
    # configure() changes the embedded process environment; isolate this fake
    # Android process from desktop tests collected in the same pytest run.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    nics: list[_Nic] = []
    fake_java = types.ModuleType("java")
    classes = {
        "java.net.NetworkInterface": types.SimpleNamespace(
            getNetworkInterfaces=lambda: _Enumeration(nics)
        ),
        "java.util.Collections": _Collections,
    }
    fake_java.jclass = lambda name: classes[name]
    monkeypatch.setitem(sys.modules, "java", fake_java)
    monkeypatch.syspath_prepend(str(ANDROID_PYTHON))
    monkeypatch.delitem(sys.modules, "pitbox_android", raising=False)
    module = importlib.import_module("pitbox_android")
    assert module.__file__.startswith(str(ANDROID_PYTHON))
    monkeypatch.setattr(module, "_default_route_address", lambda: "10.20.30.40")
    yield module, nics
    from pitwall import networking

    networking.unregister_interface_source(module.android_ipv4_interfaces)


def test_every_ipv4_binding_is_listed_with_its_interface(android_entry):
    module, nics = android_entry
    nics.extend(
        [
            _Nic("lo", [_Binding("127.0.0.1", 8)], loopback=True),
            _Nic("wlan0", [_Binding("fe80::1%wlan0", 64), _Binding("192.168.12.204", 24)]),
            _Nic("swlan0", [_Binding("192.168.43.1", 24)]),
            _Nic("rmnet_data0", [_Binding("10.20.30.40", 29)]),
            _Nic("dummy0", [], up=False),
        ]
    )
    from pitwall.networking import AdapterKind

    listed = {item.address: item for item in module.android_ipv4_interfaces()}
    assert set(listed) == {"127.0.0.1", "192.168.12.204", "192.168.43.1", "10.20.30.40"}
    assert listed["127.0.0.1"].kind is AdapterKind.LOOPBACK
    assert listed["192.168.12.204"].kind is AdapterKind.WIFI
    assert listed["192.168.12.204"].adapter_id == "android:wlan0"
    assert listed["192.168.12.204"].prefix_length == 24
    assert listed["192.168.43.1"].kind is AdapterKind.WIFI, "the hotspot is where the console sends"
    assert listed["10.20.30.40"].kind is AdapterKind.CELLULAR
    assert listed["10.20.30.40"].has_default_gateway is True
    assert listed["192.168.12.204"].has_default_gateway is False


def test_the_hotspot_outranks_mobile_data_even_when_mobile_data_is_the_default_route(android_entry):
    module, nics = android_entry
    nics.extend(
        [
            _Nic("swlan0", [_Binding("192.168.43.1", 24)]),
            _Nic("rmnet_data0", [_Binding("10.20.30.40", 29)]),
        ]
    )
    from pitwall.networking import recommend_ipv4_interface

    ranked = recommend_ipv4_interface(module.android_ipv4_interfaces()).ranked
    assert ranked[0].interface.address == "192.168.43.1"


def test_configure_registers_the_listing_with_the_backend_fallback(android_entry, tmp_path, monkeypatch):
    module, nics = android_entry
    nics.append(_Nic("wlan0", [_Binding("192.168.12.204", 24)]))
    monkeypatch.chdir(tmp_path)
    module.configure(str(tmp_path), str(tmp_path / "static"))
    from pitwall import networking

    listed = {item.address: item for item in networking.fallback_ipv4_interfaces()}
    assert listed["192.168.12.204"].name == "wlan0"
    assert listed["192.168.12.204"].adapter_id == "android:wlan0"


def test_a_vanishing_interface_does_not_stop_the_listing(android_entry):
    module, nics = android_entry

    class _Gone(_Nic):
        def getInterfaceAddresses(self):
            raise RuntimeError("java.net.SocketException: interface removed")

    nics.extend([_Gone("wlan1", []), _Nic("wlan0", [_Binding("192.168.1.5", 24)])])
    assert [item.address for item in module.android_ipv4_interfaces()] == ["192.168.1.5"]


def _native_network(name="vendor_radio0", address="192.168.12.5", transport="wifi", **extra):
    return {
        "interface_name": name,
        "transport": transport,
        "is_default": False,
        "internet_validated": False,
        "addresses": [{"address": address, "prefix_length": 24}],
        **extra,
    }


def test_link_properties_finds_local_wifi_without_internet_or_known_interface_name(android_entry, monkeypatch):
    module, nics = android_entry
    nics.append(_Nic("rmnet_data0", [_Binding("10.20.30.40", 29)]))
    monkeypatch.setattr(module, "android_network_status", lambda: {
        "networks": [_native_network()],
    })
    from pitwall.networking import AdapterKind, recommend_ipv4_interface

    result = module.android_ipv4_interfaces()
    wifi = next(item for item in result if item.address == "192.168.12.5")
    assert wifi.kind is AdapterKind.WIFI
    assert wifi.has_default_gateway is False
    assert recommend_ipv4_interface(result).ranked[0].interface == wifi


def test_link_properties_survives_restricted_java_interface_enumeration(android_entry, monkeypatch):
    module, _ = android_entry
    monkeypatch.setattr(module, "android_network_status", lambda: {"networks": [_native_network()]})
    classes = sys.modules["java"]
    monkeypatch.setattr(classes, "jclass", lambda name: (_ for _ in ()).throw(PermissionError("restricted")))
    assert [item.address for item in module.android_ipv4_interfaces()] == ["192.168.12.5"]


def test_link_properties_takes_precedence_without_duplicate_or_lost_hotspot(android_entry, monkeypatch):
    module, nics = android_entry
    nics.extend([
        _Nic("vendor_radio0", [_Binding("192.168.12.5", 16)]),
        _Nic("swlan0", [_Binding("192.168.43.1", 24)]),
    ])
    monkeypatch.setattr(module, "android_network_status", lambda: {"networks": [_native_network()]})
    result = module.android_ipv4_interfaces()
    assert len(result) == 2
    assert result[0].prefix_length == 24
    assert {item.address for item in result} == {"192.168.12.5", "192.168.43.1"}


def test_changed_link_properties_drops_the_previous_network_address(android_entry, monkeypatch):
    module, _ = android_entry
    networks = [_native_network()]
    monkeypatch.setattr(module, "android_network_status", lambda: {"networks": networks})
    assert module.android_ipv4_interfaces()[0].address == "192.168.12.5"
    networks[:] = [_native_network(address="192.168.50.6")]
    assert [item.address for item in module.android_ipv4_interfaces()] == ["192.168.50.6"]


def test_invalid_native_binding_does_not_hide_other_addresses(android_entry, monkeypatch):
    module, _ = android_entry
    monkeypatch.setattr(module, "android_network_status", lambda: {"networks": [
        _native_network(address="fe80::1"),
        _native_network(address="not-an-address"),
        _native_network(),
    ]})
    assert [item.address for item in module.android_ipv4_interfaces()] == ["192.168.12.5"]


def test_native_status_is_read_as_json_and_failure_is_nonfatal(android_entry, monkeypatch):
    module, _ = android_entry
    status = {"available": True, "service_running": True, "networks": [_native_network()]}
    monkeypatch.setattr(sys.modules["java"], "jclass", lambda name: types.SimpleNamespace(
        getNetworkStatusJson=lambda: json.dumps(status),
    ))
    assert module.android_network_status() == status
    monkeypatch.setattr(sys.modules["java"], "jclass", lambda name: types.SimpleNamespace(
        getNetworkStatusJson=lambda: "broken JSON",
    ))
    assert module.android_network_status()["available"] is False


def test_empty_java_enumeration_is_allowed(android_entry):
    module, _ = android_entry
    assert module._java_list(None) == []


def test_stop_before_configure_does_not_import_the_backend(android_entry, monkeypatch):
    module, _ = android_entry
    monkeypatch.delitem(sys.modules, "pitwall.app", raising=False)
    module.stop()
    assert "pitwall.app" not in sys.modules
    assert module._stop_requested.is_set()


def test_occupied_dashboard_port_uses_one_consistent_free_loopback_port(android_entry, monkeypatch):
    module, _ = android_entry
    from pitwall.config import settings

    monkeypatch.setitem(sys.modules, "pitwall.app", types.ModuleType("pitwall.app"))
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        original_port = occupied.getsockname()[1]
        monkeypatch.setattr(settings, "web_port", original_port)
        url = module.dashboard_url()
        assert settings.web_port != original_port
        assert url == f"http://127.0.0.1:{settings.web_port}"
        with socket.socket() as backend:
            backend.bind(("127.0.0.1", settings.web_port))
            backend.listen()
            assert module.dashboard_url() == url


def test_free_saved_dashboard_port_is_preserved(android_entry, monkeypatch):
    module, _ = android_entry
    from pitwall.config import settings

    monkeypatch.setitem(sys.modules, "pitwall.app", types.ModuleType("pitwall.app"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    monkeypatch.setattr(settings, "web_port", port)
    assert module.dashboard_url() == f"http://127.0.0.1:{port}"


@pytest.mark.skipif(os.name != "posix", reason="Android/Unix TIME_WAIT binding semantics")
def test_dashboard_restart_reuses_port_after_server_closed_connection(android_entry, monkeypatch):
    module, _ = android_entry
    from pitwall.config import settings

    monkeypatch.setitem(sys.modules, "pitwall.app", types.ModuleType("pitwall.app"))
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    with socket.socket() as previous:
        previous.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        previous.bind(("127.0.0.1", 0))
        previous.listen()
        port = previous.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            accepted, _ = previous.accept()
            with accepted:
                accepted.shutdown(socket.SHUT_RDWR)
            assert client.recv(1) == b""
    # Prove the regression fixture really has a recently closed connection.
    with socket.socket() as without_reuse:
        with pytest.raises(OSError):
            without_reuse.bind(("127.0.0.1", port))
    monkeypatch.setattr(settings, "web_port", port)
    assert module.dashboard_url() == f"http://127.0.0.1:{port}"


def test_stop_during_startup_is_forwarded_when_server_becomes_available(android_entry, monkeypatch):
    module, _ = android_entry
    module._configured = True
    server = types.SimpleNamespace(should_exit=False)
    app = types.SimpleNamespace(state=types.SimpleNamespace(server=None))
    entered = threading.Event()
    allow_server = threading.Event()
    monkeypatch.setitem(sys.modules, "pitwall.app", types.SimpleNamespace(app=app))

    def run():
        entered.set()
        assert allow_server.wait(2)
        app.state.server = server
        deadline = threading.Event()
        for _ in range(100):
            if server.should_exit:
                return
            deadline.wait(0.01)
        raise AssertionError("pending stop did not reach the new server")

    monkeypatch.setitem(sys.modules, "pitwall.main", types.SimpleNamespace(run=run))
    failures = []

    def start():
        try:
            module.start()
        except Exception as error:
            failures.append(error)

    runner = threading.Thread(target=start)
    runner.start()
    assert entered.wait(2)
    module.stop()
    allow_server.set()
    runner.join(timeout=2)
    assert not runner.is_alive()
    assert not failures
    assert server.should_exit is True
    assert module._run_finished.is_set()
