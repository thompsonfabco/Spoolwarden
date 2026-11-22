package com.thompsonfabco.filamentkit.ui.history

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.components.SwButtonKind
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme

// The Share corrections lines (05-android-app 10.6): each is shown only while the Hub says Share corrections is on.

/** Recently added, under the header facts: the line that is also the way to Corrections waiting. [count] null: the Hub did not say. */
@Composable
internal fun ShareOnButton(count: Int?, onOpen: () -> Unit, modifier: Modifier = Modifier) {
    SwButton(
        if (count == null) CorrectionsTexts.LINE_ON else CorrectionsTexts.lineOnCount(count),
        onOpen, modifier, kind = SwButtonKind.Quiet, icon = R.drawable.sw_ic_info,
    )
}

/** Edit by hand (above the action bar) and a proposal (above "Accept"): the correction will wait on the Hub. */
@Composable
internal fun ShareWillWaitLine(modifier: Modifier = Modifier) {
    RecentStatusLine(R.drawable.sw_ic_info, CorrectionsTexts.LINE_WILL_WAIT, SpoolwardenTheme.colors.muted, modifier)
}

/** Check the spools, when the spools were read from photos: fields changed here wait once the spools are added. */
@Composable
internal fun ShareCheckLine(modifier: Modifier = Modifier) {
    RecentStatusLine(R.drawable.sw_ic_info, CorrectionsTexts.LINE_CHECK, SpoolwardenTheme.colors.muted, modifier)
}

/** Under the result line of a saved edit or an accepted proposal: what became of the correction. */
@Composable
internal fun ShareResultLines(result: ShareResult?, onOpenCorrections: () -> Unit, modifier: Modifier = Modifier) {
    val colors = SpoolwardenTheme.colors
    when (result) {
        null -> Unit
        ShareResult.Waiting -> Column(modifier, verticalArrangement = Arrangement.spacedBy(SpoolwardenTheme.space.s1)) {
            RecentStatusLine(R.drawable.sw_ic_info, CorrectionsTexts.LINE_WAITING, colors.info)
            SwButton(CorrectionsTexts.TITLE, onOpenCorrections, kind = SwButtonKind.Quiet)
        }
        ShareResult.NoPhoto ->
            RecentStatusLine(R.drawable.sw_ic_info, CorrectionsTexts.LINE_NO_PHOTO, colors.info, modifier)
        is ShareResult.NotWaiting -> RecentStatusLine(
            R.drawable.sw_st_triangle, CorrectionsTexts.lineNotWaiting(result.reason), colors.warn, modifier,
            large = true,
        )
    }
}
