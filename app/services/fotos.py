"""Cache local das fotos de perfil.

O LinkedIn não serve a foto por uma URL estável: entrega uma URL assinada
da CDN com prazo de validade embutido no parâmetro `e=<unix ts>`. Guardar
apenas a URL faz o dossiê perder a imagem sozinho algumas semanas depois
da coleta — e, como o payload bruto fica em cache por LINKEDIN_TTL_DIAS,
cada "atualizar dossiê" reaplicava a MESMA URL já vencida. O dossiê nunca
se recuperava sozinho, enquanto uma pessoa nova (coleta fresca) vinha com
foto normalmente.

A correção tem duas pernas, ambas aqui:

1. `foto_expirada` permite ao worker furar o cache do Apify quando o que
   está guardado já não serve mais.
2. `guardar_foto` baixa os bytes no momento da coleta e passamos a servir
   a imagem do nosso próprio domínio — ela deixa de ter validade.
"""

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from loguru import logger

from app.core.config import settings

DIR_FOTOS = Path(settings.DIR_MEDIA) / "fotos"

# Recoletar um pouco antes do vencimento: entre a coleta e o próximo acesso
# do analista pode passar mais de um dia.
MARGEM_DIAS = 2
TAMANHO_MAXIMO = 5 * 1024 * 1024  # 5 MB — foto de perfil não passa disso
TIMEOUT = 15.0

_EXTENSOES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def foto_expirada(url: str | None) -> bool:
    """A URL assinada da CDN já venceu (ou vence nos próximos dias)?

    Sem o parâmetro `e=` não há como saber, e recoletar custa dinheiro no
    Apify — então o caso ambíguo é tratado como válido.
    """
    if not url:
        return False
    valor = parse_qs(urlparse(url).query).get("e", [None])[0]
    if not valor:
        return False
    try:
        expira = datetime.fromtimestamp(int(valor), timezone.utc)
    except (ValueError, OSError, OverflowError):
        return False
    return expira <= datetime.now(timezone.utc) + timedelta(days=MARGEM_DIAS)


def caminho_local(pessoa_id: int) -> Path | None:
    """Arquivo da cópia local, se ela existir (extensão varia)."""
    if not pessoa_id:
        return None
    for caminho in DIR_FOTOS.glob(f"{pessoa_id}.*"):
        if caminho.is_file() and caminho.stat().st_size > 0:
            return caminho
    return None


def guardar_foto(pessoa_id: int, url: str | None) -> bool:
    """Baixa a foto e guarda em disco. Falha de download não é erro fatal.

    Sobrescreve a cópia anterior: a URL nova é sempre mais atual.
    """
    if not pessoa_id or not url:
        return False
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            resposta = client.get(url)
            resposta.raise_for_status()
            conteudo = resposta.content
            tipo = (resposta.headers.get("content-type") or "").split(";")[0].strip()
    except Exception as erro:  # rede, 403 de URL vencida, host fora do ar
        logger.warning(f"Pessoa {pessoa_id}: falha ao baixar a foto ({erro})")
        return False

    return gravar_foto(pessoa_id, conteudo, tipo)


def gravar_foto(pessoa_id: int, conteudo: bytes | None, tipo: str) -> bool:
    """Valida e grava os bytes da foto (download da coleta ou envio do analista)."""
    if not conteudo or len(conteudo) > TAMANHO_MAXIMO:
        logger.warning(f"Pessoa {pessoa_id}: foto vazia ou grande demais, ignorada")
        return False
    if not tipo.startswith("image/"):
        logger.warning(f"Pessoa {pessoa_id}: resposta não é imagem ({tipo!r})")
        return False

    DIR_FOTOS.mkdir(parents=True, exist_ok=True)
    for antigo in DIR_FOTOS.glob(f"{pessoa_id}.*"):
        antigo.unlink(missing_ok=True)
    destino = DIR_FOTOS / f"{pessoa_id}{_EXTENSOES.get(tipo, '.jpg')}"
    destino.write_bytes(conteudo)
    logger.info(f"Pessoa {pessoa_id}: foto guardada em {destino.name}")
    return True


def remover_foto(pessoa_id: int) -> None:
    """Apaga a cópia local (usado ao excluir a pessoa)."""
    if not pessoa_id:
        return
    for caminho in DIR_FOTOS.glob(f"{pessoa_id}.*"):
        caminho.unlink(missing_ok=True)


# ---------- fallback: foto publicada pela imprensa ----------

SERPAPI_URL = "https://serpapi.com/search"
MAX_CANDIDATAS = 4
# Bancos de imagem e redes que servem página, marca d'água ou miniatura ruim.
_HOSTS_RUINS = ("lookaside.instagram", "instagram.com", "facebook.com", "fbsbx",
                "tiktok", "x.com", "twimg", "gettyimages", "shutterstock", "alamy")
