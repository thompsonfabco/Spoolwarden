package com.thompsonfabco.filamentkit.ui.auth

import android.net.Uri
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.data.SettingsStore
import com.thompsonfabco.filamentkit.data.UserSession
import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.EnterpriseCapabilities
import com.thompsonfabco.filamentkit.session.ServerSession
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class EnterpriseLoginUiState(
    val busy: Boolean = false,
    val error: String? = null,
    val capabilities: EnterpriseCapabilities = EnterpriseCapabilities(),
)

/**
 * Coordinates Enterprise authentication (Local/LDAP credentials and SAML PKCE) against [Api],
 * persisting the resulting user role/permissions in [SettingsStore] and token in [ServerSession].
 */
class EnterpriseLoginViewModel(
    private val api: Api,
    private val session: ServerSession,
    private val settings: SettingsStore,
    private val handle: SavedStateHandle = SavedStateHandle(),
    private val getString: (Int) -> String = { resId ->
        when (resId) {
            R.string.ent_signin_error_empty -> "Enter your username and password."
            else -> "Sign-in failed."
        }
    },
) : ViewModel() {

    private val _state = MutableStateFlow(EnterpriseLoginUiState(capabilities = api.enterprise))
    val state: StateFlow<EnterpriseLoginUiState> = _state.asStateFlow()

    private var pendingPkceVerifier: String?
        get() = handle[KEY_PKCE_VERIFIER]
        set(value) {
            handle[KEY_PKCE_VERIFIER] = value
        }

    fun loadCapabilities() {
        viewModelScope.launch {
            runCatching { api.enterpriseCapabilities() }
                .onSuccess { caps -> _state.update { it.copy(capabilities = caps) } }
        }
    }

    fun reportError(message: String) {
        _state.update { it.copy(busy = false, error = message) }
    }

    fun loginWithPassword(username: String, password: String, onSuccess: () -> Unit) {
        val cleanUser = username.trim()
        if (cleanUser.isEmpty() || password.isEmpty()) {
            _state.update { it.copy(error = getString(R.string.ent_signin_error_empty)) }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(busy = true, error = null) }
            try {
                val res = api.login(cleanUser, password)
                val resolvedName = res.user?.username?.ifBlank { cleanUser } ?: cleanUser
                settings.saveUserSession(UserSession(resolvedName, res.role, res.permissions))
                session.applyUserToken(res.token)
                _state.update { it.copy(busy = false) }
                onSuccess()
            } catch (e: ApiError) {
                _state.update { it.copy(busy = false, error = e.message ?: getString(R.string.ent_signin_failed)) }
            } catch (e: Exception) {
                _state.update { it.copy(busy = false, error = e.message ?: getString(R.string.ent_signin_failed)) }
            }
        }
    }

    /** Generates a PKCE verifier/challenge pair and returns the SAML login URL for Chrome Custom Tabs. */
    fun buildSamlAuthUrl(baseUrl: String): String {
        val raw = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val verifier = Base64.getUrlEncoder().withoutPadding().encodeToString(raw)
        val digest = MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII))
        val challenge = Base64.getUrlEncoder().withoutPadding().encodeToString(digest)
        pendingPkceVerifier = verifier
        val cleanBase = baseUrl.trimEnd('/')
        return Uri.parse("$cleanBase/auth/saml/login")
            .buildUpon()
            .appendQueryParameter("redirect_uri", CALLBACK_URI)
            .appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "S256")
            .build()
            .toString()
    }

    fun handleSamlCallback(uri: Uri, onSuccess: () -> Unit) {
        val err = uri.getQueryParameter("error")
        if (!err.isNullOrBlank()) {
            _state.update { it.copy(busy = false, error = err) }
            return
        }
        val code = uri.getQueryParameter("code")
        val verifier = pendingPkceVerifier
        if (code.isNullOrBlank() || verifier.isNullOrBlank()) {
            _state.update { it.copy(busy = false, error = getString(R.string.ent_signin_failed)) }
            return
        }
        pendingPkceVerifier = null
        viewModelScope.launch {
            _state.update { it.copy(busy = true, error = null) }
            try {
                val res = api.exchangePkce(code, verifier)
                val resolvedName = res.user?.username ?: ""
                settings.saveUserSession(UserSession(resolvedName, res.role, res.permissions))
                session.applyUserToken(res.token)
                _state.update { it.copy(busy = false) }
                onSuccess()
            } catch (e: ApiError) {
                _state.update { it.copy(busy = false, error = e.message ?: getString(R.string.ent_signin_failed)) }
            } catch (e: Exception) {
                _state.update { it.copy(busy = false, error = e.message ?: getString(R.string.ent_signin_failed)) }
            }
        }
    }

    companion object {
        const val CALLBACK_URI = "spoolwarden://auth/callback"
        private const val KEY_PKCE_VERIFIER = "pending_pkce_verifier"
    }
}
