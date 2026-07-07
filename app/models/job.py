"""Modelo JobColeta: status de uma coleta assíncrona disparada por busca."""

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class StatusJob(str, Enum):
    """Equivalente a StrEnum (3.11+), mas compatível com Python 3.10+."""
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobColeta(Base):
    __tablename__ = "job_coleta"

    id: Mapped[int] = mapped_column(primary_key=True)
    pessoa_id: Mapped[int | None] = mapped_column(
        ForeignKey("pessoa.id", ondelete="SET NULL"), nullable=True
    )
    termo_busca: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(20), default=StatusJob.QUEUED)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)

    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
