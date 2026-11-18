package com.thompsonfabco.filamentkit.ui.spool

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.thompsonfabco.filamentkit.net.AmsSlot
import com.thompsonfabco.filamentkit.net.AmsUnit
import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.AutoLoadResult
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.net.LoadResult
import com.thompsonfabco.filamentkit.net.Printer
import com.thompsonfabco.filamentkit.net.PrinterDetail
import com.thompsonfabco.filamentkit.net.autoText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** The calls "Load into a printer" needs; [of] adapts the real [Api] at each call, tests pass a fake. */
interface LoadBackend {
    suspend fun printers(): List<Printer>
    suspend fun printer(id: String): PrinterDetail
    suspend fun load(id: String, label: String, spoolId: Int, dryRun: Boolean): LoadResult

    /** Auto (A6); a backend without it answers like a Hub that has none (404). */
    suspend fun autoLoad(id: String, spoolId: Int, dryRun: Boolean): AutoLoadResult =
        throw ApiError(404, "not_found", "The Hub has no Auto.")

    companion object {
        fun of(current: () -> Api): LoadBackend = object : LoadBackend {
            override suspend fun printers() = current().printers()
            override suspend fun printer(id: String) = current().printer(id)
            override suspend fun load(id: String, label: String, spoolId: Int, dryRun: Boolean) =
                current().load(id, label, spoolId, dryRun)
            override suspend fun autoLoad(id: String, spoolId: Int, dryRun: Boolean) =
                current().autoLoad(id, spoolId, dryRun)
        }
    }
}

/**
 * Whether a load is being written, for the whole app: a tag tapped meanwhile waits (05 2.6) and the load screen ignores
 * Back (2.5 rule 2). The load's own screen sets it; it is one value because only one load runs at a time.
 */
class LoadGate {
    private val _writing = MutableStateFlow(false)
    val writing: StateFlow<Boolean> = _writing.asStateFlow()

    /** True while the write is a tag link, not a load: the wait text of a tapped tag says which. */
    var forLink: Boolean = false
        private set

    internal fun set(on: Boolean, link: Boolean = false) {
        forLink = on && link
        _writing.value = on
    }
}

/** What a finished load says: waiting when the Hub assigned it to a slot that has not loaded yet, else confirmed or not. */
internal fun loadResult(r: LoadResult, printer: String, label: String): SpoolResult = when {
    r.pending -> SpoolResult.Waiting(printer, label)
    r.confirmed -> SpoolResult.Loaded(printer, label)
    else -> SpoolResult.LoadedUnconfirmed(printer, label)
}

/** The slot label Auto travels as, through the load steps' routes; `auto` is reserved on the Hub (A6). */
const val AUTO_SLOT = "auto"

internal fun isAuto(label: String): Boolean = label.equals(AUTO_SLOT, ignoreCase = true)

/** Automatic is the slot step's first choice only on a printer the Hub says can do it; any other printer is as before. */
internal fun autoOffered(printer: Printer?): Boolean = printer?.autoPossible == true

/** The slot the step starts on: Automatic when it is offered (A5), else none (a slot is tapped). */
internal fun preselected(printer: Printer?): String? = AUTO_SLOT.takeIf { autoOffered(printer) }

/** What the Hub's refusal of an Auto says: its own `text`, else the ruled sentence (A6). */
internal fun autoRefusal(e: ApiError): String = e.autoText ?: LoadTexts.AUTO_REFUSED

/** An Auto call's failure: the refusal (409 `auto_not_possible`) in words, anything else as every load failure. */
internal fun autoFailure(e: Throwable): HubFailure =
    if (e is ApiError && e.code == AUTO_NOT_POSSIBLE) HubFailure.Message(autoRefusal(e)) else hubFailure(e)

private const val AUTO_NOT_POSSIBLE = "auto_not_possible"

/** How a spool is named in a sentence: its brand and name, else its number. */
internal fun spoolWords(spool: SpoolSummary): String = spool.title.ifBlank { "Spool #${spool.id}" }

/** The printers that can take a load now: set up and connected (05 7.6). */
internal fun canTakeLoad(p: Printer): Boolean = p.configured && p.state == Printer.STATE_CONNECTED

/** Step 1: the printers; [printers] is null while they load. */
data class LoadPrintersUi(
    val printers: List<Printer>? = null,
    val loading: Boolean = true,
    val failure: HubFailure? = null,
    val loadedAt: Long? = null,
) {
    /** The only printer that can take the load, when there is exactly one: the step is skipped. */
    val only: Printer? get() = printers?.filter(::canTakeLoad)?.singleOrNull()
}

class LoadPrintersViewModel(private val backend: LoadBackend) : ViewModel() {
    private val _state = MutableStateFlow(LoadPrintersUi())
    val state: StateFlow<LoadPrintersUi> = _state.asStateFlow()

    init {
        load()
    }

    fun load() {
        _state.update { it.copy(loading = true, failure = null) }
        viewModelScope.launch {
            try {
                val list = backend.printers().filter { it.configured }
                _state.update { LoadPrintersUi(list, loading = false, loadedAt = System.currentTimeMillis()) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, failure = hubFailure(e)) }
            }
        }
    }
}

/** Step 2: the slots of one printer, as the printer reports them; [slots] is null while they load. */
data class LoadSlotsUi(
    val printer: Printer? = null,
    val units: List<AmsUnit> = emptyList(),
    val slots: List<AmsSlot>? = null,
    val loading: Boolean = true,
    val failure: HubFailure? = null,
    val loadedAt: Long? = null,
    /** The choice the step stands on: [AUTO_SLOT] when Automatic is offered and nothing else was picked, else a slot's label. */
    val selected: String? = null,
) {
    /** Automatic is offered first (the printer says Auto is possible). */
    val auto: Boolean get() = autoOffered(printer)
}

