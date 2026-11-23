package com.thompsonfabco.filamentkit.ui.home

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.AcceptedUpdates
import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.PresetUpdate
import com.thompsonfabco.filamentkit.net.PresetUpdates
import com.thompsonfabco.filamentkit.ui.printers.Say
import com.thompsonfabco.filamentkit.ui.printers.sayText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** The server calls the Updates dialog needs (FB-041); [of] wraps the paired [Api]. */
interface PresetUpdatesBackend {
    suspend fun list(): PresetUpdates
    suspend fun accept(stamp: String, names: List<String>): AcceptedUpdates
    suspend fun restoreDeleted()

    companion object {
        fun of(api: Api): PresetUpdatesBackend = object : PresetUpdatesBackend {
            override suspend fun list() = api.presetUpdates()
            override suspend fun accept(stamp: String, names: List<String>) = api.acceptPresetUpdates(stamp, names)
            override suspend fun restoreDeleted() = api.restoreDeletedPresets()
        }
    }
}

object PresetUpdateTexts {
    fun accepted(n: Int): Say = Say.Words(R.string.preset_updates_accepted, listOf(n))
    fun slicer(context: Context, target: String) = when (target) {
        "bambu_studio", "bambu" -> context.getString(R.string.presets_target_bambu)
        "orca_slicer", "orca" -> context.getString(R.string.preset_updates_target_orca)
        else -> target
    }
    fun row(context: Context, u: PresetUpdate) = "${u.name} (${slicer(context, u.target)}): " + u.keys.joinToString(", ").ifEmpty { context.getString(R.string.preset_updates_changed_values) }
    fun error(e: Exception): Say = when {
        e is ApiError && e.code == "updates_changed" -> Say.Words(R.string.preset_updates_changed)
        e is ApiError && e.code == "module_off" -> Say.Words(R.string.preset_updates_module_off)
        e is ApiError && e.code == "offline" -> Say.Words(R.string.preset_updates_offline)
        e is ApiError -> Say.FromHub(e.message)
        else -> Say.Words(R.string.preset_updates_unexpected)
    }
}

data class PresetUpdatesState(
    val list: PresetUpdates? = null,
    val loading: Boolean = false,
    val busy: Boolean = false,
    val message: Say? = null,
    val messageIsError: Boolean = false,
    val unauthorized: Boolean = false,
)

/** The Updates dialog: the list, Accept (exactly the names shown), Restore deleted presets. */
class PresetUpdatesViewModel(private val backend: PresetUpdatesBackend) : ViewModel() {
    private val _state = MutableStateFlow(PresetUpdatesState())
    val state: StateFlow<PresetUpdatesState> = _state.asStateFlow()

    /** Loads the list again; [keepMessage] keeps the outcome of the action that asked for the reload. */
    fun load(keepMessage: Boolean = false) {
        _state.update { it.copy(loading = true, message = if (keepMessage) it.message else null) }
        viewModelScope.launch {
            run({ _state.update { it.copy(loading = false) } }) {
                val l = backend.list()
                _state.update { it.copy(list = l) }
            }
        }
    }

    /** Accepts the updates now shown, and nothing else; a refused list (a newer build) is loaded again. */
    fun accept() {
        val s = _state.value
        val l = s.list ?: return
        val stamp = l.stamp ?: return
        val names = l.waiting.map { it.name }
        if (names.isEmpty() || s.busy) return
        _state.update { it.copy(busy = true, message = null) }
        viewModelScope.launch {
            run({ _state.update { it.copy(busy = false) }; load(keepMessage = true) }) {
                val r = backend.accept(stamp, names)
                _state.update { it.copy(message = PresetUpdateTexts.accepted(r.accepted.size), messageIsError = false) }
            }
        }
    }

    fun restoreDeleted() {
        if (_state.value.busy) return
        _state.update { it.copy(busy = true, message = null) }
        viewModelScope.launch {
            run({ _state.update { it.copy(busy = false) } }) {
                backend.restoreDeleted()
                _state.update { it.copy(message = Say.Words(R.string.preset_updates_restored), messageIsError = false) }
            }
        }
    }

    private suspend fun run(finally: () -> Unit, block: suspend () -> Unit) {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            val unauthorized = e is ApiError && e.code == "unauthorized"
            _state.update {
                it.copy(message = PresetUpdateTexts.error(e), messageIsError = true, unauthorized = it.unauthorized || unauthorized)
            }
        } finally {
            finally()
        }
    }
}

@Composable
fun PresetUpdatesDialog(vm: PresetUpdatesViewModel, onClose: () -> Unit) {
    val s by vm.state.collectAsState()
    val l = s.list
    val context = LocalContext.current
    AlertDialog(
        onDismissRequest = { if (!s.busy) onClose() },
        title = { Text(stringResource(R.string.preset_updates_title)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                when {
                    l == null && s.loading -> Text(stringResource(R.string.preset_updates_loading))
                    l == null -> Unit
                    l.waiting.isEmpty() -> Text(
                        if (l.accepted > 0) stringResource(R.string.preset_updates_nothing_waiting, l.accepted)
                        else stringResource(R.string.preset_updates_none),
                        style = MaterialTheme.typography.bodySmall,
                    )
                    else -> {
                        Text(stringResource(R.string.preset_updates_intro), style = MaterialTheme.typography.bodySmall)
                        l.waiting.forEach { Text("• " + PresetUpdateTexts.row(context, it)) }
                        Button(onClick = vm::accept, enabled = !s.busy && !s.loading) {
                            Text(
                                if (s.busy) stringResource(R.string.common_accepting)
                                else pluralStringResource(R.plurals.preset_updates_accept, l.waiting.size, l.waiting.size),
                            )
                        }
                    }
                }
                if (l != null && l.yours.isNotEmpty()) {
                    Text(stringResource(R.string.preset_updates_yours, l.yours.joinToString(", ")), style = MaterialTheme.typography.bodySmall)
                }
                s.message?.let { Text(sayText(it), color = if (s.messageIsError) MaterialTheme.colorScheme.error else Color.Unspecified) }
                OutlinedButton(onClick = vm::restoreDeleted, enabled = !s.busy && !s.unauthorized) {
                    Text(stringResource(R.string.preset_updates_restore))
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose, enabled = !s.busy) { Text(stringResource(R.string.preset_updates_close)) } },
    )
}
