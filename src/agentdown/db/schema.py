"""Database schema (SQLAlchemy Core). Keep it Postgres-portable: no SQLite-only types or SQL.

Services, access paths and descriptions live in the catalogue file (catalog/services.yaml),
loaded into memory. The DB only stores what agents report against: `targets` (with a
`listed` flag mirrored from the catalogue at startup), reports and daily usage counters.
"""

import sqlalchemy as sa

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

UTCDateTime = sa.DateTime(timezone=True)

targets = sa.Table(
    "targets",
    metadata,
    sa.Column("pk", sa.Integer, primary_key=True),
    sa.Column("type", sa.String(16), nullable=False),  # site | route | mcp | skill
    sa.Column("target_id", sa.String(512), nullable=False),  # canonical, normalised id
    sa.Column("listed", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.UniqueConstraint("type", "target_id"),
    sa.Index(None, "target_id"),  # status and page queries look paths up by id alone
)

reports = sa.Table(
    "reports",
    metadata,
    sa.Column("pk", sa.Integer, primary_key=True),
    sa.Column("target_pk", sa.Integer, sa.ForeignKey("targets.pk"), nullable=False),
    sa.Column(
        "outcome", sa.String(16), nullable=False
    ),  # always "failed" (success reports removed)
    sa.Column("what_failed", sa.JSON),  # list of enum values
    sa.Column("country", sa.String(2)),  # ISO-3166 alpha-2, self-reported
    sa.Column("agent_type", sa.String(32)),
    sa.Column("note_scrubbed", sa.Text),  # never served to agents
    sa.Column("reporter_fp", sa.String(64), nullable=False),  # salted, daily-rotating
    sa.Column("prefix_fp", sa.String(64), nullable=False),  # salted network prefix
    sa.Column("ua_family", sa.String(32)),
    sa.Column("signed", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("options_version", sa.Integer, nullable=False),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index(None, "target_pk", "created_at"),
    sa.Index(None, "reporter_fp", "target_pk", "created_at"),
    sa.Index(None, "created_at"),  # the site-wide counter
)

status_current = sa.Table(
    "status_current",
    metadata,
    sa.Column("target_pk", sa.Integer, sa.ForeignKey("targets.pk"), primary_key=True),
    # no_reported_issues | issues_reported | many_issues_reported (M4 detector)
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("since", UTCDateTime, nullable=False),
    sa.Column("n_window", sa.Integer, nullable=False, server_default="0"),
    sa.Column("baseline", sa.Float, nullable=False, server_default="0"),
    sa.Column("breakdown", sa.JSON),
    # Consecutive evaluations below the current status; it steps down at 6 (D19).
    sa.Column("calm_runs", sa.Integer, nullable=False, server_default="0"),
    sa.Column("updated_at", UTCDateTime, nullable=False),
)

status_transitions = sa.Table(
    "status_transitions",
    metadata,
    sa.Column("pk", sa.Integer, primary_key=True),
    sa.Column("target_pk", sa.Integer, sa.ForeignKey("targets.pk"), nullable=False),
    sa.Column("from_status", sa.String(32), nullable=False),
    sa.Column("to_status", sa.String(32), nullable=False),
    sa.Column("inputs", sa.JSON, nullable=False),  # everything the detector used
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index(None, "target_pk", "created_at"),
)

salts = sa.Table(
    "salts",
    metadata,
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("salt", sa.String(64), nullable=False),
    sa.Column("created_at", UTCDateTime, nullable=False),
)

# Daily usage counters for learning (D64). Counts only: no IPs, fingerprints or raw input.
# event: lookup_service | lookup_path | lookup_miss | report
# subject: a service id, a canonical path id, a normalised miss id, or "unparseable"
usage_daily = sa.Table(
    "usage_daily",
    metadata,
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("event", sa.String(16), primary_key=True),
    sa.Column("subject", sa.String(512), primary_key=True),
    sa.Column("count", sa.Integer, nullable=False, server_default="0"),
)

# Status lookups per UTC hour, for the rolling counter in the page header. Pruned after 8 days.
checks_hourly = sa.Table(
    "checks_hourly",
    metadata,
    sa.Column("hour", UTCDateTime, primary_key=True),
    sa.Column("count", sa.Integer, nullable=False, server_default="0"),
)
