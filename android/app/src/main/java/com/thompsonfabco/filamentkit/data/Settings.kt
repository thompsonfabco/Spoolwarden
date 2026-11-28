package com.thompsonfabco.filamentkit.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.lookFromWire
import com.thompsonfabco.filamentkit.ui.theme.wire
import java.io.IOException
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.security.KeyStoreException
import java.security.ProviderException
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.getAndUpdate
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.retryWhen
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * The paired server. [endpoints] are candidate hosts, best first (mDNS name, last good host, each pairing IP);
 * [fingerprint] is the pinned SHA-256 (lowercase hex, no colons); [token] is the bearer token.
 */
@Serializable
data class ServerConfig(
    val endpoints: List<String>,
    val port: Int,
    val fingerprint: String,
    val token: String,
    val name: String,
)

/** What the pairing screens need from [Settings]; lets JVM tests pass a fake. */
interface SettingsStore {
    suspend fun save(config: ServerConfig)
    suspend fun clear()

    /** The user chose to skip the fingerprint check when pairing with a new server by address. */
    suspend fun skipsFingerprintCheck(): Boolean = false
}

/**
 * The one paired server, in DataStore. The token lives in app-private storage (`allowBackup=false`).
 * The whole config is one JSON value so a save is atomic.
 */
class Settings(private val store: DataStore<Preferences>) : SettingsStore {
    private val json = Json { ignoreUnknownKeys = true }
    private val _corruptServerConfig = MutableStateFlow(false)

    /** True when a stored [ServerConfig] failed to decode; cleared by [consumeCorruptServerConfig], [save], or [clear]. */
    val corruptServerConfig: StateFlow<Boolean> = _corruptServerConfig.asStateFlow()

    /** Returns whether a stored [ServerConfig] failed to decode and clears the one-time notice flag. */
    fun consumeCorruptServerConfig(): Boolean = _corruptServerConfig.getAndUpdate { false }

    /** The paired server, or null when unpaired (or the stored value is unreadable). */
    val server: Flow<ServerConfig?> = store.data.map { prefs ->
        prefs[KEY]?.let { text ->
            runCatching { json.decodeFromString<ServerConfig>(ServerCipher.decrypt(text)) }
                .onFailure { e ->
                    Log.w(Settings::class.java.simpleName, e)
                    _corruptServerConfig.value = true
                }
                .getOrNull()
        }
    }

    /** One read for the startup: the paired server, or null when unpaired or the store cannot be read. */
    suspend fun serverOrNull(): ServerConfig? = try {
        server.first()
    } catch (e: IOException) {
        null
    }

    override suspend fun save(config: ServerConfig) {
        _corruptServerConfig.value = false
        val encoded = json.encodeToString(ServerConfig.serializer(), config)
        store.edit { it[KEY] = ServerCipher.encrypt(encoded) }
    }

    override suspend fun clear() {
        _corruptServerConfig.value = false
        store.edit {
            it.remove(KEY)
            it.remove(HUB_LOOK) // the next Hub says its own
        }
    }

    /** Off unless the user turned it on. It belongs to the phone, so forgetting a server leaves it as it is. */
    val skipFingerprintCheck: Flow<Boolean> = store.data.map { it[SKIP_FINGERPRINT] ?: false }

    override suspend fun skipsFingerprintCheck(): Boolean = skipFingerprintCheck.first()

    suspend fun setSkipFingerprintCheck(skip: Boolean) {
        store.edit { it[SKIP_FINGERPRINT] = skip }
    }

    /** Light, dark or follow the phone; System when nothing (or something unknown) is stored. */
    val appearance: Flow<Appearance> = store.data.map { prefs ->
        when (prefs[APPEARANCE]) {
            "light" -> Appearance.Light
            "dark" -> Appearance.Dark
            else -> Appearance.System
        }
    }.retryWhen { e, _ ->
        // a read error shows System for now and tries again, so a later choice still arrives
        if (e is IOException) {
            emit(Appearance.System)
            delay(RETRY_MS)
            true
        } else {
            false
        }
    }

    suspend fun setAppearance(appearance: Appearance) {
        store.edit { it[APPEARANCE] = appearance.name.lowercase() }
    }

