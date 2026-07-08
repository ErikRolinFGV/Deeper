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

**Coleta.** Combinação de serviços terceirizados (Apify para LinkedIn) e APIs oficiais/abertas (SerpAPI, GDELT, B3, Receita Federal). Cada coletor é um módulo independente em `app/services/collectors/`. O coletor principal (SerpAPI) restringe buscas aos 10 maiores portais de imprensa BR, filtra páginas-índice/comentários e extrai data de publicação da própria URL. *(Crunchbase foi cortado do escopo em jul/2026: a API deixou de ter acesso self-service e exige licença enterprise.)*

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
├── scripts/
│   ├── checar_instalacao.bat      # Diagnóstico: Postgres/Memurai instalados e rodando?
│   ├── checar_ambiente.py         # Valida .env, banco, Redis e chaves de API
│   └── rodar_worker.py            # Runner do worker compatível com Windows
└── tests/                         # 26 testes (pytest, sem dependências externas)
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

## Fluxo de uma busca completa (implementado)

1. Analista da FSB envia `POST /busca` com `{"nome": "Eduardo Bartolomeo"}` (ou `force_refresh: true` para ignorar o cache).
2. API verifica se perfil existe em cache fresco (< 7 dias). Se sim, retorna `{pessoa_id, cache_hit: true}` direto.
3. Caso contrário, cria `JobColeta` e enfileira no Redis. Retorna `{job_id}`.
4. Worker localiza/cria a `Pessoa`, coleta menções via SerpAPI (deduplicadas por URL) e, se houver URL de LinkedIn cadastrada, enriquece o perfil via Apify.
5. **Higienização** (sem custo de LLM): menções antigas de páginas-índice são removidas e datas nulas preenchidas a partir da URL.
6. **Auto-recuperação**: menções de execuções anteriores que ficaram sem extração (falha de API no meio do job) voltam para a fila de processamento.
7. Cada menção passa pelo *extrator LLM* (tool_use com schema forçado): sentimento, temas, empresas, eventos públicos nomeados, pessoas co-mencionadas e cargo do alvo.
8. Persistência: `Mencao`, `Empresa`, `Evento` populados; para cada pessoa co-mencionada, aresta em `Relacao` criada ou reforçada (+1 peso, evidência anexada).
9. *Sintetizador LLM* gera o briefing executivo de 3 parágrafos em português, instruído a não especular além dos dados.
10. Job marcado como `done` com `pessoa_id` preenchido. Cliente consulta `GET /perfil/{pessoa_id}` e `GET /grafo/{pessoa_id}`.

Custo por busca nova: ~10 chamadas de extração + 1 de síntese (centavos de dólar). Cada coletor e chamada LLM é tolerante a falha — fonte fora do ar degrada o dossiê, não derruba o job.

---

## API keys necessárias

Todas configuradas via `.env` na raiz do projeto.

| Variável                | Serviço                           | Custo aproximado            |
|-------------------------|-----------------------------------|-----------------------------|
| `ANTHROPIC_API_KEY`     | Claude Sonnet                     | centavos por busca nova     |
| `APIFY_TOKEN`           | Scraping LinkedIn                 | free tier ($5/mês) cobre testes; ~$3–10 por 1000 perfis |
| `SERPAPI_KEY`           | Google search programático        | free tier (250 buscas/mês) cobre testes |
| `CRUNCHBASE_API_KEY`    | *(fora do escopo — usar valor placeholder)* | —          |
| `JWT_SECRET`            | Auth interna (gerar com `secrets.token_urlsafe`) | —          |
| `DATABASE_URL`          | Postgres local                    | —                           |
| `REDIS_URL`             | Redis local (Memurai no Windows)  | —                           |

GDELT, B3 e Receita são gratuitos e não exigem chave. Fase de testes: praticamente só o crédito Anthropic (US$ 5–10). **Atenção**: se a senha do Postgres tiver caracteres especiais (`@`, `$`, `%`...), escreva-os URL-encoded no `DATABASE_URL` (ex: `@` → `%40`, `$` → `%24`).

