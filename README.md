# FSB Executive Intelligence — MVP

Plataforma de inteligência sobre executivos brasileiros desenvolvida como MVP para a **FSB Holding** (comunicação e dados). A ferramenta permite que analistas da FSB busquem nomes de figuras corporativas relevantes (CEOs, presidentes, board members) e recebam um dossiê estruturado com informações públicas: trajetória profissional, presença na imprensa, eventos onde apareceu, conexões com outros executivos e síntese executiva gerada por IA.

---

## Objetivo

Reduzir o tempo que um analista da FSB gasta pesquisando manualmente sobre um executivo — passando de horas de busca em Google, LinkedIn e portais de notícias para minutos de leitura de um perfil consolidado. O diferencial está em três pontos:

1. **Cobertura ampla por agregação** — múltiplas fontes públicas combinadas em vez de scraping bruto.
2. **Inteligência por LLM** — extração de entidades, síntese e inferência de relações feita pela API da Anthropic.
3. **Grafo de conexões inferido** — relações entre executivos descobertas por co-ocorrência em mídia, eventos e cargos compartilhados, não por scraping de redes sociais.

---

## Arquitetura

O backend está dividido em cinco camadas com responsabilidades isoladas:

```
[Coleta] → [Processamento LLM] → [Armazenamento] → [API] → [Frontend]
```

**Coleta.** Combinação de serviços terceirizados (Apify para LinkedIn) e APIs oficiais/abertas (Crunchbase, SerpAPI, GDELT, B3, Receita Federal). Cada coletor é um módulo independente em `app/services/collectors/`.

**Processamento.** Três tipos de chamada à API da Anthropic (Claude Sonnet):
- *Extrator estruturado* — texto bruto vira JSON validado por Pydantic.
- *Sintetizador de perfil* — agrega fragmentos em um briefing executivo em português.
- *Inferidor de relações* — identifica vínculos entre pessoas a partir de contexto.

**Armazenamento.** PostgreSQL como banco principal, com `pgvector` habilitado desde o início para busca semântica futura. Redis para cache de buscas (TTL de 7 dias) e fila de jobs.

**API.** FastAPI com Pydantic v2. Jobs de coleta são assíncronos via RQ (Redis Queue) — endpoint inicial retorna `job_id` e cliente faz polling de status.

**Frontend.** Definido em fase posterior. O backend expõe JSON pronto para consumo por Cytoscape.js (grafo), Recharts ou similar (timelines), e renderização de relatório.

---

## Stack técnica

| Camada           | Ferramenta                                   |
|------------------|----------------------------------------------|
| Linguagem        | Python 3.11+                                 |
| API              | FastAPI + Pydantic v2                        |
| Banco            | PostgreSQL 16 + pgvector                     |
| ORM              | SQLAlchemy 2                                 |
| Migrations       | Alembic                                      |
| Cache/Fila       | Redis + RQ                                   |
| Scraping (cinto) | Playwright                                   |
| LinkedIn         | Apify SDK                                    |
| LLM              | Anthropic SDK (Claude Sonnet)                |
| HTTP             | httpx                                        |
| Auth             | python-jose (JWT)                            |
| Logs             | loguru                                       |
| Testes           | pytest                                       |
| Lint/Format      | ruff + black                                 |

---

## Estrutura de pastas (planejada)

```
CEO_Mais/
├── README.md
├── .env.example
├── requirements.txt
├── alembic.ini
├── app/
│   ├── main.py                    # FastAPI app entrypoint
│   ├── core/
│   │   ├── config.py              # Settings (Pydantic Settings)
│   │   ├── db.py                  # Conexão SQLAlchemy
│   │   └── security.py            # JWT, hashing
│   ├── api/
│   │   ├── busca.py               # POST /busca
│   │   ├── perfil.py              # GET /perfil/{id}
│   │   ├── grafo.py               # GET /grafo/{id}
│   │   └── job.py                 # GET /job/{id}
│   ├── models/                    # SQLAlchemy models
│   │   ├── pessoa.py
│   │   ├── empresa.py
│   │   ├── cargo.py
│   │   ├── relacao.py
│   │   ├── evento.py
│   │   ├── mencao.py
│   │   └── job.py
│   ├── schemas/                   # Pydantic schemas (request/response)
│   ├── services/
│   │   ├── collectors/
│   │   │   ├── apify_linkedin.py
│   │   │   ├── crunchbase.py
│   │   │   ├── serpapi_news.py
│   │   │   ├── gdelt.py
│   │   │   ├── b3.py
│   │   │   └── receita.py
│   │   ├── llm/
│   │   │   ├── extrator.py
│   │   │   ├── sintetizador.py
│   │   │   └── inferidor_relacoes.py
│   │   ├── graph/
│   │   │   ├── construtor.py      # cria arestas a partir de extrações
│   │   │   └── queries.py         # CTEs recursivas
│   │   └── cache.py
│   └── workers/
│       └── busca_worker.py        # Job RQ que orquestra coleta
├── migrations/                    # Alembic versions
└── tests/
```

---

## Modelo de dados (entidades principais)

