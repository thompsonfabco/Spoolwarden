```
# AUT-5 findings on the web app that a later step removes (tests/aut5_run.mjs --allow). One line each:
#   <check> | <where, as the audit prints it> | <count> | <reason: the step that rebuilds that markup>
# <count> is how often the finding appears in the test's whole run (6 views x 1280/320 x light/dark = 24 passes): a run
# that finds it more often fails, and one that finds it less often fails as stale, so a rebuilt screen has to lower
# or drop its lines. The audit also reads the fields of hidden screens, so a field of one
# screen shows up in every view.
```
