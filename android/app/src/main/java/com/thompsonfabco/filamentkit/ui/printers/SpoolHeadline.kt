package com.thompsonfabco.filamentkit.ui.printers

/** A spool's name in a headline position starts with a capital ("Spool #9"); mid-sentence it stays as it is ("spool #9"). */
fun spoolHeadline(name: String): String = name.replaceFirstChar { it.uppercase() }
