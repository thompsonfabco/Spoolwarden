package com.thompsonfabco.filamentkit.ui.printers

/** A spool's name in a headline position starts with a capital ("The spool"); mid-sentence it stays as it is ("the spool"); a named or numbered spool ("Spool 9") is unchanged. */
fun spoolHeadline(name: String): String = name.replaceFirstChar { it.uppercase() }
