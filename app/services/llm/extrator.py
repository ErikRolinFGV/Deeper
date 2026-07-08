"""Extrator estruturado: texto bruto -> JSON validado por Pydantic.

Usa Claude Sonnet via API Anthropic com `tool_use` para forçar o schema.
A vantagem do tool_use sobre 'devolva JSON' é que o modelo é obrigado a
preencher exatamente os campos definidos, sem campos extras nem nomes
arbitrários — o que torna a etapa de validação Pydantic trivial.
"""

from typing import Any

from anthropic import Anthropic, APIError
from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings

MODELO = "claude-sonnet-4-6"
MAX_TOKENS = 2048


class EntidadesExtraidas(BaseModel):
    """Saída estruturada da extração de uma unidade de texto."""

    eventos: list[str] = Field(
        default_factory=list,
        description=(
            "APENAS eventos públicos nomeados: conferências, fóruns, premiações, "
            "painéis (ex: Fórum de Davos, Brazil Conference, Lide). "
            "NÃO incluir acontecimentos noticiosos (renovação de contrato, anúncio "
            "de sucessão, demissão) nem datas comemorativas (Natal, aniversários)."
        ),
    )
    empresas_mencionadas: list[str] = Field(default_factory=list)
    cargo_pessoa_alvo: str | None = Field(
        default=None,
        description=(
            "Cargo atual da pessoa-alvo se o texto informar, sempre incluindo a "
            "organização quando identificável (ex: 'CEO da Vale', não apenas 'CEO')"
        ),
    )
    pessoas_mencionadas: list[str] = Field(
        default_factory=list,
        description="Outras pessoas citadas além da pessoa-alvo da análise",
    )
    valores_monetarios: list[str] = Field(
        default_factory=list,
        description="Valores como 'R$ 500 milhões', 'US$ 2bi', etc.",
    )
    datas: list[str] = Field(
        default_factory=list,
        description="Datas relevantes, em AAAA-MM-DD quando possível inferir",
    )
    sentimento: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="-1 negativo, 0 neutro, +1 positivo, relativo à pessoa-alvo",
    )
    temas: list[str] = Field(
        default_factory=list,
        description="Principais temas (ex: ESG, M&A, transformação digital)",
    )


# Schema da tool — o modelo é obrigado a chamá-la com essa estrutura exata.
EXTRATOR_TOOL: dict[str, Any] = {
    "name": "registrar_entidades",
    "description": (
        "Registra as entidades estruturadas extraídas do texto sobre o executivo. "
        "Use esta tool para devolver a análise completa."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "eventos": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Apenas eventos públicos nomeados (conferências, fóruns, "
                    "premiações). Nunca acontecimentos noticiosos nem datas "
                    "comemorativas."
                ),
            },
            "empresas_mencionadas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Nomes próprios de companhias citadas",
            },
            "cargo_pessoa_alvo": {
                "type": ["string", "null"],
                "description": (
                    "Cargo atual da pessoa-alvo, se o texto informar, com a "
                    "organização (ex: 'CEO da Vale', não apenas 'CEO')"
                ),
            },
            "pessoas_mencionadas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Outras pessoas citadas além da pessoa-alvo",
            },
            "valores_monetarios": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Valores monetários no formato original do texto",
            },
            "datas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Datas relevantes em AAAA-MM-DD quando possível",
            },
            "sentimento": {
                "type": "number",
                "description": "Tom do texto sobre a pessoa-alvo: -1 a +1",
                "minimum": -1.0,
                "maximum": 1.0,
            },
            "temas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Principais temas tratados no texto",
            },
        },
        "required": [
            "eventos",
            "empresas_mencionadas",
            "pessoas_mencionadas",
            "valores_monetarios",
            "datas",
            "sentimento",
            "temas",
        ],
    },
}


SYSTEM_PROMPT = """Você é um extrator estruturado de informações para o setor de comunicação corporativa brasileiro, trabalhando para a FSB Holding.

Você receberá um texto sobre um executivo brasileiro (perfil profissional, artigo de imprensa, post de rede social, release corporativo) e deve extrair entidades estruturadas chamando a tool `registrar_entidades`.

Diretrizes:
- Seja preciso. Em caso de dúvida, prefira omitir a inventar.
- `eventos`: somente eventos públicos com nome próprio (Fórum de Davos, Lide, Web Summit).
  O fato noticiado em si (renovação de contrato, anúncio de CEO, demissão) NÃO é evento.
  Datas comemorativas (Natal, aniversário da empresa) também não.
- `cargo_pessoa_alvo`: cargo atual da pessoa-alvo se o texto informar; caso contrário null.
- `pessoas_mencionadas`: apenas nomes de outras pessoas além da pessoa-alvo da análise.
- `empresas_mencionadas`: nomes próprios de companhias, não setores genéricos como "varejo" ou "tecnologia".
- `datas`: use AAAA-MM-DD quando puder inferir o ano com segurança; caso contrário, omita.
- `sentimento`: avalie o tom do texto sobre a pessoa-alvo especificamente, não o tom geral.
- `temas`: termos curtos e canônicos (ESG, M&A, IPO, transformação digital, etc.).
- Sempre chame a tool exatamente uma vez, mesmo que algumas listas fiquem vazias.
- Nunca devolva texto livre fora da tool."""


_cliente: Anthropic | None = None


def _get_cliente() -> Anthropic:
    """Cliente Anthropic singleton."""
    global _cliente
    if _cliente is None:
        _cliente = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _cliente


def extrair(texto: str, contexto_pessoa: str) -> EntidadesExtraidas | None:
    """Extrai entidades estruturadas de um texto sobre uma pessoa.

    Args:
        texto: texto bruto a analisar (artigo, post, bio).
        contexto_pessoa: nome ou descrição da pessoa-alvo da análise.

    Returns:
        EntidadesExtraidas em caso de sucesso; None se a chamada falhar ou
        o modelo não usar a tool corretamente.
    """
    if not texto or not texto.strip():
        logger.warning("Extrator chamado com texto vazio")
        return None

    logger.info(
        f"LLM: extraindo entidades sobre '{contexto_pessoa}' ({len(texto)} chars)"
    )
    client = _get_cliente()

    user_message = (
        f"Pessoa-alvo da análise: {contexto_pessoa}\n\n"
        f"--- TEXTO ---\n{texto}\n--- FIM ---"
    )

    try:
        resposta = client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[EXTRATOR_TOOL],
            tool_choice={"type": "tool", "name": "registrar_entidades"},
            messages=[{"role": "user", "content": user_message}],
        )
    except APIError as exc:
        logger.error(f"Falha de API Anthropic: {exc}")
        return None
    except Exception as exc:
        logger.exception(f"Erro inesperado na chamada do extrator: {exc}")
        return None

    # Procura o tool_use na resposta
    for bloco in resposta.content:
        if getattr(bloco, "type", None) == "tool_use" and bloco.name == "registrar_entidades":
            try:
                return EntidadesExtraidas(**bloco.input)
            except ValidationError as exc:
                logger.error(f"Tool retornou estrutura inválida: {exc}")
                return None

    logger.warning("LLM não chamou a tool registrar_entidades — resposta ignorada")
    return None
