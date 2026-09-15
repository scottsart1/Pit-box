package com.yourpitbox.app;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.ParcelFileDescriptor;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.io.IOException;

/**
 * Observe LAN and internet connections independently. A local Wi-Fi network
 * need not have internet, and a phone's internet route can be cellular while
 * its hotspot receives telemetry. Never change the process's network route.
 *
 * NetworkInterface alone can omit interfaces on Android; LinkProperties gives
 * the app-visible addresses and NetworkCapabilities gives their actual kind.
 * The Python entry combines these with NetworkInterface for hotspot addresses
 * which ConnectivityManager does not normally expose as upstream networks.
 */
final class AndroidNetworkMonitor {
    private final Context context;
    private final ConnectivityManager manager;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean closed;
    private boolean registered;
    private volatile String snapshot = "{\"platform\":\"android\",\"networks\":[],\"warnings\":[]}";
    private String observerError;

    private final Runnable refresh = this::refreshSnapshot;
    private final ConnectivityManager.NetworkCallback callback = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) { scheduleRefresh(); }
        @Override public void onLost(Network network) { scheduleRefresh(); }
        @Override public void onCapabilitiesChanged(Network network, NetworkCapabilities caps) { scheduleRefresh(); }
        @Override public void onLinkPropertiesChanged(Network network, LinkProperties links) { scheduleRefresh(); }
    };

    AndroidNetworkMonitor(Context context) {
        this.context = context.getApplicationContext();
        manager = (ConnectivityManager) this.context.getSystemService(Context.CONNECTIVITY_SERVICE);
    }

    void start() {
        if (manager != null) {
            try {
                // No INTERNET requirement: console-only Wi-Fi is useful too.
                manager.registerNetworkCallback(new NetworkRequest.Builder().clearCapabilities().build(), callback);
                registered = true;
            } catch (RuntimeException error) {
                observerError = "Network monitoring unavailable: " + error.getClass().getSimpleName();
            }
        }
        refreshSnapshot();
    }

    private void scheduleRefresh() {
        // Do not call synchronous ConnectivityManager methods from its callback
        // thread. Coalesce the link/capability events into one fresh snapshot.
        handler.removeCallbacks(refresh);
        handler.post(refresh);
    }

    private void refreshSnapshot() {
        if (closed) return;
        JSONObject result = new JSONObject();
        JSONArray networks = new JSONArray();
        JSONArray warnings = new JSONArray();
        try {
            result.put("platform", "android");
            result.put("sdk_int", Build.VERSION.SDK_INT);
            result.put("target_sdk", context.getApplicationInfo().targetSdkVersion);
            result.put("updated_at_ms", System.currentTimeMillis());
            result.put("network_observer_registered", registered);
            result.put("internet_permission", context.checkSelfPermission(Manifest.permission.INTERNET)
                    == PackageManager.PERMISSION_GRANTED);
            if (observerError != null) warnings.put(observerError);
            WifiManager wifi = (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
            result.put("wifi_enabled", wifi != null && wifi.isWifiEnabled());
            PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
            result.put("battery_optimization_exempt", power != null
                    && power.isIgnoringBatteryOptimizations(context.getPackageName()));
            if (manager != null) {
                Network active = manager.getActiveNetwork();
                for (Network network : manager.getAllNetworks()) {
                    NetworkCapabilities caps = manager.getNetworkCapabilities(network);
                    LinkProperties links = manager.getLinkProperties(network);
                    if (caps == null || links == null) continue;
                    String transport = caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) ? "vpn"
                            : caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ? "wifi"
                            : caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) ? "ethernet"
                            : caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ? "cellular" : "unknown";
                    JSONArray addresses = new JSONArray();
                    for (LinkAddress link : links.getLinkAddresses()) {
                        if (link.getAddress() instanceof Inet4Address) {
                            addresses.put(new JSONObject()
                                    .put("address", link.getAddress().getHostAddress())
                                    .put("prefix_length", link.getPrefixLength()));
                        }
                    }
                    networks.put(new JSONObject()
                            .put("interface_name", links.getInterfaceName())
                            .put("transport", transport)
                            .put("is_default", network.equals(active))
                            .put("internet_validated", caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED))
                            .put("addresses", addresses));
                }
            }
        } catch (RuntimeException | JSONException error) {
            warnings.put("Network details unavailable: " + error.getClass().getSimpleName());
        }
        try {
            result.put("networks", networks);
            result.put("warnings", warnings);
            snapshot = result.toString();
        } catch (JSONException ignored) {
            // All keys are constants and values are JSON primitives.
        }
    }

    String snapshot() { return snapshot; }

    /**
     * Mark only this unconnected socket for a LAN network. Incoming wildcard
     * telemetry sockets and the process's internet route remain unchanged.
     */
    boolean bindLocalSocket(int descriptor, String peerAddress) throws IOException {
        if (manager == null || closed) return false;
        // The Python caller supplies a validated numeric IPv4 literal, never
        // a hostname; do not permit an accidental DNS lookup here either.
        if (!peerAddress.matches("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+")) {
            throw new IOException("A local IPv4 address is required");
        }
        InetAddress peer = InetAddress.getByName(peerAddress);
        if (!(peer instanceof Inet4Address) || peer.isLoopbackAddress()) return false;
        Network chosen = null;
        int chosenPrefix = -1;
        Network active = manager.getActiveNetwork();
        for (Network network : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(network);
            LinkProperties links = manager.getLinkProperties(network);
            if (caps == null || links == null || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
                    || !(caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) continue;
            for (LinkAddress link : links.getLinkAddresses()) {
                int prefix = link.getPrefixLength();
                if (!(link.getAddress() instanceof Inet4Address) || prefix < 1 || prefix > 32) continue;
                if (sameSubnet(peer.getAddress(), link.getAddress().getAddress(), prefix)
                        && (prefix > chosenPrefix || (prefix == chosenPrefix && network.equals(active)))) {
                    chosen = network;
                    chosenPrefix = prefix;
                }
            }
        }
        // Android hotspots are downstream interfaces without a Network object.
        // Let their directly-connected kernel route handle this socket.
        if (chosen == null) return false;
        try (ParcelFileDescriptor duplicate = ParcelFileDescriptor.fromFd(descriptor)) {
            chosen.bindSocket(duplicate.getFileDescriptor());
        }
        return true;
    }

    private static boolean sameSubnet(byte[] left, byte[] right, int prefix) {
        for (int index = 0; index < 4 && prefix > 0; index++) {
            int bits = Math.min(prefix, 8);
            int mask = (0xff << (8 - bits)) & 0xff;
            if ((left[index] & mask) != (right[index] & mask)) return false;
            prefix -= bits;
        }
        return true;
    }

    void stop() {
        closed = true;
        handler.removeCallbacksAndMessages(null);
        if (registered && manager != null) {
            try { manager.unregisterNetworkCallback(callback); }
            catch (RuntimeException ignored) { /* already removed by the OS */ }
        }
        registered = false;
    }
}
