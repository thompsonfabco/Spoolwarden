package com.thompsonfabco.filamentkit.ui.history

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.CorrectionEntry
import com.thompsonfabco.filamentkit.net.CorrectionsWaiting
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.net.NotQueuedCorrection
import com.thompsonfabco.filamentkit.net.ShareState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/** The Share corrections routes of the Hub (stage V1.4); [of] adapts the Api, tests pass a fake. */
interface CorrectionsBackend {
    suspend fun waiting(): CorrectionsWaiting
    suspend fun remove(id: String)

    /** Whether Share corrections is on; null from a Hub that has no such route. */
    suspend fun share(): ShareState?

    companion object {
        /** Every call goes to the Api [current] gives at that moment (a trusted certificate or a re-pair is used at once). */
        fun of(current: () -> Api): CorrectionsBackend = object : CorrectionsBackend {
            override suspend fun waiting() = current().corrections()
            override suspend fun remove(id: String) = current().removeCorrection(id)
            override suspend fun share() = current().shareStateOrNull()
        }
    }
}

/** Only the Hub's own 401 is this phone's pairing; a 502 `unauthorized` is the reader refusing the Hub's token (the Hub says so). */
internal fun ApiError.isPairingRefusal() = status == HTTP_UNAUTHORIZED && code == "unauthorized"

/** A refusal in the Hub's own words where the app's states would blame the wrong thing (the reader's silence is not "the cloud"). */
internal fun correctionFailure(e: ApiError): RecentFailure =
    if (e.code == "unreachable" || e.code == "unauthorized") RecentFailure.Hub(e.message) else failureOf(e)

private const val HTTP_UNAUTHORIZED = 401

/** The state of Share corrections, or null when the Hub has no such route (404: a Hub older than the feature shows none of it). */
internal suspend fun Api.shareStateOrNull(): ShareState? = try {
    shareCorrections()
} catch (e: ApiError) {
    if (e.status == HTTP_NOT_FOUND) null else throw e
}

private const val HTTP_NOT_FOUND = 404

/** Which reader's reading was corrected. */
enum class Engine { Local, Cloud }

/** One changed field of a correction: what the reader read and what the value is now. */
class CorrectionChange(val field: String, val read: JsonElement?, val now: JsonElement?)

/**
 * One row of Corrections waiting (05-android-app 10.6). [record] is the Hub's record id when the record is still kept
 * ([recordKept] false: it is not, so the headline is no link; null: not known), [removing] while its Remove runs.
 */
class CorrectionRow(
    val id: String,
    val filament: String?,
    val at: String,
    val engine: Engine?,
    val changes: List<CorrectionChange>,
    val note: String?,
    val photos: Int,
    val thumbnail: ByteArray?,
    val record: String?,
    val recordKept: Boolean?,
    val removing: Boolean = false,
)

/** A record whose correction the reader did not take: it is not waiting, so it has no Remove. */
class NotTaken(val record: String, val filament: String?, val at: String?, val reason: String)

data class CorrectionsState(
    val loaded: Boolean = false,
    val loading: Boolean = false,
    val rows: List<CorrectionRow> = emptyList(),
    val notTaken: List<NotTaken> = emptyList(),
    val count: Int = 0,
    val bytes: Long = 0,
    val share: ShareState? = null,
    /** The reader does not answer (503 `unreachable`): the list cannot be shown, what waits is kept. */
    val readerDown: Boolean = false,
    val error: RecentFailure? = null,
    /** A Remove just worked: the screen says so once ([CorrectionsViewModel.removedShown]). */
    val removed: Boolean = false,
    val removeFailure: RecentFailure? = null,
    val unauthorized: Boolean = false,
    val mismatch: Pair<String, String>? = null,
    val loadedAt: Long? = null,
) {
    val offline: Boolean get() = error == RecentFailure.Offline

    /** Share corrections is off while corrections are left (the head says no new ones are added). */
    val sharingOff: Boolean get() = share != null && !share.on
}

