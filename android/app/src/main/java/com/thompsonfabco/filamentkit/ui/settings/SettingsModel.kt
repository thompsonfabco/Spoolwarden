package com.thompsonfabco.filamentkit.ui.settings

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.alerts.AlertsConfig
import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.nav.HubState
import com.thompsonfabco.filamentkit.ui.printers.Say
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.update.UpdateFailure
import com.thompsonfabco.filamentkit.update.UpdateUi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** The groups of 05-android-app 12.1, in the order the list shows them. */
internal enum class SettingsGroup { Appearance, Hub, Alerts, Voice, Updates, Licences, Developer }

/** The paired Hub as the Hub connection group shows it (12.3). */
internal class SettingsHub(val name: String, val address: String, val fingerprint: String, val version: String?)

/** What the list of 12.1 summarises, and what the pages of 12.2 to 12.6 are drawn from. */
internal class SettingsFacts(
    val appearance: Appearance,
    /** Null: no Hub is paired. */
    val hub: SettingsHub?,
    val hubState: HubState,
    val update: UpdateUi,
    val appVersion: String,
    /** Debug builds only: the Developer group exists. */
    val developer: Boolean,
    /** The theme in use: the phone's choice, else the Hub's default, else Console. */
    val look: Look = Look.Console,
    /** Alerts on this phone; the default is the switch off with nothing to ask. */
    val alerts: AlertsFacts = AlertsFacts(),
    /** The Weigh Station voice: the group exists only while the Hub says it has a Weigh Station. */
    val voice: VoiceUi = VoiceUi(),
)

/** What the Alerts group shows: the phone's switch, whether the Hub has alerts at all, the local network permission, and the Hub's alert rules. */
internal class AlertsFacts(
    val on: Boolean = false,
    /** False when the Hub answered that it has no alerts (an older Hub). */
    val available: Boolean = true,
    val localNetworkGranted: Boolean = true,
    val config: AlertsConfig? = null,
    val offline: Boolean = false,
    val revision: Int = 0,
)

/** What the Hub alert rules section knows: the Hub's last answer and whether a failed save should revert the fields. */
internal data class AlertsConfigUi(
    val config: AlertsConfig? = null,
    val offline: Boolean = false,
    val revision: Int = 0,
)

