"""SQLAlchemy ORM models — SQLite schema per design-dashboard.md."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all gateway ORM models."""


class Scan(Base):
    """A single scan execution and its metadata."""

    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    total_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="engine")
    diagnostics: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[str | None] = mapped_column(Text, nullable=True)

    violations: Mapped[list[Violation]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    proposals: Mapped[list[RemediationProposal]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Violation(Base):
    """A single violation found during a scan."""

    __tablename__ = "violations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scan_id: Mapped[str] = mapped_column(Text, ForeignKey("scans.id"), nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    file: Mapped[str] = mapped_column(Text, nullable=False)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, default="")

    scan: Mapped[Scan] = relationship(back_populates="violations")


class RemediationProposal(Base):
    """An AI or deterministic remediation proposal linked to a scan."""

    __tablename__ = "remediation_proposals"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scan_id: Mapped[str] = mapped_column(Text, ForeignKey("scans.id"), nullable=False)
    violation_id: Mapped[str | None] = mapped_column(Text, ForeignKey("violations.id"), nullable=True)
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan: Mapped[Scan] = relationship(back_populates="proposals")
