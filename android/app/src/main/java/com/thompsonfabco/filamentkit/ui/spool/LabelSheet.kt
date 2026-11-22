package com.thompsonfabco.filamentkit.ui.spool

import android.annotation.SuppressLint
import android.content.Context
import android.print.PrintAttributes
import android.print.PrintManager
import android.util.Base64
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.viewinterop.AndroidView
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.components.SwButtonKind
import com.thompsonfabco.filamentkit.ui.components.SwSheet
import com.thompsonfabco.filamentkit.ui.components.SwSkeleton
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.CancellationException

/**
 * The texts of Print a label (05-android-app 7.10) that are not in the string catalogue yet. Only the texts owner opens
 * `strings/en.json`; each text has a proposed key in the contract-requests file and moves there with it.
 */
object PrintTexts {
    const val MENU = "Print a label"
    const val TITLE = "Print a label"
    const val PRINT = "Print"
    const val SIZE = "Size"
    const val SHEETS_NOTE = "Sheets of labels are printed from the Hub page or the web app."
    const val ADDRESS =
        "The code holds this Hub's home address. If the Hub is renamed or moves to another port, old labels still open in this app's scanner but not from a phone's camera."

    /** "Could not draw the label. <the Hub's text>"; just the first sentence when no text came. */
    fun couldNot(hubText: String) = if (hubText.isBlank()) "Could not draw the label." else "Could not draw the label. $hubText"

    /** The preview's description; the weight is left out when the Hub gave none. */
    fun preview(id: Int, name: String, material: String, left: String?) =
        if (left == null) "Label of spool $id: $name, $material" else "Label of spool $id: $name, $material, $left g"
}

/**
 * The label sizes the phone offers (`size=` of `GET api/spools/{id}/label.svg`, 7.10): the two roll
 * sizes. Sheets of labels are printed from the Hub page or the web app.
 */
internal enum class LabelSize(val wire: String, val title: String, val widthMm: Float, val heightMm: Float) {
    Small("62x29", "62 x 29 mm", 62f, 29f),
    Medium("50x30", "50 x 30 mm", 50f, 30f),
}

/** What the app asks Android to print on: the label's own size, in thousandths of an inch; no margins. */
internal fun labelMedia(size: LabelSize): PrintAttributes.MediaSize =
    PrintAttributes.MediaSize("sw-label-${size.wire}", size.title, mil(size.widthMm), mil(size.heightMm))

private fun mil(mm: Float): Int = Math.round(mm * 1000 / 25.4f)

/**
 * The page the print service gets: the Hub's drawing as an image (so nothing inside the drawing can run), as wide as the
 * page, on a page with no margin and no gap under the image (a gap would push a label onto a second one).
 */
internal fun labelHtml(svg: ByteArray): String =
    "<html><head><style>@page{margin:0}html,body{margin:0;padding:0}</style></head><body>" +
        "<img alt=\"\" style=\"display:block;width:100%;height:auto\" src=\"data:image/svg+xml;base64," +
        Base64.encodeToString(svg, Base64.NO_WRAP) + "\"></body></html>"

/** What the Hub drew: the [Loaded] SVG, or why it did not: the Hub's text ([Failed], blank: none) or a changed certificate. */
internal sealed interface LabelLoad {
    class Loaded(val svg: ByteArray) : LabelLoad
    data class Failed(val text: String) : LabelLoad
    data object CertificateChanged : LabelLoad
}

/** Asks for one label drawing ([ask] is `GET api/spools/{id}/label.svg?size=`); a failure is told, never thrown. */
internal suspend fun loadLabel(ask: suspend () -> ByteArray): LabelLoad = try {
    LabelLoad.Loaded(ask())
} catch (e: CancellationException) {
    throw e
} catch (e: FingerprintMismatch) {
    LabelLoad.CertificateChanged
} catch (e: ApiError) {
    LabelLoad.Failed(e.message)
} catch (e: Exception) {
    LabelLoad.Failed(e.message.orEmpty())
}

/** Where the sheet gets a drawing from. */
class LabelSource(val svg: suspend (size: String) -> ByteArray)

