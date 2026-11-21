"""dm peer role

Revision ID: 2f97036a9459
Revises: 5019ed9e4215
Create Date: 2026-11-21 14:19:07.190827
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f97036a9459"
down_revision: str | Sequence[str] | None = "5019ed9e4215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The 6/8 split (slice 2d-3, D-018) adds DM to the CHECKs on player_scope.peer_role and
# peer_group.peer_role, and five columns to player_scope. SQLite can't change a CHECK without
# rebuilding the table, and a rebuild fails while other tables reference it (ERRORS.md), so
# the three derived tables are dropped and created again, empty. Nothing is lost: run
# `scouting aggregate` afterwards and they're rebuilt from the stored matches.

PEER_ROLES_BEFORE = "'GK', 'CB', 'FB', 'CM', 'AM', 'W', 'ST'"
PEER_ROLES_AFTER = "'GK', 'CB', 'FB', 'DM', 'CM', 'AM', 'W', 'ST'"


def _drop_derived_tables() -> None:
    op.drop_table("percentile")  # references peer_group: goes first
    op.drop_table("peer_group")
    op.drop_table("player_scope")


def _create_derived_tables(peer_roles: str, *, split: bool) -> None:
    split_columns = (
        [
            sa.Column("peer_method", sa.String(length=10), nullable=True),
            sa.Column("depth", sa.Double(), nullable=True),
            sa.Column("depth_starts", sa.Integer(), nullable=True),
            sa.Column("partner_gap", sa.Double(), nullable=True),
            sa.Column("partner_starts", sa.Integer(), nullable=True),
            sa.CheckConstraint(
                "peer_method IS NULL OR peer_method IN ('depth', 'partner', 'formation')",
                name=op.f("ck_player_scope_peer_method"),
            ),
        ]
        if split
        else []
    )
    op.create_table(
        "player_scope",
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("matches", sa.Integer(), nullable=False),
        sa.Column("starts", sa.Integer(), nullable=False),
        sa.Column("primary_role", sa.String(length=4), nullable=True),
        sa.Column("peer_role", sa.String(length=4), nullable=True),
        sa.Column("role_source", sa.String(length=8), nullable=True),
        sa.Column("avg_rating", sa.Double(), nullable=True),
        sa.Column("rated_matches", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        *split_columns,
        sa.CheckConstraint(
            f"peer_role IS NULL OR peer_role IN ({peer_roles})",
            name=op.f("ck_player_scope_peer_role"),
        ),
        sa.CheckConstraint(
            "primary_role IS NULL OR primary_role IN "
            "('GK', 'CB', 'FB', 'WB', 'DM', 'CM', 'AM', 'W', 'ST')",
            name=op.f("ck_player_scope_primary_role"),
        ),
        sa.CheckConstraint(
            "role_source IS NULL OR role_source IN ('starts', 'listed')",
            name=op.f("ck_player_scope_role_source"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_player_scope_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scope.id"],
            name=op.f("fk_player_scope_scope_id_scope"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("player_id", "scope_id", name=op.f("pk_player_scope")),
    )
    op.create_index(
        op.f("ix_player_scope_scope_id_peer_role"),
        "player_scope",
        ["scope_id", "peer_role"],
        unique=False,
    )
    op.create_table(
        "peer_group",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("peer_role", sa.String(length=4), nullable=False),
        sa.Column("min_minutes", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            f"peer_role IN ({peer_roles})",
            name=op.f("ck_peer_group_peer_role"),
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scope.id"],
            name=op.f("fk_peer_group_scope_id_scope"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_peer_group")),
        sa.UniqueConstraint(
            "scope_id",
            "peer_role",
            "min_minutes",
            name=op.f("uq_peer_group_scope_id_peer_role_min_minutes"),
        ),
    )
    op.create_table(
        "percentile",
        sa.Column("peer_group_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("metric_id", sa.Integer(), nullable=False),
        sa.Column("basis", sa.String(length=8), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("pct", sa.Double(), nullable=False),
        sa.Column("ranked_among", sa.Integer(), nullable=False),
        sa.CheckConstraint("basis IN ('per90', 'rate')", name=op.f("ck_percentile_basis")),
        sa.CheckConstraint("pct >= 0 AND pct <= 100", name=op.f("ck_percentile_pct")),
        sa.ForeignKeyConstraint(
            ["metric_id"], ["metric.id"], name=op.f("fk_percentile_metric_id_metric")
        ),
        sa.ForeignKeyConstraint(
            ["peer_group_id"],
            ["peer_group.id"],
            name=op.f("fk_percentile_peer_group_id_peer_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_percentile_player_id_player"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "peer_group_id", "player_id", "metric_id", name=op.f("pk_percentile")
        ),
        sqlite_with_rowid=False,
    )
    op.create_index(op.f("ix_percentile_player_id"), "percentile", ["player_id"], unique=False)


def upgrade() -> None:
    _drop_derived_tables()
    _create_derived_tables(PEER_ROLES_AFTER, split=True)


def downgrade() -> None:
    _drop_derived_tables()
    _create_derived_tables(PEER_ROLES_BEFORE, split=False)