/** Loads and patches the Hub's alert rules (`GET`/`PATCH api/alerts`), mirroring [VoiceModel]. */
internal class AlertsConfigModel(
    private val scope: CoroutineScope,
    private val api: () -> Api?,
    private val onProblem: (Throwable) -> Unit = {},
) {
    private val _state = MutableStateFlow(AlertsConfigUi())
    val state: StateFlow<AlertsConfigUi> = _state.asStateFlow()

    private val _said = MutableSharedFlow<Say>(extraBufferCapacity = 4)
    val said: SharedFlow<Say> = _said.asSharedFlow()

    fun clear() {
        _state.value = AlertsConfigUi()
    }

    fun refresh() {
        val a = api() ?: return
        scope.launch {
            try {
                val c = a.alertsConfig()
                _state.update { it.copy(config = c, offline = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                when {
                    e.status == 404 -> _state.update { it.copy(config = null, offline = false) }
                    e.isOffline() -> _state.update { it.copy(offline = true) }
                    else -> onProblem(e)
                }
            } catch (e: FingerprintMismatch) {
                onProblem(e)
            } catch (_: Exception) {
                // an answer the app could not read: the last one stays
            }
        }
    }

    fun patch(changes: JsonObject) {
        val a = api() ?: return
        if (changes.isEmpty()) return
        scope.launch {
            try {
                val c = a.patchAlerts(changes)
                _state.update { it.copy(config = c, offline = false) }
                _said.tryEmit(Say.Words(R.string.alerts_saved))
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                notSaved(e)
            } catch (e: FingerprintMismatch) {
                onProblem(e)
            } catch (_: Exception) {
                notSaved(null)
            }
        }
    }

    private fun notSaved(e: ApiError?) {
        _state.update { it.copy(offline = it.offline || e?.isOffline() == true, revision = it.revision + 1) }
        when {
            e == null -> _said.tryEmit(Say.Words(R.string.alerts_save_failed))
            e.isOffline() -> Unit
            e.code == "unauthorized" -> onProblem(e)
            else -> _said.tryEmit(if (e.notFromHub) Say.Words(R.string.alerts_save_failed) else Say.FromHub(e.message))
        }
    }
}

/** The groups the list shows: Developer only in a debug build, Weigh Station voice only when the Hub has a Weigh Station. */
internal fun settingsGroups(developer: Boolean, voice: Boolean = false): List<SettingsGroup> =
    SettingsGroup.entries.filter { (developer || it != SettingsGroup.Developer) && (voice || it != SettingsGroup.Voice) }

/**
 * The fingerprint check switch is the old "skip" setting the other way round: on (the default) means the check runs,
 * so it is on exactly when skipping is off. Turning it off asks first; turning it on needs no question.
 */
internal fun fingerprintCheckOn(skipFingerprintCheck: Boolean): Boolean = !skipFingerprintCheck

internal fun needsSkipConfirmation(turnedOn: Boolean): Boolean = !turnedOn

/** What the Updates summary of 12.1 says. */
internal sealed interface UpdatesSummary {
    /** "Update ready: 0.5.3". */
    data class Ready(val version: String) : UpdatesSummary

    /** "App 0.5.2 · up to date": only once a check reached the Hub. */
    data object UpToDate : UpdatesSummary

    /** Not checked yet: just the app's version. */
    data object Unchecked : UpdatesSummary
}

internal fun updatesSummary(ui: UpdateUi): UpdatesSummary = when {
    ui.available != null -> UpdatesSummary.Ready(ui.offeredVersion?.takeIf { it.isNotBlank() } ?: ui.available.toString())
    ui.checked && !ui.serverTooNew -> UpdatesSummary.UpToDate
    else -> UpdatesSummary.Unchecked
}

/** The states of the update panel (05-android-app 13), one for each row of its table. */
internal sealed interface UpdatePanelState {
    /** Nothing is known yet (no check reached the Hub). */
    data object Unknown : UpdatePanelState
    data object UpToDate : UpdatePanelState
    data object Offered : UpdatePanelState

    /** The Hub speaks a newer API than the app understands. */
    data class HubNewer(val canInstall: Boolean) : UpdatePanelState
    /** [writing]: the APK is being handed to Android, so Cancel is off. */
    data class Downloading(val percent: Int?, val writing: Boolean = false) : UpdatePanelState
    data object NeedsPermission : UpdatePanelState
    /** [canReopen]: Android's window is waiting for an answer, so it can be opened again (it may not have opened). */
    data class Installing(val canReopen: Boolean) : UpdatePanelState
    data object Waiting : UpdatePanelState
    data class Failed(val failure: UpdateFailure, val canInstall: Boolean) : UpdatePanelState
}

/** The panel for [ui]; a download under way wins, then a failure, then the install steps, then the offer. */
internal fun updatePanel(ui: UpdateUi): UpdatePanelState = when {
    ui.busy -> UpdatePanelState.Downloading(ui.progress, ui.writing)
    ui.failure != null -> UpdatePanelState.Failed(ui.failure, canInstall = ui.available != null)
    ui.needsPermission -> UpdatePanelState.NeedsPermission
    ui.installing -> UpdatePanelState.Installing(canReopen = ui.confirmPending)
    ui.confirmPending -> UpdatePanelState.Waiting
    ui.serverTooNew -> UpdatePanelState.HubNewer(canInstall = ui.available != null)
    ui.available != null -> UpdatePanelState.Offered
    ui.checked -> UpdatePanelState.UpToDate
    else -> UpdatePanelState.Unknown
}
