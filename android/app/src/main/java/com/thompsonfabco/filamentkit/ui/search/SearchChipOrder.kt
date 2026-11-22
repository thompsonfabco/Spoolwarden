package com.thompsonfabco.filamentkit.ui.search

/**
 * The chips under the search field, in reading and TalkBack order (05 Spools screen). The full design order is
 * Sealed, Opened, In a printer, Checked out, Running low; the app has no Opened or In a printer chip yet, and
 * they slot in between SealedOnly and CheckedOut.
 */
enum class SearchChip { SealedOnly, CheckedOut, RunningLow }

/** Sealed only always; Checked out, then Running low, each only when the Hub has that filter. */
fun searchChipOrder(hasChecked: Boolean, hasLow: Boolean): List<SearchChip> = buildList {
    add(SearchChip.SealedOnly)
    if (hasChecked) add(SearchChip.CheckedOut)
    if (hasLow) add(SearchChip.RunningLow)
}
