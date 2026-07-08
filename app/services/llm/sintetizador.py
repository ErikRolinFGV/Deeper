"""Sintetizador: agrega todos os fragmentos extraídos em um briefing executivo."""

import json

from anthropic import Anthropic, APIError
from loguru import logger

from app.core.config import settings

MODELO = "claude-sonnet-4-6"
MAX_TOKENS = 1500

SYSTEM_PROMPT = """Você é um analista sênior de comunicação corporativa da FSB Holding.
Receberá um conjunto estruturado de dados sobre um executivo brasileiro e deve produzir
um briefing executivo de 3 parágrafos em português:

1. Posicionamento público e trajetória recente.
2. Temas que a pessoa defende e tom da presença na mídia.
3. Momentos-chave dos últimos 12 meses e implicações para relacionamento.

Tom: profissional, objetivo, sem adjetivação excessiva. Cite fontes quando relevante.
Baseie-se apenas nos dados fornecidos; se forem escassos, diga isso com transparência
em vez de especular. Responda somente com os 3 parágrafos, sem títulos nem preâmbulo."""

_cliente: Anthropic | None = None


def _get_cliente() -> Anthropic:
    global _cliente
    if _cliente is None:
        _cliente = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _cliente


def sintetizar(dados_consolidados: dict) -> str | None:
    """Gera o briefing executivo de 3 parágrafos.

    Args:
        dados_consolidados: dict com nome, cargo, menções (fonte/título/data/
            sentimento/temas), empresas, eventos e valores monetários agregados.

    Returns:
        Texto do briefing, ou None se a chamada falhar.
    """
    logger.info("LLM: sintetizando briefing executivo")

    payload = json.dumps(dados_consolidados, ensure_ascii=False, default=str, indent=2)

    try:
        resposta = _get_cliente().messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Dados consolidados sobre o executivo:\n\n{payload}",
                }
            ],
        )
    except APIError as exc:
        logger.error(f"Falha de API Anthropic no sintetizador: {exc}")
        return None
    except Exception as exc:
        logger.exception(f"Erro inesperado no sintetizador: {exc}")
        return None

    texto = "".join(
        bloco.text for bloco in resposta.content if getattr(bloco, "type", None) == "text"
    ).strip()

    return texto or None
