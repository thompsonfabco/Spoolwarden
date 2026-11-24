package com.thompsonfabco.filamentkit.ui.search

/**
 * The address of Spoolman's own page as the Hub gave it (`spoolman_url`), or null when there is none or
 * it is not a web address: the item is then not offered. The app never builds the address itself.
 */
internal fun spoolmanLink(url: String?): String? {
    val link = url?.trim()?.takeIf { it.isNotEmpty() } ?: return null
    return link.takeIf { it.startsWith("http://", ignoreCase = true) || it.startsWith("https://", ignoreCase = true) }
}
