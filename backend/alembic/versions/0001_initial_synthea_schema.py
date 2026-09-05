"""initial synthea schema

Revision ID: 0001
Revises:
Create Date: 2026-09-05

Bootstraps all 18 Synthea tables from the SQLAlchemy models in app.db.models
(schema documented in data_model.md, join safety documented in join_reference.md).
This is the from-scratch first migration, so it defers to Base.metadata rather than
hand-written op.create_table() calls - every later schema change goes through a
normal `alembic revision --autogenerate` diff against this baseline.
"""

from typing import Sequence, Union

from alembic import op

# make `import app...` resolve the same way env.py does
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.base import Base
import app.db.models  # noqa: F401  (registers every table on Base.metadata)

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
