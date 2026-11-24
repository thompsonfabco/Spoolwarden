package com.thompsonfabco.filamentkit.ui.history

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.ActivityEvent
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.UndoWhyNot
import com.thompsonfabco.filamentkit.ui.printers.Say
import kotlinx.coroutines.CancellationException
import kotlin.math.max

/** The event kinds the Hub can undo: the seven spool and slot actions. */
internal object UndoKinds {
    const val WEIGHED = "spool.weighed"
    const val MOVED = "spool.moved"
    const val EMPTIED = "spool.emptied"
    const val OPENED = "spool.opened"
    const val LOADED = "spool.loaded"
    const val UNLOADED = "spool.unloaded"
    const val SLOT_CLEARED = "slot.cleared"
    val ALL = setOf(WEIGHED, MOVED, EMPTIED, OPENED, LOADED, UNLOADED, SLOT_CLEARED)
}

/**
 * What an action offers to undo: the events to look in ([subjects], for example `spool:212`) and the kinds the action
 * writes. [serial] makes two offers of the same action different, so a screen shows the second toast too.
 */
data class UndoOffer(val subjects: List<String>, val kinds: Set<String>, val serial: Long = 0)

/** What pressing Undo came to: [text] is what the toast says next. */
sealed interface UndoOutcome {
    val text: Say

    data class Done(override val text: Say) : UndoOutcome
    data class Refused(override val text: Say) : UndoOutcome
}

/** The rules of the undo toast, pure so they are tested without a screen. */
object UndoRules {
    /** How long the toast is held: 10 seconds. */
    const val TOAST_MS = 10_000

    /** An event is looked for only when it is this young: the action just happened. */
    const val MAX_AGE_MS = 120_000L

    /**
     * The toast's hold: [recommendedMs] is what `AccessibilityManager.getRecommendedTimeoutMillis(10000,
     * FLAG_CONTENT_CONTROLS)` says (never below 10 seconds; longer for people who set a longer time to act).
     */
    fun holdMs(recommendedMs: Int): Long = max(recommendedMs, TOAST_MS).toLong()

    /**
     * The event the action wrote: the newest of [events] (newest first) when its kind is one of [kinds], it is young
     * enough, and the Hub offers an Undo for it. Never an older event: if the newest is not the action's, there is none.
     */
    fun pick(events: List<ActivityEvent>, kinds: Set<String>, nowMs: Long): ActivityEvent? {
        val newest = events.maxByOrNull { it.id } ?: return null
        if (newest.kind !in kinds || !newest.canUndo) return null
        val at = newest.atMillis
        if (at != null && nowMs - at > MAX_AGE_MS) return null
        return newest
    }

    /** The words of a refusal: the Hub's own `message` when it sent one, else ours for its `error`. */
    fun refusal(e: Throwable): Say {
        if (e !is ApiError) return Say.Words(R.string.app_history_undo_failed)
        val hubText = e.message.takeUnless { e.notFromHub }
        return when {
            hubText != null && e.status == CONFLICT -> Say.FromHub(hubText)
            e.status == CONFLICT -> whyNot(e.code)
            e.status == NOT_FOUND -> Say.Words(R.string.history_undo_update_hub)
            e.status != 0 && !e.notFromHub -> Say.FromHub(e.message)
            else -> Say.Words(R.string.app_history_undo_failed)
        }
    }

    /** Our words for a `why_not` value. */
    fun whyNot(code: String?): Say = Say.Words(
        when (code) {
            UndoWhyNot.CHANGED_SINCE -> R.string.undo_why_changed_since
            UndoWhyNot.TOO_OLD -> R.string.undo_why_too_old
            UndoWhyNot.ALREADY_UNDONE -> R.string.undo_why_already_undone
            else -> R.string.undo_why_not_undoable
        },
    )

    /** "Put back." and, for a load or an unload, the Hub's note after it. */
    fun done(note: String?): Say {
        val extra = note?.takeIf { it.isNotBlank() } ?: return Say.Words(R.string.spools_put_back)
        return Say.Words(R.string.history_put_back_note, listOf(extra))
    }

    private const val CONFLICT = 409
    private const val NOT_FOUND = 404
}

/**
 * Finds the event an action wrote and undoes it. The Hub's answer to an action carries no event id, so the id is
 * learned by reading the subject's newest events after the action ([find]).
 */
class UndoFlow(private val backend: ActivityBackend, private val now: () -> Long = System::currentTimeMillis) {
    /** The event to offer Undo for, or null: an older Hub, an action with no undo, or a read that failed. */
    suspend fun find(offer: UndoOffer): ActivityEvent? {
        val events = try {
            offer.subjects.flatMap { backend.activity(it, FIND_LIMIT) }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            return null
        }
        return UndoRules.pick(events, offer.kinds, now())
    }

    /** Undoes [event]; a refusal or a failure is an outcome with its words, not an exception. */
    suspend fun undo(event: ActivityEvent): UndoOutcome = try {
        UndoOutcome.Done(UndoRules.done(backend.undoEvent(event.id).note))
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        UndoOutcome.Refused(UndoRules.refusal(e))
    }

    private companion object {
        /** The newest few (`newest`), whatever order the Hub gives them in: [UndoRules.pick] takes the highest id. */
        const val FIND_LIMIT = 3
    }
}