/**
 * Print a label (05-android-app 7.10), a sheet over the spool screen: the size, the label as the Hub draws it, what the
 * code holds, and Print, which hands the label to Android's print service (one label; nothing changes on the Hub).
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun PrintLabelSheet(spool: SpoolSummary, source: LabelSource, onDismiss: () -> Unit) {
    val space = SpoolwardenTheme.space
    val colors = SpoolwardenTheme.colors
    val context = LocalContext.current
    var size by remember { mutableStateOf(LabelSize.Small) }
    var load by remember { mutableStateOf<LabelLoad?>(null) }
    var retry by remember { mutableIntStateOf(0) }
    var page by remember { mutableStateOf<WebView?>(null) }
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(size, retry) {
        load = null
        shown = false
        load = loadLabel { source.svg(size.wire) }
    }
    SwSheet(onDismiss = onDismiss) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = space.s4).padding(bottom = space.s4),
            verticalArrangement = Arrangement.spacedBy(space.s3),
        ) {
            Text(PrintTexts.TITLE, Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            Text(PrintTexts.SIZE, style = MaterialTheme.typography.labelLarge, color = colors.text)
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                LabelSize.entries.forEachIndexed { index, choice ->
                    SegmentedButton(
                        selected = choice == size,
                        onClick = { size = choice },
                        shape = SegmentedButtonDefaults.itemShape(index, LabelSize.entries.size),
                        modifier = Modifier.heightIn(min = SpoolwardenTheme.sizes.row),
                        colors = SegmentedButtonDefaults.colors(
                            activeContainerColor = colors.accent, activeContentColor = colors.onAccent,
                            activeBorderColor = colors.accent,
                            inactiveContainerColor = colors.surface, inactiveContentColor = colors.text,
                            inactiveBorderColor = colors.borderStrong,
                        ),
                        label = { Text(choice.title, style = MaterialTheme.typography.labelLarge) },
                    )
                }
            }
            Text(PrintTexts.SHEETS_NOTE, style = MaterialTheme.typography.bodySmall, color = colors.muted)
            val said = PrintTexts.preview(spool.id, spool.title, spool.material, spool.remaining?.let { formatGrams(it) })
            val ratio = size.widthMm / size.heightMm
            when (val l = load) {
                null -> SwSkeleton(Modifier.aspectRatio(ratio), height = SpoolwardenTheme.sizes.columnMax / 4)
                is LabelLoad.Failed, LabelLoad.CertificateChanged -> {
                    Text(
                        if (l is LabelLoad.Failed) PrintTexts.couldNot(l.text) else stringResource(R.string.common_cert_changed_notice),
                        style = MaterialTheme.typography.bodyMedium, color = colors.bad,
                    )
                    SwButton(stringResource(R.string.common_try_again), { retry++ })
                }
                is LabelLoad.Loaded -> AndroidView(
                    modifier = Modifier.fillMaxWidth().heightIn(max = SpoolwardenTheme.sizes.columnMax / 2).aspectRatio(ratio)
                        .semantics { contentDescription = said },
                    factory = { ctx ->
                        WebView(ctx).apply {
                            settings.javaScriptEnabled = false
                            settings.allowFileAccess = false
                            // the drawing is described by the sheet's own node, not read as a second, unnamed image
                            importantForAccessibility = android.view.View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
                            webViewClient = object : WebViewClient() {
                                override fun onPageFinished(view: WebView, url: String?) {
                                    shown = true
                                }
                            }
                            page = this
                        }
                    },
                    update = { it.loadDataWithBaseURL(null, labelHtml(l.svg), "text/html", "utf-8", null) },
                    onRelease = { if (page === it) page = null; it.destroy() },
                )
            }
            Text(PrintTexts.ADDRESS, style = MaterialTheme.typography.bodySmall, color = colors.muted)
            SwButton(
                PrintTexts.PRINT, { printLabel(context, page, spool, size) }, Modifier.fillMaxWidth(),
                kind = SwButtonKind.Main, enabled = load is LabelLoad.Loaded && shown,
            )
        }
    }
}

/** Hands the label shown in [page] to Android's print service, which shows its own print dialog (no PDF is made here). */
private fun printLabel(context: Context, page: WebView?, spool: SpoolSummary, size: LabelSize) {
    val view = page ?: return
    val manager = context.getSystemService(PrintManager::class.java) ?: return
    val job = "Spool ${spool.id}"
    manager.print(
        job, view.createPrintDocumentAdapter(job),
        PrintAttributes.Builder().setMediaSize(labelMedia(size)).setMinMargins(PrintAttributes.Margins.NO_MARGINS).build(),
    )
}
