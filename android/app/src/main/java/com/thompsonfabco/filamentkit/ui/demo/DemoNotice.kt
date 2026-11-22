package com.thompsonfabco.filamentkit.ui.demo

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.ui.components.NoticeCard
import com.thompsonfabco.filamentkit.ui.components.NoticeKind
import com.thompsonfabco.filamentkit.ui.components.SwAction
import com.thompsonfabco.filamentkit.ui.components.SwDialog
import com.thompsonfabco.filamentkit.ui.components.SwDialogButton
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/**
 * The texts of the demo notice (05-android-app 3.7) that are not in the string catalogue yet. Only the texts owner opens
 * `strings/en.json`; each text has a proposed key in the RC5 report and moves there with it.
 */
object DemoTexts {
    const val NOTICE = "Demo data. Nothing here is real."
    const val LEAVE = "Leave demo"
    const val ASK_TITLE = "Leave the demo?"
    const val ASK_BODY = "Everything in it is deleted."
}

/** `409 not_in_demo`: the Hub is not in the demo any more (05-android-app 3.7). */
internal const val NOT_IN_DEMO = "not_in_demo"

/**
 * The notice is the first thing in the app's content while the Hub runs in demo: only once paired (the pairing screens come
 * before the app knows what the Hub is), never after the person left it, and with no Dismiss.
 */
internal fun demoNoticeShown(demo: Boolean, paired: Boolean, inPairing: Boolean, left: Boolean): Boolean =
    demo && paired && !inPairing && !left

/** What Leave demo came to: the demo is [Gone] (left, or it was off already), or it [Refused] with [text] (blank: no word came). */
internal sealed interface LeaveOutcome {
    data object Gone : LeaveOutcome
    data class Refused(val text: String) : LeaveOutcome
}

/**
 * Runs `POST api/demo/leave` ([call], which answers whether the Hub is still in the demo). A 409 `not_in_demo` means the
 * demo is already off, so the notice goes all the same; any other failure is the Hub's text (or the client's).
 */
internal suspend fun leaveDemo(call: suspend () -> Boolean): LeaveOutcome = try {
    if (call()) LeaveOutcome.Refused("") else LeaveOutcome.Gone
} catch (e: CancellationException) {
    throw e
} catch (e: ApiError) {
    if (e.status == 409 && e.code == NOT_IN_DEMO) LeaveOutcome.Gone else LeaveOutcome.Refused(e.message)
}

/**
 * The demo notice of 3.7: an info [NoticeCard] with the quiet button "Leave demo", which asks first and calls [leave];
 * [onGone] runs when the demo is gone and the notice goes.
 */
@Composable
internal fun DemoNotice(leave: suspend () -> Boolean, onGone: () -> Unit, modifier: Modifier = Modifier) {
    var asking by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var refused by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    NoticeCard(
        NoticeKind.Info, DemoTexts.NOTICE, modifier.padding(SpoolwardenTheme.space.s3),
        actions = listOf(SwAction(DemoTexts.LEAVE) { refused = null; asking = true }),
    )
    if (asking) {
        SwDialog(
            title = DemoTexts.ASK_TITLE,
            onClose = { if (!busy) asking = false },
            confirm = SwDialogButton(DemoTexts.LEAVE, {
                busy = true
                scope.launch {
                    when (val outcome = leaveDemo(leave)) {
                        LeaveOutcome.Gone -> { asking = false; onGone() }
                        is LeaveOutcome.Refused -> refused = outcome.text
                    }
                    busy = false
                }
            }, busy = busy),
            cancel = SwAction(stringResource(R.string.common_cancel)) { asking = false },
            cancelEnabled = !busy,
        ) {
            Text(DemoTexts.ASK_BODY, style = MaterialTheme.typography.bodyMedium)
            refused?.let { Text(it.ifBlank { stringResource(R.string.common_hub_answer_unknown) }, color = SpoolwardenTheme.colors.bad, style = MaterialTheme.typography.bodyMedium) }
        }
    }
}
