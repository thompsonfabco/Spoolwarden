package com.thompsonfabco.filamentkit.ui.settings

import android.os.Build
import androidx.compose.runtime.Composable
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** The Language setting (05-android-app 12.2): the list, its names, the Android 13 rule and the locale config. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.UPSIDE_DOWN_CAKE], qualifiers = "w411dp-h8000dp")
class LanguageChoiceTest {
    @get:Rule val rule = createComposeRule()

    @Test fun theSettingIsShownFromAndroid13Only() {
        assertFalse(languageSettingShown(Build.VERSION_CODES.Q))
        assertFalse(languageSettingShown(Build.VERSION_CODES.S_V2))
        assertTrue(languageSettingShown(Build.VERSION_CODES.TIRAMISU))
        assertTrue(languageSettingShown(Build.VERSION_CODES.UPSIDE_DOWN_CAKE))
    }

    @Test fun aLanguageIsNamedAsItNamesItselfWithTheMachineNoteWhenMarked() {
        val context = RuntimeEnvironment.getApplication()
        assertEquals("English", languageName(context, "en"))
        assertEquals("Deutsch (machine-translated)", languageName(context, "de", machineTranslated = setOf("de")))
        assertEquals("Русский", languageName(context, "ru"))
    }

    @Test fun theChoiceIsReadFromAndroidsTagsAndNoneIsAutomatic() {
        assertNull(chosenLanguage(null))
        assertNull(chosenLanguage(""))
        assertNull("a language the app does not carry", chosenLanguage("fr"))
        assertEquals("en", chosenLanguage("en"))
        assertEquals("a region still names the language", "en", chosenLanguage("en-GB,de"))
        assertEquals("de", chosenLanguage("de-AT", carried = listOf("en", "de")))
        assertEquals("", languageTags(null))
        assertEquals("en", languageTags("en"))
    }

    @Test fun localesConfigListsExactlyTheLanguagesTheAppCarries() {
        val xml = RuntimeEnvironment.getApplication().resources.getXml(R.xml.locales_config)
        val names = mutableListOf<String>()
        while (xml.next() != org.xmlpull.v1.XmlPullParser.END_DOCUMENT) {
            if (xml.eventType == org.xmlpull.v1.XmlPullParser.START_TAG && xml.name == "locale") {
                names += xml.getAttributeValue("http://schemas.android.com/apk/res/android", "name")
            }
        }
        assertEquals(CARRIED_LANGUAGES, names)
    }

    @Test fun theLanguageSentIsThePhonesWhenCarriedElseTheHubs() {
        assertEquals("en-GB", acceptLanguage("en-GB", "de"))
        assertEquals("de", acceptLanguage("fr-FR", "de"))
        assertEquals("fr-FR", acceptLanguage("fr-FR", null))
        assertEquals("fr-FR", acceptLanguage("fr-FR", " "))
        assertEquals("de-AT", acceptLanguage("de-AT", "en", carried = listOf("en", "de")))
    }

    @Composable
    private fun Themed(content: @Composable () -> Unit) = SpoolwardenTheme(Look.Console, Appearance.Light) { content() }

    @Test fun theDropdownOffersAutomaticFirstAndTheCarriedLanguages() {
        rule.setContent { Themed { LanguageSetting() } }
        rule.onNodeWithText("Language").assertExists()
        rule.onNodeWithText("Automatic uses this phone's language if the app has it, otherwise the Hub's language.").assertExists()
        rule.onNodeWithText("Automatic").performClick()
        rule.onAllNodesWithText("Automatic").assertCountEquals(2)
        rule.onNodeWithText("English").assertExists()
    }

    @Test @Config(sdk = [Build.VERSION_CODES.S])
    fun belowAndroid13NothingIsDrawn() {
        rule.setContent { Themed { LanguageSetting() } }
        rule.onAllNodesWithText("Language").assertCountEquals(0)
    }
}
