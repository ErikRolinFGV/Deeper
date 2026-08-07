"""Modelo AliasPessoa: nomes alternativos pelos quais uma pessoa é citada.

Nasce da fusão de entidades: quando o analista diz que o nó "Dani Braun" é a
"Daniela Braun" do acervo, o slug antigo vira alias. Sem isso, a próxima
coleta que extraísse "Dani Braun" de uma matéria criaria o nó duplicado de
novo, desfazendo silenciosamente o trabalho do analista.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AliasPessoa(Base):
    __tablename__ = "alias_pessoa"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(255))
    pessoa_id: Mapped[int] = mapped_column(
        ForeignKey("pessoa.id", ondelete="CASCADE"), index=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
