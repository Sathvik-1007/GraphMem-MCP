"""Hypothesis configuration for the property-test package.

The suite runs with ``filterwarnings = ["error"]``, so a health check that
hypothesis would normally report as a warning becomes a hard failure.  Two of
them fire here for legitimate reasons and are suppressed once, centrally:

``function_scoped_fixture``
    ``tmp_path`` is used only as a *directory* to put per-example SQLite files
    in.  Nothing is carried between examples — each one builds and closes its
    own backend — so the usual "your fixture is not reset" hazard does not
    apply.

``too_slow`` / ``data_too_large``
    Every DB-backed example opens a real SQLite file and runs migrations.  That
    is deliberate: the point of these tests is to exercise the real query
    layer, not a mock of it.

``database=None`` + ``derandomize=True`` keep the suite deterministic and stop
hypothesis from writing a ``.hypothesis/`` directory into the repo.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

settings.register_profile(
    "graphmem",
    deadline=None,
    database=None,
    derandomize=True,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
        HealthCheck.filter_too_much,
    ],
)
settings.load_profile("graphmem")
