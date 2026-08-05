"""Root conftest: Hypothesis harness for the mutmut campaign.

mutmut 3.x runs several pytest sessions inside one process (stats pass,
clean-baseline pass, then one per mutant). Hypothesis tracks each property
test's executor across the whole process, so the same test run by a fresh class
instance each session trips HealthCheck.differing_executors. That is a pure
artifact of mutmut re-running in-process, not a test defect: the examples,
inputs, and assertions are unchanged. mutmut copies this file into its
`mutants/` tree (see [tool.mutmut] also_copy) and runs the suite there.

The suppressed check never fires in a normal single-session `pytest` run, so
loading the profile globally is inert outside the campaign; the repo defines no
other Hypothesis profile. If a heavier dev venv times out every mutant, cap
Hypothesis `max_examples` in this profile (see the mutation-testing TODO).
"""

from hypothesis import HealthCheck, settings

settings.register_profile("mutmut", suppress_health_check=[HealthCheck.differing_executors])
settings.load_profile("mutmut")