    /** The theme this phone chose in Settings; null until it does (then the Hub's default applies, see `resolveLook`). */
    val lookChoice: Flow<Look?> = store.data.map { prefs -> lookFromWire(prefs[LOOK]) }.retryOnRead(null)

    suspend fun setLook(look: Look) {
        store.edit { it[LOOK] = look.wire }
    }

    /** The Hub's default theme as its last answer said; kept so a start without the Hub still uses it. */
    val hubLook: Flow<Look?> = store.data.map { prefs -> lookFromWire(prefs[HUB_LOOK]) }.retryOnRead(null)

    suspend fun setHubLook(look: Look) {
        store.edit { it[HUB_LOOK] = look.wire }
    }

    /** A read error shows [fallback] for now and tries again, so a later value still arrives. */
    private fun <T> Flow<T>.retryOnRead(fallback: T): Flow<T> = retryWhen { e, _ ->
        if (e is IOException) {
            emit(fallback)
            delay(RETRY_MS)
            true
        } else {
            false
        }
    }

    companion object {
        private const val RETRY_MS = 1000L
        private val KEY = stringPreferencesKey("server")
        private val APPEARANCE = stringPreferencesKey("appearance")
        private val LOOK = stringPreferencesKey("look")
        private val HUB_LOOK = stringPreferencesKey("hub_look")
        private val SKIP_FINGERPRINT = booleanPreferencesKey("skip_fingerprint_check")

        private val Context.dataStore by preferencesDataStore("settings")

        fun create(context: Context): Settings = Settings(context.applicationContext.dataStore)
    }
}

/**
 * Encrypts the serialized [ServerConfig] with a non-exportable AES-256-GCM key in `AndroidKeyStore`.
 * Falls back to plaintext in JVM/Robolectric unit tests where `AndroidKeyStore` is absent, and still
 * reads legacy unencrypted JSON values.
 */
private object ServerCipher {
    private const val KEYSTORE = "AndroidKeyStore"
    private const val KEY_ALIAS = "filament_kit_server_config"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val GCM_TAG_BITS = 128
    private const val KEY_BITS = 256
    private const val PREFIX = "v1:"

    @Synchronized
    fun encrypt(plaintext: String): String {
        val ks = keyStoreOrNull() ?: return plaintext
        return try {
            encryptWith(getOrCreateKey(ks), plaintext)
        } catch (_: GeneralSecurityException) {
            encryptWith(regenerateKey(ks), plaintext)
        } catch (_: ProviderException) {
            encryptWith(regenerateKey(ks), plaintext)
        }
    }

    @Synchronized
    fun decrypt(stored: String): String {
        if (!stored.startsWith(PREFIX)) return stored
        val parts = stored.removePrefix(PREFIX).split(':')
        require(parts.size == 2) { "malformed encrypted server config" }
        val key = existingKey() ?: error("AndroidKeyStore key missing for encrypted server config")
        val iv = Base64.getDecoder().decode(parts[0])
        val ct = Base64.getDecoder().decode(parts[1])
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(GCM_TAG_BITS, iv))
        return String(cipher.doFinal(ct), Charsets.UTF_8)
    }

    private fun encryptWith(key: SecretKey, plaintext: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = Base64.getEncoder().encodeToString(cipher.iv)
        val ct = Base64.getEncoder().encodeToString(cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8)))
        return "$PREFIX$iv:$ct"
    }

    @Synchronized
    private fun keyStoreOrNull(): KeyStore? = try {
        KeyStore.getInstance(KEYSTORE).apply { load(null) }
    } catch (_: KeyStoreException) {
        null
    }

    @Synchronized
    private fun existingKey(): SecretKey? {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        return ks.getKey(KEY_ALIAS, null) as? SecretKey
    }

    @Synchronized
    private fun getOrCreateKey(ks: KeyStore): SecretKey =
        (ks.getKey(KEY_ALIAS, null) as? SecretKey) ?: generateKey()

    @Synchronized
    private fun regenerateKey(ks: KeyStore): SecretKey {
        if (ks.containsAlias(KEY_ALIAS)) ks.deleteEntry(KEY_ALIAS)
        return generateKey()
    }

    private fun generateKey(): SecretKey {
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        gen.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_BITS)
                .build(),
        )
        return gen.generateKey()
    }
}
