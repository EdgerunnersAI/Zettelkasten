"""Phase 8.0 Rev++: apply_migrations.py must surface distinct exit codes for drift vs other failures."""
from __future__ import annotations


def test_drift_detected_exit_code_distinct_from_migration_failed():
    from ops.scripts import apply_migrations
    assert hasattr(apply_migrations, "EXIT_DRIFT_DETECTED"), (
        "EXIT_DRIFT_DETECTED constant must exist (Rev++ operator-UX); deploy.sh and humans rely on it"
    )
    assert apply_migrations.EXIT_DRIFT_DETECTED != 1, (
        "EXIT_DRIFT_DETECTED must be distinct from generic MIGRATION_FAILED=1 so deploy.sh can branch"
    )
    assert isinstance(apply_migrations.EXIT_DRIFT_DETECTED, int)