/** Corrections waiting: the list from the reader through the Hub, and Remove (nothing else is done in the app). */
class CorrectionsViewModel(
    private val backend: CorrectionsBackend,
    private val now: () -> Long = System::currentTimeMillis,
) : ViewModel() {
    private val _state = MutableStateFlow(CorrectionsState())
    val state: StateFlow<CorrectionsState> = _state.asStateFlow()
    private var client: Any? = null
    private var clientKnown = false

    /** Corrections whose Remove is running: a refresh in between keeps their button busy, so a second press is not sent. */
    private val inFlight = mutableSetOf<String>()

    /** The session's client as the screen sees it now: a different one (paired again) than last time reloads. */
    fun clientSeen(now: Any?) {
        val changed = clientKnown && client !== now
        client = now
        clientKnown = true
        if (changed) refresh()
    }

    fun refresh() {
        if (_state.value.loading) return
        _state.update { it.copy(loading = true) }
        viewModelScope.launch {
            load(keepRemoved = false)
            _state.update { it.copy(loading = false) }
        }
    }

    private suspend fun load(keepRemoved: Boolean) {
        try {
            val w = backend.waiting()
            val share = try {
                backend.share()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                null
            }
            _state.update {
                it.copy(
                    loaded = true, rows = w.entries.map { e -> rowOf(e).withRemoving(e.id in inFlight) }, notTaken = w.notQueued.map(::notTakenOf),
                    count = w.count, bytes = w.bytes, share = share, readerDown = false, error = null, loadedAt = now(),
                    unauthorized = false, mismatch = null, removed = it.removed && keepRemoved, removeFailure = null,
                )
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: FingerprintMismatch) {
            _state.update { it.copy(mismatch = e.expected to e.actual) }
        } catch (e: ApiError) {
            when {
                e.isPairingRefusal() -> _state.update { it.copy(unauthorized = true) }
                // the reader's own silence is its own notice (the Hub says so with 503 `unreachable`), not "the cloud is down"
                e.code == READER_DOWN -> _state.update { it.copy(readerDown = true, error = null, loaded = false) }
                else -> _state.update { it.copy(error = correctionFailure(e), readerDown = false) }
            }
        } catch (_: Exception) {
            _state.update { it.copy(error = RecentFailure.Unexpected) }
        }
    }

    /** Takes correction [id] out of the queue; the record it came from is not touched. */
    fun remove(id: String) {
        val row = _state.value.rows.firstOrNull { it.id == id } ?: return
        if (id in inFlight) return
        inFlight += id
        mark(id, true)
        _state.update { it.copy(removeFailure = null) }
        viewModelScope.launch {
            try {
                backend.remove(id)
                _state.update { s -> s.copy(rows = s.rows.filter { it.id != id }, count = (s.count - 1).coerceAtLeast(0), removed = true) }
                // what is left, and its size, from the reader
                load(keepRemoved = true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: FingerprintMismatch) {
                mark(id, false)
                _state.update { it.copy(mismatch = e.expected to e.actual) }
            } catch (e: ApiError) {
                mark(id, false)
                if (e.isPairingRefusal()) {
                    _state.update { it.copy(unauthorized = true) }
                } else {
                    // gone already (taken out elsewhere): the list shows what is left, then the notice (a load clears it)
                    if (e.code == "not_found") load(keepRemoved = false)
                    _state.update { it.copy(removeFailure = correctionFailure(e)) }
                }
            } catch (_: Exception) {
                mark(id, false)
                _state.update { it.copy(removeFailure = RecentFailure.Unexpected) }
            } finally {
                inFlight -= id
            }
        }
    }

    private fun mark(id: String, busy: Boolean) = _state.update { s ->
        s.copy(rows = s.rows.map { if (it.id == id) it.withRemoving(busy) else it })
    }

    fun removedShown() = _state.update { it.copy(removed = false) }

    private fun CorrectionRow.withRemoving(busy: Boolean) = CorrectionRow(
        id, filament, at, engine, changes, note, photos, thumbnail, record, recordKept, busy,
    )

    private companion object {
        const val READER_DOWN = "unreachable"
    }
}

/** The filament of a correction by what it is now (brand and name); what was read when the corrected values have none. */
internal fun filamentOf(corrected: JsonObject, predicted: JsonObject): String? {
    fun name(o: JsonObject) = listOf("vendor", "name").mapNotNull { RecentValues.text(o[it]) }.joinToString(" ").trim().ifEmpty { null }
    return name(corrected) ?: name(predicted)
}

/** The fields whose value differs between the reading and now, in the order the record shows them (others after, by name). */
internal fun changesOf(predicted: JsonObject, corrected: JsonObject): List<CorrectionChange> {
    val order = RecentField.entries.map { it.wire }
    val keys = (predicted.keys + corrected.keys).distinct().sortedWith(compareBy({ order.indexOf(it).let { i -> if (i < 0) order.size else i } }, { it }))
    return keys.filter { !sameValue(predicted[it], corrected[it]) }.map { CorrectionChange(it, predicted[it], corrected[it]) }
}

/** The Hub's record id of a correction's `record` key: the first item's key is the id, a later item's is `<id>.<n>`. */
internal fun recordIdOf(key: String?): String? = key?.substringBefore('.')?.trim()?.ifEmpty { null }

/**
 * A row of Corrections waiting. The Hub leaves `record` out when the record is gone (R87 3), so no record means gone:
 * [CorrectionRow.recordKept] is false and the row shows the thumbnail alone.
 */
internal fun rowOf(e: CorrectionEntry): CorrectionRow {
    val record = recordIdOf(e.record)
    return CorrectionRow(
        id = e.id,
        filament = filamentOf(e.corrected, e.predicted),
        at = e.createdAt,
        engine = when (e.source) {
            "local" -> Engine.Local
            "cloud" -> Engine.Cloud
            else -> null
        },
        changes = changesOf(e.predicted, e.corrected),
        note = e.note?.trim()?.ifEmpty { null },
        photos = e.photos,
        thumbnail = e.thumbnail?.let { runCatching { java.util.Base64.getMimeDecoder().decode(it) }.getOrNull() }?.takeIf { it.isNotEmpty() },
        record = record,
        recordKept = record != null,
    )
}

internal fun notTakenOf(n: NotQueuedCorrection) = NotTaken(n.record, n.name, n.at, n.reason)

/** The key the screen's view model is kept under. */
internal const val CORRECTIONS_KEY = "corrections"
