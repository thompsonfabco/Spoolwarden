package com.thompsonfabco.filamentkit.ui.profile

/**
 * What the catalog form offers and builds that is not text for a person: the materials it offers when the Hub sends
 * none, and the one-line name of an entry. The texts of the form are in the string catalogue.
 */
object CatalogTexts {
    /** The materials the form offers when the Hub sends none (an older Hub); anything else is typed under "Other". */
    val MATERIALS = listOf("PLA", "PETG", "ABS", "ASA", "TPU", "PC", "PA", "PVA", "HIPS", "PP")

    /** The line of one entry: brand and name. */
    fun entryLine(brand: String, name: String): String = listOf(brand, name).filter { it.isNotBlank() }.joinToString(" ")
}
