package com.thompsonfabco.filamentkit.ui.pair

import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import kotlinx.coroutines.CancellationException

/**
 * Label mode of the scanner (05-android-app 4.4, 7.1): [notice] is a line over the preview (a label's spool the Hub does
 * not know), [rescan] counts up to start scanning again after a read that did not end it.
 */
class LabelScan(val notice: String? = null, val rescan: Int = 0) {
    companion object {
        /** How long a notice stays over the preview, and how long after a read that found nothing the camera reads again. */
        const val NOTICE_MS = 3_000L
        const val RESCAN_MS = 1_500L
    }
}

private val LABEL_ADDRESS = Regex("""^https?://[^/\s?#]+/s/(\d{1,9})/?(?:[?#].*)?$""", RegexOption.IGNORE_CASE)

/**
 * The spool number of a label's code: the address `https://<hub>/s/<number>`, of which only the number after `/s/` counts
 * (the host is ignored, so a label opens its spool whatever Hub address is printed on it). Null for any other text.
 */
fun labelSpoolNumber(text: String): Int? =
    LABEL_ADDRESS.matchEntire(text.trim())?.groupValues?.get(1)?.toIntOrNull()?.takeIf { it > 0 }

/** What the Hub said of a label's spool number. */
internal sealed interface LabelLookup {
    data object Found : LabelLookup
    data object NotFound : LabelLookup
    data object CertificateChanged : LabelLookup
    data class Failed(val text: String) : LabelLookup
}

/**
 * Asks the Hub for the spool ([ask] is `GET api/spools/{id}`): a 404 is a number the Hub does not know; a changed certificate
 * and any other failure are told, never thrown.
 */
internal suspend fun lookupLabelSpool(ask: suspend () -> Unit): LabelLookup = try {
    ask()
    LabelLookup.Found
} catch (e: CancellationException) {
    throw e
} catch (e: FingerprintMismatch) {
    LabelLookup.CertificateChanged
} catch (e: ApiError) {
    if (e.status == 404) LabelLookup.NotFound else LabelLookup.Failed(e.message)
} catch (e: Exception) {
    LabelLookup.Failed(e.message.orEmpty())
}
