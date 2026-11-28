package com.thompsonfabco.filamentkit.ui.alerts

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.navigation.NavDestination
import androidx.navigation.NavDestination.Companion.hasRoute
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.alerts.AlertItem
import com.thompsonfabco.filamentkit.alerts.AlertTarget
import com.thompsonfabco.filamentkit.alerts.AlertsRoute
import com.thompsonfabco.filamentkit.alerts.SettingsAlertsRoute
import com.thompsonfabco.filamentkit.alerts.AlertsUi
import com.thompsonfabco.filamentkit.alerts.alertsList
import com.thompsonfabco.filamentkit.ui.components.NoticeCard
import com.thompsonfabco.filamentkit.ui.components.ScreenState
import com.thompsonfabco.filamentkit.ui.components.SwAction
import com.thompsonfabco.filamentkit.ui.components.SwEmptyState
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme

private enum class AlertFilter(val labelRes: Int) {
    All(R.string.features_filter_all),
    Raised(R.string.alert_list_raised),
    Earlier(R.string.alert_list_earlier),
}

/**
 * The Alerts list, opened from "<n> more alerts" and Settings > Alerts > Recent alerts: the raised
 * conditions first, then the alert events of the last 7 days, newest first, each with its icon (the notice's shape),
 * title, sentence, time and its action. A place the app has no screen for has no button here: this is that screen.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
internal fun AlertsScreen(
    ui: AlertsUi,
    nowMillis: Long,
    onAction: (AlertTarget) -> Unit,
    modifier: Modifier = Modifier,
    loading: Boolean = false,
    offline: Boolean = false,
    onRefresh: () -> Unit = {},
) {
    val space = SpoolwardenTheme.space
    val rows = alertsList(ui.conditions, ui.events, nowMillis, AlertsUi.LIST_DAYS)
    val raisedCount = ui.conditions.count { it.condition?.raised != false }
    val raised = rows.take(raisedCount)
    val earlier = rows.drop(raisedCount)
    var filter by rememberSaveable { mutableStateOf(AlertFilter.All) }

    PullToRefreshBox(
        isRefreshing = loading && ui.loaded,
        onRefresh = onRefresh,
        modifier = modifier.fillMaxSize(),
    ) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            Column(
                Modifier.widthIn(max = SpoolwardenTheme.sizes.columnMax).fillMaxWidth().verticalScroll(rememberScrollState())
                    .padding(space.s4),
                verticalArrangement = Arrangement.spacedBy(space.s3),
            ) {
                if (!ui.available) {
                    SwEmptyState(
                        icon = R.drawable.sw_st_hollow,
                        title = stringResource(R.string.alert_list_title),
                        text = stringResource(R.string.alerts_unavailable),
                        dashed = false,
                    )
                } else {
                    ScreenState(
                        loaded = ui.loaded,
                        loading = loading || !ui.loaded,
                        isEmpty = rows.isEmpty(),
                        empty = {
                            SwEmptyState(
                                icon = R.drawable.sw_st_hollow,
                                title = stringResource(R.string.alert_list_title),
                                text = stringResource(R.string.alert_list_empty),
                                dashed = false,
                            )
                        },
                        onTryAgain = onRefresh,
                        offline = offline,
                        loadedSecondsAgo = ui.loadedAt?.let { ((nowMillis - it) / MS_PER_SECOND).coerceAtLeast(0) } ?: 0,
                    ) {
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(space.s2)) {
                            AlertFilter.entries.forEach { entry ->
                                FilterChip(
                                    selected = filter == entry,
                                    onClick = { filter = entry },
                                    label = { Text(stringResource(entry.labelRes)) },
                                    modifier = Modifier.defaultMinSize(minWidth = SpoolwardenTheme.sizes.row, minHeight = SpoolwardenTheme.sizes.row),
                                )
                            }
                        }
                        val showRaised = filter != AlertFilter.Earlier && raised.isNotEmpty()
                        val showEarlier = filter != AlertFilter.Raised && earlier.isNotEmpty()
                        if (!showRaised && !showEarlier) {
                            SwEmptyState(
                                icon = R.drawable.sw_st_hollow,
                                title = stringResource(R.string.alert_list_title),
                                text = stringResource(R.string.alert_list_empty),
                            )
                        } else {
                            if (showRaised) {
                                Text(
                                    stringResource(R.string.alert_list_raised),
                                    Modifier.semantics { heading() },
                                    style = MaterialTheme.typography.titleMedium,
                                )
                                raised.forEach { AlertRow(it, nowMillis, onAction) }
                            }
                            if (showEarlier) {
                                Text(
                                    stringResource(R.string.alert_list_earlier),
                                    Modifier.semantics { heading() },
                                    style = MaterialTheme.typography.titleMedium,
                                )
                                earlier.forEach { AlertRow(it, nowMillis, onAction) }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AlertRow(item: AlertItem, nowMillis: Long, onAction: (AlertTarget) -> Unit) {
    val target = item.target()
    NoticeCard(
        item.severity.noticeKind(), item.sentenceFor(nowMillis), title = item.event.title,
        actions = if (target == AlertTarget.AlertsList) {
            emptyList()
        } else {
            listOf(SwAction(stringResource(target.labelRes())) { onAction(target) })
        },
    )
}

private const val MS_PER_SECOND = 1000L

/** The top bar's title on the alert screens; null on any other screen. */
@Composable
internal fun alertsPlaceTitle(destination: NavDestination?): String? = when {
    destination == null -> null
    destination.hasRoute(AlertsRoute::class) -> stringResource(R.string.settings_alerts_recent)
    destination.hasRoute(SettingsAlertsRoute::class) -> stringResource(R.string.settings_group_alerts)
    else -> null
}
