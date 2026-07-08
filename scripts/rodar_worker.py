"""Inicia o worker RQ de forma compatível com Windows.

O worker padrão do RQ usa os.fork(), que não existe no Windows. Este script
usa SimpleWorker (executa o job no mesmo processo) com TimerDeathPenalty
(timeout via threading.Timer em vez de sinais Unix).

Uso, a partir da raiz do projeto com o venv ativado:

    python scripts\\rodar_worker.py

Deixe esta janela aberta: é ela que processa as buscas enfileiradas.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from redis import Redis
from rq import Queue, SimpleWorker
from rq.timeouts import TimerDeathPenalty

from app.core.config import settings
from app.workers.busca_worker import PIPELINE_VERSAO


class WorkerWindows(SimpleWorker):
    """SimpleWorker com timeout baseado em timer (funciona sem sinais Unix)."""

    death_penalty_class = TimerDeathPenalty


if __name__ == "__main__":
    conexao = Redis.from_url(settings.REDIS_URL)
    fila = Queue("coleta", connection=conexao)
    logger.info(
        f"Worker iniciado (pipeline v{PIPELINE_VERSAO}) — aguardando jobs na fila 'coleta' (Ctrl+C para parar)"
    )
    WorkerWindows([fila], connection=conexao).work()
