"""The Android interface listing, driven against a fake java.net.NetworkInterface.

On a phone the backend's stdlib fallback learns one address from the default
route, which is the mobile network when the phone is a hotspot. The Android
entry module lists every interface through java.net.NetworkInterface and
registers that listing with the backend; this drives it with fakes.
"""

from __future__ import annotations

import importlib
import sys
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