_PALAVRAS_RUINS = ("logo", "icon", "banner", "sprite")


def _tokens(texto: str | None) -> list[str]:
    """Minúsculas, sem acento, só palavras com 3+ letras."""
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return [t for t in re.split(r"[^a-z0-9]+", base.lower()) if len(t) >= 3]


def _pontuar_candidata(item: dict, nome: str) -> int:
    """Quão provável é a imagem ser um retrato DESTA pessoa (0 = descarta).

    O sinal forte é o nome no arquivo: redações salvam o retrato como
    "Maria-Antonietta-Russo-tim.jpg". Nome só no título da matéria é fraco
    (a foto pode ser de outra pessoa, do prédio, de um grupo).
    """
    url = item.get("original") or ""
    if not url.startswith("http"):
        return 0
    url_baixa = url.lower()
    if any(h in url_baixa for h in _HOSTS_RUINS):
        return 0
    arquivo = url_baixa.split("?")[0].rsplit("/", 1)[-1]
    if any(p in arquivo for p in _PALAVRAS_RUINS):
        return 0
    largura = int(item.get("original_width") or 0)
    altura = int(item.get("original_height") or 0)
    if largura and altura:
        if min(largura, altura) < 150:
            return 0
        if largura / altura > 2.2:  # faixa panorâmica: palco, banner
            return 0

    toks_nome = _tokens(nome)
    if len(toks_nome) < 2:
        return 0
    em_arquivo = sum(1 for t in toks_nome if t in _tokens(arquivo))
    em_titulo = sum(1 for t in toks_nome if t in _tokens(item.get("title")))

    # Foto de perfil do LinkedIn indexada pelo Google, com o nome no título.
    if "profile-displayphoto" in url_baixa and em_titulo >= 2:
        return 90
    if em_arquivo >= 2:
        return 60 + 5 * em_arquivo + (5 if em_titulo >= 2 else 0)
    return 0


def buscar_foto_imprensa(nome: str, contexto: str | None = None) -> list[str]:
    """URLs candidatas a retrato, da mais para a menos confiável.

    Usada quando o LinkedIn não entrega foto (perfil com foto visível só
    para a rede de contatos). Custa 1 busca SerpAPI. Só devolve imagens com
    o nome da pessoa no arquivo, para não pôr o rosto de outra pessoa no
    dossiê; se nada passar no filtro, o dossiê fica com as iniciais.
    """
    if not nome or not settings.SERPAPI_KEY:
        return []
    consulta = f'"{nome}"' + (f" {contexto}" if contexto else "")
    try:
        resposta = httpx.get(
            SERPAPI_URL,
            params={"engine": "google_images", "q": consulta, "hl": "pt-br",
                    "gl": "br", "api_key": settings.SERPAPI_KEY},
            timeout=30.0,
        )
        resposta.raise_for_status()
        imagens = resposta.json().get("images_results", []) or []
    except Exception as erro:
        logger.warning(f"Busca de foto na imprensa falhou para '{nome}' ({erro})")
        return []
    pontuadas = sorted(
        ((_pontuar_candidata(i, nome), n, i["original"]) for n, i in enumerate(imagens[:40])
         if _pontuar_candidata(i, nome) > 0),
        key=lambda x: (-x[0], x[1]),
    )
    return [url for _, _, url in pontuadas[:MAX_CANDIDATAS]]


def completar_foto_pela_imprensa(pessoa, contexto: str | None = None) -> bool:
    """Se a pessoa está sem foto guardada, tenta a da imprensa. True se achou."""
    if caminho_local(pessoa.id):
        return False
    for url in buscar_foto_imprensa(pessoa.nome_completo or pessoa.nome, contexto):
        if guardar_foto(pessoa.id, url):
            pessoa.foto_url = url
            logger.info(f"Pessoa {pessoa.id}: foto obtida na imprensa ({url[:80]})")
            return True
    logger.info(f"Pessoa {pessoa.id}: nenhuma foto confiável na imprensa")
    return False


def foto_publica(pessoa) -> str | None:
    """URL que a API devolve ao frontend.

    Preferimos a nossa cópia, que não expira. Sem cópia (pessoa coletada
    antes desta mudança, ou download que falhou), devolvemos o original do
    LinkedIn — se ele estiver vencido o frontend cai no avatar de iniciais,
    que é o comportamento antigo.
    """
    local = caminho_local(pessoa.id)
    if local:
        # ?v= muda quando a foto é trocada: o navegador guarda /foto/{id} por
        # um dia e, sem isso, continuaria exibindo a imagem anterior.
        versao = int(local.stat().st_mtime)
        return f"{settings.API_BASE_URL.rstrip('/')}/foto/{pessoa.id}?v={versao}"
    return pessoa.foto_url