| Entidade   | Propósito                                                      |
|------------|----------------------------------------------------------------|
| `Pessoa`   | Executivo individual com bio, foto, link LinkedIn              |
| `Empresa`  | Companhia (Vale, Itaú, etc) com setor e identificadores        |
| `Cargo`    | Ponte Pessoa↔Empresa com função, datas, é_atual                |
| `Relacao`  | Aresta Pessoa↔Pessoa com tipo, peso e lista de evidências      |
| `Evento`   | Encontro público (Davos, Lide, Brazil Conference) com data     |
| `Mencao`   | Aparição em mídia: fonte, url, data, texto, sentimento         |
| `JobColeta`| Status de coleta assíncrona (queued, running, done, failed)    |

---

## Fluxo de uma busca completa

1. Analista da FSB envia `POST /busca` com `{"nome": "Eduardo Bartolomeo"}`.
2. API verifica se perfil existe em cache fresco (< 7 dias). Se sim, retorna direto.
3. Caso contrário, cria `JobColeta` e enfileira no Redis. Retorna `{job_id}`.
4. Worker RQ dispara **em paralelo**: Apify (LinkedIn), Crunchbase (carreira), SerpAPI (imprensa BR), GDELT (eventos globais), B3 (se aplicável), Receita (quadros societários).
5. Cada resposta bruta vai para o *extrator LLM*, que devolve JSON estruturado (eventos, empresas, pessoas mencionadas, sentimento, temas).
6. Persistência: `Pessoa`, `Cargo`, `Empresa`, `Evento`, `Mencao` são populados. Para cada pessoa co-mencionada, uma aresta em `Relacao` é criada ou reforçada.
7. Última chamada ao *sintetizador LLM*: gera o briefing executivo em português a partir do conjunto consolidado.
8. Job marcado como `done`. Frontend, que faz polling em `GET /job/{id}`, consulta `GET /perfil/{id}` e `GET /grafo/{id}` para renderização.

---

## API keys necessárias

Todas configuradas via `.env` na raiz do projeto.

| Variável                | Serviço                           | Custo aproximado            |
|-------------------------|-----------------------------------|-----------------------------|
| `ANTHROPIC_API_KEY`     | Claude Sonnet                     | $0.15–0.25 por busca nova   |
| `APIFY_TOKEN`           | Scraping LinkedIn                 | ~$5–10 por 1000 perfis      |
| `SERPAPI_KEY`           | Google search programático        | ~$50 por 5000 buscas        |
| `CRUNCHBASE_API_KEY`    | Carreira corporativa              | ~$49/mês plano básico       |
| `JWT_SECRET`            | Auth interna                      | —                           |
| `DATABASE_URL`          | Postgres local                    | —                           |
| `REDIS_URL`             | Redis local                       | —                           |

GDELT, B3 e Receita são gratuitos. Estimativa total para uso de MVP demonstrável: **$50–100/mês**, cobrindo dezenas de buscas únicas por dia (cache derruba custo de buscas repetidas).

---

## Setup local (Windows)

Pré-requisitos:

- Python 3.11+ instalado
- PostgreSQL 16 rodando localmente
- Redis rodando localmente (via WSL, Memurai ou similar)
- Contas criadas nos serviços listados acima

Passos resumidos (a serem detalhados quando o projeto for criado):

```bash
# Criar virtualenv e instalar dependências
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Configurar variáveis de ambiente
copy .env.example .env
# Editar .env com as chaves reais

# Criar banco e rodar migrations
createdb fsb_executive_intelligence
alembic upgrade head

# Subir servidor FastAPI
uvicorn app.main:app --reload

# Em outro terminal, subir worker
rq worker --url $REDIS_URL
```

API disponível em `http://localhost:8000`. Documentação automática em `/docs`.

---

## Decisões arquiteturais registradas

- **Sem scraping direto agressivo do LinkedIn.** Risco legal e reputacional para a FSB. Apify gerencia o risco técnico e a operação.
- **LLM desde o MVP.** Custo absorvível e qualidade dos relatórios justifica.
- **Grafo construído por co-ocorrência**, não por scraping de rede social. Mais ético, mais defensável, e tecnicamente mais interessante.
- **Postgres em vez de Neo4j** para o grafo. CTEs recursivas atendem o volume de MVP sem operar um segundo banco.
- **Local-first** para desenvolvimento. Docker será adicionado quando o escopo for validado com a FSB.
- **RQ em vez de Celery** para jobs. Simplicidade vence em fase de MVP.

---

## Status atual

- [x] Arquitetura definida
- [x] Stack escolhida
- [x] Decisões registradas
- [x] Estrutura de pastas criada
- [x] Modelo de dados implementado (SQLAlchemy + Alembic, migration inicial `36e4c0a56103`)
- [ ] Coletores implementados (SerpAPI pronto; Apify parcial; Crunchbase, GDELT, B3 e Receita são stubs)
- [ ] Camada LLM implementada (extrator pronto; sintetizador e inferidor de relações são stubs)
- [x] API FastAPI funcional (`/busca`, `/perfil`, `/grafo`, `/job` implementados e testados)
- [ ] Worker RQ orquestrando jobs (esqueleto criado; pipeline de coleta é TODO)
- [ ] Frontend (definição posterior)

---

## Próximos passos imediatos

1. Revisão deste README com Erik e, em seguida, com a equipe FSB.
2. Estruturação inicial de pastas e `requirements.txt`.
3. Configuração das contas nos serviços externos.
4. Implementação iterativa por camada, começando pelos modelos de dados.

---

**Projeto desenvolvido por Erik Rolin (FGV ECMI — Comunicação) para a FSB Holding.**