---

## Setup local (Windows)

Pré-requisitos:

- Python 3.11+ instalado
- PostgreSQL 16 rodando localmente
- Redis rodando localmente (via WSL, Memurai ou similar)
- Contas criadas nos serviços listados acima

Passos:

```powershell
# Criar virtualenv e instalar dependências
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Configurar variáveis de ambiente
copy .env.example .env
# Editar .env com as chaves reais

# Conferir instalação de Postgres/Memurai (duplo clique também funciona)
scripts\checar_instalacao.bat

# Criar banco (no SQL Shell/psql): CREATE DATABASE fsb_executive_intelligence;

# Validar ambiente completo: banco, Redis e as 3 chaves de API
python scripts\checar_ambiente.py

# Criar as tabelas
alembic upgrade head

# Terminal 1 — API
uvicorn app.main:app --reload

# Terminal 2 — worker (runner próprio: o `rq worker` padrão usa fork e não roda no Windows)
python scripts\rodar_worker.py
```

API disponível em `http://localhost:8000`. Documentação interativa (Swagger) em `/docs`.

### Regras de operação

- **Mudou código → reinicie o worker** (Ctrl+C e rodar de novo). O `--reload` do uvicorn só cobre a API; o worker não se atualiza sozinho.
- **Um worker por vez.** Workers antigos esquecidos em outros terminais disputam a fila e processam jobs com código velho. O worker loga `pipeline vAAAA-MM-DD.N` no startup e em cada job — se a versão no log não bater com o código, há processo velho vivo.
- Rodar os testes: `pytest` (26 testes; usam SQLite em memória e mocks — não gastam API nem exigem Postgres/Redis).

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

Backend em **~80%**, validado ponta a ponta com buscas reais (dossiês de executivos da Vale e Coca-Cola gerados com sucesso).

- [x] Arquitetura definida, stack escolhida, decisões registradas
- [x] Ambiente local validado (Postgres 18, Memurai, chaves de API — ver `scripts/checar_ambiente.py`)
- [x] Modelo de dados implementado (SQLAlchemy + Alembic, migration inicial `36e4c0a56103`)
- [x] API FastAPI funcional (`/busca`, `/perfil`, `/grafo`, `/job`)
- [x] Worker RQ orquestrando o pipeline completo (coleta → extração → persistência → grafo → briefing), com higienização e auto-recuperação
- [x] Camada LLM: extrator estruturado (tool_use) e sintetizador de briefing prontos
- [x] Coletor SerpAPI completo (10 portais BR, filtros de índice, datas via URL); Apify parcial (exige URL de LinkedIn conhecida)
- [x] 26 testes automatizados (API + worker + coletor)
- [ ] Coletores GDELT, B3 e Receita (stubs) — Crunchbase cortado do escopo
- [ ] Inferidor de relações LLM (stub); grafo hoje usa só co-menção
- [ ] Autenticação JWT plugada nos endpoints (módulo pronto em `core/security.py`, não aplicado)
- [ ] Frontend (definição posterior)

---

## Próximos passos

1. **Frontend demonstrável** — busca + dossiê + grafo visual (Cytoscape.js); o backend já entrega JSON pronto para renderização. Candidato a próxima sessão.
2. Coletores adicionais: GDELT (eventos globais), BrasilAPI/Receita (quadros societários — conexões formais para o grafo), B3.
3. Inferidor de relações LLM para qualificar arestas além da co-menção.
4. Descoberta automática de URL de LinkedIn pelo nome (destrava o enriquecimento via Apify).
5. Plugar autenticação JWT nos endpoints antes de qualquer demo externa.

---

**Projeto desenvolvido por Erik Rolin (FGV ECMI — Comunicação) para a FSB Holding.**
