package com.yourpitbox.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Non-exportable Android Keystore key; encrypted preferences only, no backup. */
public final class KeyVault {
    private static final String ALIAS = "yourpitbox.credentials.v1";
    private static KeyVault instance;
    private final SharedPreferences preferences;
    public static synchronized KeyVault initialize(Context context) {
        if (instance == null) instance = new KeyVault(context.getApplicationContext());
        return instance;
    }
    public static synchronized KeyVault getInstance() {
        if (instance == null) throw new IllegalStateException("Vault is not initialized");
        return instance;
    }
    private KeyVault(Context context) {
        preferences = context.getSharedPreferences("encrypted_vault", Context.MODE_PRIVATE);
    }
    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build());
            generator.generateKey();
        }
        return (SecretKey) store.getKey(ALIAS, null);
    }
    private String providerName(String name) {
        if (!AccountSession.isUnlocked()) throw new IllegalStateException("Profile is locked");
        return ProfilePaths.providerName(AccountSession.profileId(), AccountSession.legacyProfile(), name);
    }
    public synchronized String readProvider(String name) throws Exception { return read(providerName(name)); }
    public synchronized void writeProvider(String name, String value) throws Exception { write(providerName(name), value); }
    public synchronized String read(String name) throws Exception {
        String stored = preferences.getString(name, null);
        if (stored == null) return null;
        String[] parts = stored.split(":", -1);
        if (parts.length != 2) throw new IllegalStateException("Invalid encrypted vault entry");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
        cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
        return new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }
    public synchronized void write(String name, String value) throws Exception {
        SharedPreferences.Editor edit = preferences.edit();
        if (value == null) edit.remove(name);
        else {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
            edit.putString(name, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":"
                + Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP));
        }
        if (!edit.commit()) throw new IllegalStateException("Encrypted vault write failed");
    }
}
