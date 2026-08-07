"""Modelo Mencao: aparição da pessoa em mídia (artigo, post, release)."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Mencao(Base):
    __tablename__ = "mencao"

    id: Mapped[int] = mapped_column(primary_key=True)
    pessoa_id: Mapped[int] = mapped_column(ForeignKey("pessoa.id", ondelete="CASCADE"), index=True)

    fonte: Mapped[str] = mapped_column(String(160))  # "valor", "estadao", "linkedin", etc.
    url: Mapped[str] = mapped_column(String(1024))
    titulo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_publicacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    sentimento: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1 a +1
    temas: Mapped[str | None] = mapped_column(String(512), nullable=True)  # CSV simples

    # Papel da pessoa NESTA matéria: protagonista | citado | autor.
    # "autor" = ela assinou o texto (repórter/colunista): a matéria conta como
    # trajetória, mas NÃO gera conexões — quem ela citou é pauta, não relação.
    papel: Mapped[str | None] = mapped_column(String(20), nullable=True)

    coletado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pessoa: Mapped["Pessoa"] = relationship(back_populates="mencoes")  # type: ignore[name-defined]  # noqa: F821
