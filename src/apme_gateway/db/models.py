"""SQLAlchemy ORM models for gateway persistence.

Schema supports the executive dashboard (PR 3) and future operator workbench
(PR 4).  The ``sessions`` table groups scans by project via the deterministic
``session_id`` (SHA-256 of the project root).
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):  # type: ignore[misc]
    """Declarative base for all gateway models."""


class Session(Base):
    """A project session, keyed by the deterministic session_id hash.

    Attributes:
        session_id: Deterministic 16-char hex SHA-256 of the project root.
        project_path: Filesystem path of the scanned project.
        first_seen: ISO 8601 timestamp of the first event.
        last_seen: ISO 8601 timestamp of the most recent event.
        scans: Related scan rows.
    """

    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_path: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen: Mapped[str] = mapped_column(Text, nullable=False)

    scans: Mapped[list[Scan]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Scan(Base):
    """An individual scan or fix run.

    Attributes:
        scan_id: UUID of the scan run.
        session_id: Owning session hash (FK to sessions).
        project_path: Project root path.
        source: Origin of the scan (cli, ci, gateway).
        created_at: ISO 8601 timestamp of creation.
        scan_type: Either "scan" or "fix".
        total_violations: Total violation count.
        auto_fixable: Count of tier-1 fixable violations.
        ai_candidate: Count of tier-2 AI-candidate violations.
        manual_review: Count of tier-3 manual violations.
        diagnostics_json: JSON-serialised ScanDiagnostics.
        session: Back-reference to owning Session.
        violations: Related violation rows.
        proposals: Related proposal rows.
        logs: Related log rows.
    """

    __tablename__ = "scans"

    scan_id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.session_id"), nullable=False)
    project_path: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="cli")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    scan_type: Mapped[str] = mapped_column(Text, nullable=False, default="scan")
    total_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_fixable: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_candidate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    diagnostics_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped[Session] = relationship(back_populates="scans")
    violations: Mapped[list[Violation]] = relationship(back_populates="scan", cascade="all, delete-orphan")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="scan", cascade="all, delete-orphan")
    logs: Mapped[list[ScanLog]] = relationship(back_populates="scan", cascade="all, delete-orphan")


class Violation(Base):
    """A single violation recorded during a scan.

    Attributes:
        id: Auto-increment primary key.
        scan_id: Owning scan UUID (FK to scans).
        rule_id: Rule identifier (e.g. L001).
        level: Severity level string.
        message: Human-readable violation description.
        file: Relative file path.
        line: Line number or None.
        path: YAML path within the file.
        remediation_class: Numeric remediation tier.
        scope: Numeric rule scope.
        scan: Back-reference to owning Scan.
    """

    __tablename__ = "violations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(Text, ForeignKey("scans.scan_id"), nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    file: Mapped[str] = mapped_column(Text, nullable=False, default="")
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remediation_class: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scan: Mapped[Scan] = relationship(back_populates="violations")


class Proposal(Base):
    """An AI proposal outcome from a fix session.

    Attributes:
        id: Auto-increment primary key.
        scan_id: Owning scan UUID (FK to scans).
        proposal_id: Engine-generated proposal UUID.
        rule_id: Rule that triggered the proposal.
        file: File the proposal targets.
        tier: Proposal tier (2 or 3).
        confidence: AI confidence score (0.0-1.0).
        status: Outcome (approved, rejected, pending).
        scan: Back-reference to owning Scan.
    """

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(Text, ForeignKey("scans.scan_id"), nullable=False)
    proposal_id: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")

    scan: Mapped[Scan] = relationship(back_populates="proposals")


class ScanLog(Base):
    """A structured log entry from the scan pipeline (ADR-033).

    Attributes:
        id: Auto-increment primary key.
        scan_id: Owning scan UUID (FK to scans).
        message: Log message text.
        phase: Pipeline subsystem (engine, native, opa, etc.).
        progress: Progress fraction 0.0-1.0.
        level: Numeric log level.
        scan: Back-reference to owning Scan.
    """

    __tablename__ = "scan_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(Text, ForeignKey("scans.scan_id"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False, default="")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scan: Mapped[Scan] = relationship(back_populates="logs")
