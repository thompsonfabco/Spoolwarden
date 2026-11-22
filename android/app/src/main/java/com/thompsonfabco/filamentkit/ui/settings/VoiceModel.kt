package com.thompsonfabco.filamentkit.ui.settings

import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
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

/**
 * The Weigh Station voice group's state. One per app: the Settings list reads [state] to know
 * whether the group exists, the group's page edits through it. [onProblem] takes a changed certificate and a revoked
 * token to the pairing screen, as every other call does. The result of Test or a refused save comes out of [said].
 */
internal class VoiceModel(
    private val scope: CoroutineScope,
    private val api: () -> Api?,
    private val onProblem: (Throwable) -> Unit = {},
) {
    private val _state = MutableStateFlow(VoiceUi())
    val state: StateFlow<VoiceUi> = _state.asStateFlow()

    private val _said = MutableSharedFlow<String>(extraBufferCapacity = 4)

    /** A sentence for a snackbar: the result of Test, or why a save did not take. */
    val said: SharedFlow<String> = _said.asSharedFlow()

    /** Reads the Hub's voice: no such route (404) is no group; unreachable keeps the last answer, marked offline. */
    fun refresh() {
        val a = api() ?: return
        scope.launch {
            try {
                val v = a.voice()
                _state.update { it.copy(settings = v, offline = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                when {
                    e.status == 404 -> _state.update { it.copy(settings = null, offline = false) }
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

    /** Saves what differs from the Hub's values; a control let go with nothing changed asks nothing. */
    fun save(edit: VoiceEdit) {
        val a = api() ?: return
        val saved = _state.value.settings ?: return
        val changes = voicePatch(saved, edit)
        if (changes.isEmpty()) return
        scope.launch {
            try {
                val v = a.patchVoice(changes)
                _state.update { it.copy(settings = v, offline = false) }
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

    /** Test with the values on the screen; the result is [said]. */
    fun test(edit: VoiceEdit) {
        val a = api() ?: return
        val saved = _state.value.settings ?: return
        if (_state.value.testing) return
        val req = voiceTestRequest(saved, edit)
        _state.update { it.copy(testing = true) }
        scope.launch {
            try {
                a.testVoice(req.speaker, req.speed, req.volume)
                _said.tryEmit(VoiceTexts.TEST_PLAYED)
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                when {
                    e.isOffline() -> _state.update { it.copy(offline = true) }
                    e.code == "unauthorized" -> onProblem(e)
                    else -> _said.tryEmit(testFailureText(e))
                }
            } catch (e: FingerprintMismatch) {
                onProblem(e)
            } catch (_: Exception) {
                _said.tryEmit(VoiceTexts.TEST_NO_ANSWER)
            } finally {
                _state.update { it.copy(testing = false) }
            }
        }
    }

    /** A save that did not take: the controls go back to the Hub's values and the Hub's sentence is said. */
    private fun notSaved(e: ApiError?) {
        _state.update { it.copy(offline = it.offline || e?.isOffline() == true, revision = it.revision + 1) }
        when {
            e == null -> _said.tryEmit(VoiceTexts.SAVE_FAILED)
            e.isOffline() -> Unit
            e.code == "unauthorized" -> onProblem(e)
            else -> _said.tryEmit(if (e.notFromHub) VoiceTexts.SAVE_FAILED else e.message)
        }
    }
}