class LoadSlotsViewModel(private val printerId: String, private val backend: LoadBackend) : ViewModel() {
    private val _state = MutableStateFlow(LoadSlotsUi())
    val state: StateFlow<LoadSlotsUi> = _state.asStateFlow()

    init {
        load()
    }

    /** The step's choice moves to [label] (Automatic or a slot); Next then goes on with it. */
    fun select(label: String) = _state.update { it.copy(selected = label) }

    fun load() {
        _state.update { it.copy(loading = true, failure = null) }
        viewModelScope.launch {
            try {
                val d = backend.printer(printerId)
                val units = d.status?.ams.orEmpty()
                val slots = units.flatMap { it.slots }.filter { it.label.isNotBlank() }
                _state.update {
                    LoadSlotsUi(
                        d.printer, units, slots, loading = false, loadedAt = System.currentTimeMillis(),
                        // a reload keeps what the person picked, if it is still a choice
                        selected = it.selected?.takeIf { pick -> isAuto(pick) && autoOffered(d.printer) || slots.any { s -> s.label == pick } }
                            ?: preselected(d.printer),
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, failure = hubFailure(e)) }
            }
        }
    }
}

/**
 * Step 3 (05 7.6): [plan] is the dry run's answer; while [writing] the load is being sent. [finished] is the result the
 * spool screen shows once it is back on screen; [dropped] and [pairingRefused] are the two writes that end on this step.
 */
data class LoadPreviewUi(
    val printer: Printer? = null,
    val units: List<AmsUnit> = emptyList(),
    val checking: Boolean = true,
    val plan: LoadResult? = null,
    /** The dry run of an Auto load ([AUTO_SLOT]): the slots watched and the spool it replaces; [plan] stays null then. */
    val autoPlan: AutoLoadResult? = null,
    val writing: Boolean = false,
    val failure: HubFailure? = null,
    val pairingRefused: Boolean = false,
    val dropped: Boolean = false,
    /** [failure] came from the write: the Hub refused it, so it is shown with no Try again (05 7.6). */
    val failedWrite: Boolean = false,
    val finished: SpoolResult? = null,
) {
    /** The Hub's preview is here, whichever kind of load it is for. */
    val ready: Boolean get() = plan != null || autoPlan != null
}

/**
 * The preview of loading [spool] into slot [label] of printer [printerId] (any slot when [label] is [AUTO_SLOT]), asked
 * for when it is made, and the load itself. The write is not stopped by leaving the screen: Back waits for its answer ([writing], [gate]).
 */
class LoadPreviewViewModel(
    private val spool: SpoolSummary,
    private val printerId: String,
    private val label: String,
    private val backend: LoadBackend,
    private val gate: LoadGate = LoadGate(),
) : ViewModel() {
    private val _state = MutableStateFlow(LoadPreviewUi())
    val state: StateFlow<LoadPreviewUi> = _state.asStateFlow()

    init {
        check()
    }

    fun check() {
        _state.update { it.copy(checking = true, failure = null, dropped = false, pairingRefused = false) }
        viewModelScope.launch {
            try {
                val d = backend.printer(printerId)
                val auto = isAuto(label)
                val plan = if (auto) null else backend.load(printerId, label, spool.id, dryRun = true)
                val autoPlan = if (auto) backend.autoLoad(printerId, spool.id, dryRun = true) else null
                _state.update {
                    it.copy(
                        printer = d.printer, units = d.status?.ams.orEmpty(), checking = false, plan = plan, autoPlan = autoPlan,
                        failure = it.failure.afterSuccess(),
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(checking = false, failure = autoFailure(e)) }
            }
        }
    }

    /** Sends the load. Once sent it is not cancelled: the screen waits for the answer. */
    fun load() {
        val s = _state.value
        val printer = s.printer ?: return
        if (s.writing || s.checking || !s.ready) return
        gate.set(true)
        _state.update { it.copy(writing = true, failure = null, failedWrite = false, pairingRefused = false, dropped = false) }
        viewModelScope.launch {
            try {
                val result = if (isAuto(label)) {
                    backend.autoLoad(printerId, spool.id, dryRun = false)
                    SpoolResult.Waiting(printer.name, AUTO_SLOT, spoolWords(spool))
                } else {
                    val r = backend.load(printerId, label, spool.id, dryRun = false)
                    // the spool as the Hub has it now, for the spool screen that is shown next
                    r.spool.asSummary()?.let(SpoolCache::put)
                    loadResult(r, printer.name, label)
                }
                _state.update { it.copy(writing = false, finished = result) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: FingerprintMismatch) {
                _state.update { it.copy(writing = false, failure = HubFailure.CertificateChanged) }
            } catch (e: ApiError) {
                _state.update {
                    when {
                        // the Hub refused the pairing before doing anything
                        e.code == UNAUTHORIZED -> it.copy(writing = false, pairingRefused = true)
                        // no route: the request may or may not have reached the printer
                        e.status == 0 -> it.copy(writing = false, dropped = true)
                        else -> autoFailure(e).let { f ->
                            // an answer the app cannot read: nobody knows if it worked, so the load is not offered again
                            if (f == HubFailure.Unexpected) it.copy(writing = false, dropped = true)
                            else it.copy(writing = false, failure = f, failedWrite = true)
                        }
                    }
                }
            } catch (e: Exception) {
                _state.update { it.copy(writing = false, dropped = true) }
            } finally {
                gate.set(false)
            }
        }
    }

    override fun onCleared() {
        gate.set(false)
    }

    private companion object {
        const val UNAUTHORIZED = "unauthorized"
    }
}
