package com.thompsonfabco.filamentkit.ui.settings

import android.app.LocaleManager
import android.content.Context
import android.os.Build
import android.os.LocaleList
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.components.SwMenu
import com.thompsonfabco.filamentkit.ui.components.SwMenuItem
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import java.util.Locale

/**
 * The languages this app carries, as codes: one `res/values-<code>/strings.xml` each, and one line each in
 * `res/xml/locales_config.xml` (a test holds the two equal). English is the app's own, in `res/values`.
 */
internal val CARRIED_LANGUAGES: List<String> = listOf("en")

/** Carried languages that are still marked machine-translated; they show it after their name. */
internal val MACHINE_TRANSLATED_LANGUAGES: Set<String> = emptySet()

/** Per-app language needs Android 13 (`LocaleManager`); on an older phone the Language setting is not shown. */
internal fun languageSettingShown(sdk: Int): Boolean = sdk >= Build.VERSION_CODES.TIRAMISU

/** [code] named as that language names itself (`English`, `Deutsch`), with "(machine-translated)" when it still is. */
internal fun languageName(context: Context, code: String, machineTranslated: Set<String> = MACHINE_TRANSLATED_LANGUAGES): String {
    val locale = Locale.forLanguageTag(code)
    val name = locale.getDisplayName(locale).replaceFirstChar { it.titlecase(locale) }
    return if (code in machineTranslated) listOf(name, context.getString(R.string.settings_language_machine_translated)).joinToString(" ") else name
}

/**
 * Which carried language the app's per-app language tags ([tags], comma separated, empty or null: none chosen) name:
 * null is Automatic. A tag with a region (`de-AT`) names its language when the app carries only `de`.
 */
internal fun chosenLanguage(tags: String?, carried: List<String> = CARRIED_LANGUAGES): String? {
    val first = tags?.split(',')?.map { it.trim() }?.firstOrNull { it.isNotEmpty() } ?: return null
    return carried.firstOrNull { it.equals(first, ignoreCase = true) }
        ?: carried.firstOrNull { it.equals(Locale.forLanguageTag(first).language, ignoreCase = true) }
}

/**
 * The `Accept-Language` the app sends: the phone's own language ([phoneTag], what the app is drawn in, per-app choice
 * included) when the app carries it, otherwise the Hub's ([hubLanguage]; the phone's when the Hub gave none), so a screen is
 * never in two languages (05-android-app 12.2, "Automatic").
 */
internal fun acceptLanguage(phoneTag: String, hubLanguage: String?, carried: List<String> = CARRIED_LANGUAGES): String =
    if (chosenLanguage(phoneTag, carried) != null) phoneTag else hubLanguage?.takeIf { it.isNotBlank() } ?: phoneTag

/** The tags to hand to Android for a choice: null (Automatic) is the empty list, which gives the language back to the system. */
internal fun languageTags(choice: String?): String = choice.orEmpty()

/**
 * The Language group of Appearance (05-android-app 12.2): "Automatic" and every carried language in a dropdown. Choosing sets
 * Android's own per-app language for the app, which draws it again in that language. Not drawn below Android 13.
 */
@Composable
internal fun LanguageSetting(modifier: Modifier = Modifier) {
    if (!languageSettingShown(Build.VERSION.SDK_INT)) return
    LanguageDropdown(LocalContext.current, modifier)
}

@RequiresApi(Build.VERSION_CODES.TIRAMISU)
@Composable
private fun LanguageDropdown(context: Context, modifier: Modifier) {
    val manager = remember { context.getSystemService(LocaleManager::class.java) }
    var chosen by remember { mutableStateOf(chosenLanguage(manager.applicationLocales.toLanguageTags())) }
    var open by remember { mutableStateOf(false) }
    val automatic = stringResource(R.string.common_option_automatic)
    val shown = chosen?.let { languageName(context, it) } ?: automatic
    val space = SpoolwardenTheme.space
    Column(modifier, verticalArrangement = Arrangement.spacedBy(space.s3)) {
        GroupTitle(stringResource(R.string.settings_language_title))
        Box {
            SwButton(shown, { open = true }, Modifier.fillMaxWidth().heightIn(min = SpoolwardenTheme.sizes.row))
            SwMenu(open, { open = false }) {
                SwMenuItem(automatic, { open = false; chosen = null; manager.applicationLocales = LocaleList.getEmptyLocaleList() }, checked = chosen == null)
                CARRIED_LANGUAGES.forEach { code ->
                    SwMenuItem(languageName(context, code), {
                        open = false
                        chosen = code
                        manager.applicationLocales = LocaleList.forLanguageTags(languageTags(code))
                    }, checked = chosen == code)
                }
            }
        }
        Text(stringResource(R.string.settings_language_helper), color = SpoolwardenTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
    }
}
