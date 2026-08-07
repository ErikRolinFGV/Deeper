# Deeper — Inteligência sobre executivos · MVP para a FSB Holding

Ferramenta interna onde analistas de comunicação pesquisam executivos brasileiros e recebem, em cerca de um minuto, um dossiê estruturado a partir de fontes públicas: trajetória profissional, presença na imprensa com análise de sentimento, eventos, briefing executivo gerado por IA e um **grafo de conexões** com evidência anexada em cada relação.

O produto responde a quatro perguntas que um analista precisa responder antes de qualquer aproximação:

> **Quem é, o que pensa, como a mídia o trata, e quem ele conhece.**

---

## Sumário

- [Estado do projeto](#estado-do-projeto)
- [O que a ferramenta faz](#o-que-a-ferramenta-faz)
- [Arquitetura](#arquitetura)
- [O pipeline de uma busca](#o-pipeline-de-uma-busca)
- [O grafo de conexões](#o-grafo-de-conexões)
- [Curadoria humana](#curadoria-humana)
- [Salvaguardas de qualidade](#salvaguardas-de-qualidade)
- [Stack técnica](#stack-técnica)
- [Modelo de dados](#modelo-de-dados)
- [API](#api)
- [Estrutura de pastas](#estrutura-de-pastas)
- [Setup local (Windows)](#setup-local-windows)
- [Regras de operação](#regras-de-operação)
- [Custos](#custos)
- [Legalidade e ética](#legalidade-e-ética)
- [Decisões arquiteturais](#decisões-arquiteturais)
- [Limitações conhecidas](#limitações-conhecidas)
- [Roadmap](#roadmap)

---

## Estado do projeto

**MVP funcional, validado com dados reais.** Roda localmente (API + worker + banco na máquina do desenvolvedor); ainda não há deploy, autenticação ou multiusuário — ver [Limitações](#limitações-conhecidas).

| | |
|---|---|
| Pipeline | Completo, ponta a ponta, com dossiês reais no banco |
| Frontend | `frontend/index_v2.html` — arquivo único, integrado à API |
| Testes | **125 automatizados** (`pytest`, sem tocar em API externa) |
| Migrations | 7 (última: `e5f6a7b8c9d0`) |
| Versão do pipeline | Logada no startup do worker (`PIPELINE_VERSAO`) |

---

## O que a ferramenta faz

**Busca com identidade confirmada.** O analista digita nome, cargo, empresa (*"CEO do Nubank"*) ou cola um link do LinkedIn. A busca **não** dispara coleta: abre um painel com quem já está no acervo e candidatos reais do LinkedIn, com headline de cada um. A coleta só começa quando o analista aponta quem é — é o que impede que o dossiê nasça de um homônimo.

**Dossiê.** Ficha (foto, cargo atual, localização, LinkedIn), briefing executivo de 3 parágrafos, trajetória profissional em linha do tempo, menções na imprensa com sentimento (−1 a +1) e temas, eventos, e o grafo.

**Grafo de conexões.** Rede navegável com tipos de vínculo distintos, evidências clicáveis, filtros de profundidade e peso, modo tela cheia e navegação de pessoa para pessoa.

**Acervo compartilhado.** Todo dossiê fica disponível para a equipe por 7 dias sem custo de nova coleta. O grafo cresce conforme a equipe pesquisa.

---

## Arquitetura

```
┌──────────────┐   HTTP    ┌──────────────┐         ┌─────────────┐
│  index_v2    │ ────────▶ │   FastAPI    │ ──────▶ │  PostgreSQL │
│  (browser)   │ ◀──────── │  (11 rotas)  │ ◀────── │             │
└──────────────┘           └──────┬───────┘         └──────▲──────┘
                                  │ enfileira              │
                                  ▼                        │
                           ┌──────────────┐                │
                           │ Redis + RQ   │                │
                           └──────┬───────┘                │
                                  ▼                        │
                        ┌───────────────────┐              │
                        │  busca_worker     │──────────────┘
                        │  (pipeline)       │
                        └─────────┬─────────┘
                                  │
        ┌─────────────┬───────────┼────────────┬──────────────┐
        ▼             ▼           ▼            ▼              ▼
   SerpAPI      leitor_artigo   Apify     Claude Sonnet   inferidor
  (imprensa)    (corpo da       (LinkedIn)  (extração +    formal
                 matéria)                    síntese)     (cargos)
```

A coleta roda **assíncrona**: a API devolve `job_id` e o frontend faz polling. Nenhuma etapa bloqueia a interface, e cada fonte é tolerante a falha — uma fonte fora do ar degrada o dossiê, não derruba o job.

---

## O pipeline de uma busca

1. **Identidade confirmada** — o analista escolhe o perfil do LinkedIn nas sugestões (ou cola a URL). Pessoa nova sem essa confirmação é rejeitada com `422`.
2. **Imprensa** — SerpAPI restrito a 10 portais brasileiros (Valor, Estadão, Folha, O Globo, Exame, InfoMoney, NeoFeed, Veja, IstoÉ Dinheiro, Brazilian Report). Descarta páginas-índice ("Tudo sobre…") e normaliza URLs removendo parâmetros de rastreamento.
3. **Leitura das matérias** — `leitor_artigo` baixa o corpo do texto (até 4.000 caracteres) e extrai a **assinatura**. Manchete de economia raramente cita pessoas; o corpo é onde o grafo nasce.
4. **LinkedIn** — Apify (`apimaestro/linkedin-profile-detail`) traz trajetória, formação, bio, foto e localização. Payload bruto fica em cache por 30 dias.
5. **Extração (IA)** — cada matéria passa por Claude Sonnet com `tool_use`: o modelo preenche um formulário fixo (pessoas relacionadas com descritor, empresas, eventos, valores, temas, sentimento, papel da pessoa no texto, se é lista/ranking). Não escreve texto livre — preenche campos.
6. **Grafo** — co-menções viram arestas; o inferidor formal cruza cargos com períodos sobrepostos.
7. **Síntese (IA)** — só depois de tudo estruturado, o modelo escreve o briefing de 3 parágrafos a partir do **estado completo do banco** (não apenas do lote coletado agora).

Tempo total: **20 a 60 segundos**.

---

## O grafo de conexões

Cada aresta tem tipo, peso (nº de evidências) e a lista de evidências anexada.

| Tipo | Origem | Aparência |
|---|---|---|
| `colega_empresa` / `co_board` | Cargos com períodos sobrepostos no LinkedIn | Laranja sólido |
| `co_mencionado` | Citados na mesma matéria | Cinza |
| `co_evento` | Mesmo evento público | Pontilhado |
| `manual` | Registrada pelo analista | Tracejado turquesa |

**Contexto da evidência.** Toda co-menção é classificada como `direta` ou `lista` — pessoas citadas juntas num ranking ("50 mais ricos") não têm relação genuína. Essas arestas nascem esmaecidas e podem ser filtradas.

**Identidade dos nós.** Quem já foi pesquisado aparece normal; quem é só um nome extraído de matéria aparece com **borda tracejada** e badge "identidade não confirmada", com o descritor que a matéria forneceu ("filho do executivo", "CFO da Vale").

**Legibilidade em escala.** Acima de ~18 nós os nomes só aparecem no que está em foco (hover na vizinhança) ou com zoom — sem isso a tela vira ruído. Os rótulos das relações seguem a mesma regra. O arranjo tem repulsão e comprimento de aresta proporcionais ao tamanho da rede.

**Só o que alcança a raiz.** A expansão caminha apenas por arestas que serão desenhadas (peso mínimo, não ocultas, filtro de listas). Um aglomerado solto na tela sugeriria uma relação que não existe, então nada aparece sem caminho até a pessoa pesquisada.

> A rede de conexões **não** é a lista de contatos do LinkedIn — essa não é pública e não é acessada. É inferida de fatos públicos: trajetórias sobrepostas e matérias que citam as duas pessoas.

---

## Curadoria humana

O grafo não é só resultado de máquina: acumula conhecimento da equipe. Quatro ações do analista, todas persistidas e sobreviventes a recoletas:

| Ação | Onde | Para quê |
|---|---|---|
| **Anotar conexão** | Card da aresta | Rótulo ("filho", "sócio") + observação livre. O rótulo aparece escrito na linha do grafo. |
| **Marcar como incorreta** | Card da aresta | Some do grafo; o registro fica no banco para não ser ressuscitado sem ninguém ver. |
| **Fundir entidades** | Card do nó | "Dani Braun" (imprensa) e "Daniela Braun" (LinkedIn) são a mesma pessoa. As redes se unem e o nome antigo vira **apelido**, para coletas futuras reconhecerem. |
| **Criar conexão** | Botão "+ Conexão" | Vínculo que a casa conhece e a imprensa não mostrou. Exige **justificativa obrigatória** — é a evidência quando não há fonte pública. |
| **Arrumar o grafo** | Arrastar os nós | A disposição é salva no servidor e reaparece igual na próxima abertura, para toda a equipe. "Reorganizar" descarta e recalcula. |

**Excluir perfil** também está na interface (aba Acervo e cabeçalho do dossiê), removendo cargos, menções, relações e, opcionalmente, os nós órfãos que só existiam por causa daquela pessoa.

---

## Salvaguardas de qualidade

Cada uma nasceu de um erro real encontrado em teste:

- **Homônimo na origem** — identidade confirmada por perfil do LinkedIn antes da coleta. Trocar o perfil de alguém que já tinha um confirmado zera o dossiê (é outra pessoa física); confirmar o perfil de um nó que nunca teve **preserva** as conexões (é enriquecimento).
- **Homônimo na imprensa** — o extrator recebe nome + cargo e descarta matérias sobre outra pessoa com o mesmo nome.
- **Matéria assinada pela pessoa** — detecção de byline (meta tags, `rel="author"`, "Por Fulano") + veredito do LLM. Um executivo ex-jornalista não vira "conexão" de todo mundo sobre quem escreveu, e o sentimento dessas matérias fica fora do briefing.
- **Listas e rankings** — rótulo do LLM + heurística de fan-out (6+ pessoas citadas juntas).
- **Datas** — vêm da URL e dos metadados, nunca do texto interpretado (evita registrar data de posse como data da matéria).
- **Higienização automática** — a cada busca: remove páginas-índice, funde matérias duplicadas por parâmetros de rastreamento, preenche datas faltantes.
- **Auto-recuperação** — menções que ficaram sem extração (falha de API no meio) voltam para a fila na próxima busca.
- **Cache de custo** — dossiê 7 dias, perfil do LinkedIn 30 dias.

---

## Stack técnica

| Camada | Ferramenta |
|---|---|
| Linguagem | Python 3.11+ |
| API | FastAPI + Pydantic v2 |
| Banco | PostgreSQL + SQLAlchemy 2 |
| Migrations | Alembic |
| Fila | Redis + RQ |
| LLM | Anthropic SDK (Claude Sonnet) |
| LinkedIn | Apify SDK |
| Busca | SerpAPI |
| Leitura de matérias | httpx + BeautifulSoup |
| Frontend | HTML/CSS/JS em arquivo único + Cytoscape.js |
| Testes | pytest |
| Lint | ruff + black |

---

## Modelo de dados

| Entidade | Propósito |
|---|---|
| `Pessoa` | Executivo. Inclui `contexto_origem` (descritor de quando nasceu de co-menção), `identidade_confirmada`, cache do LinkedIn e `grafo_layout` (disposição salva do grafo) |
| `Empresa` | Companhia |
| `Cargo` | Ponte Pessoa↔Empresa com função, período e `eh_atual` |
| `Relacao` | Aresta Pessoa↔Pessoa: tipo, peso, evidências (JSON), `rotulo`, `nota`, `oculta` |
| `AliasPessoa` | Nomes alternativos criados por fusão de entidades |
| `Evento` | Encontro público, com participantes |
| `Mencao` | Aparição na imprensa: fonte, url, data, texto, sentimento, temas, `papel` |
| `JobColeta` | Status da coleta assíncrona |

---

## API

Documentação interativa em `/docs`. Contrato detalhado em [`frontend/CONTRATO_API.md`](frontend/CONTRATO_API.md).

| Rota | Função |
|---|---|
| `GET /sugestoes?q=&externas=&contexto=` | Candidatos do acervo + LinkedIn. Detecta URL colada |
| `POST /busca` | Inicia coleta (exige `linkedin_url` para pessoa nova) → `job_id` |
| `GET /job/{id}` | Status da coleta (`queued`/`running`/`done`/`failed`) |
| `GET /perfil/{id}` | Dossiê completo |
| `DELETE /perfil/{id}` | Remove a pessoa e dados derivados |
| `POST /perfil/{id}/fundir` | Funde dois registros da mesma pessoa |
| `GET /grafo/{id}?profundidade=&peso_minimo=` | Nós e arestas com evidências |
| `POST /grafo/relacao` | Cria conexão manual (justificativa obrigatória) |
| `PATCH /grafo/relacao/{id}` | Anota ou oculta uma conexão |
| `PUT /grafo/{id}/layout` | Salva (ou limpa) a disposição do grafo arrumada pelo analista |
| `GET /acervo` | Lista os executivos já pesquisados |
| `GET /` | Health check |

---

## Estrutura de pastas

```
CEO_Mais/
├── README.md
├── ROTEIRO_APRESENTACAO_FSB.md     # roteiro da apresentação
├── requirements.txt · alembic.ini · .env
├── app/
│   ├── main.py                     # FastAPI + CORS
│   ├── core/                       # config, db, security
│   ├── api/                        # busca, perfil, grafo, job, sugestoes, acervo
│   ├── models/                     # pessoa, empresa, cargo, relacao, alias, evento, mencao, job
│   ├── schemas/
│   ├── services/
│   │   ├── collectors/             # serpapi_news, apify_linkedin, leitor_artigo
│   │   ├── llm/                    # extrator, sintetizador
│   │   ├── graph/                  # construtor, queries, inferidor_formal
│   │   ├── manutencao.py           # fusão, exclusão, aliases
│   │   └── cache.py
│   └── workers/busca_worker.py     # pipeline completo
├── frontend/
│   ├── index_v2.html               # aplicação (atual)
│   ├── CONTRATO_API.md
│   └── exemplo_*.json
├── migrations/versions/            # 7 migrations
├── scripts/                        # checar_ambiente.py, rodar_worker.py
└── tests/                          # 125 testes
```

---

## Setup local (Windows)

**Pré-requisitos:** Python 3.11+, PostgreSQL, Redis (Memurai no Windows), chaves de API.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env      # editar com as chaves reais

# criar o banco no psql: CREATE DATABASE fsb_executive_intelligence;
python scripts\checar_ambiente.py    # valida banco, Redis e chaves
alembic upgrade head

# Terminal 1 — API
uvicorn app.main:app --reload

# Terminal 2 — worker (runner próprio: o `rq worker` padrão usa fork e não roda no Windows)
python scripts\rodar_worker.py
```

Abra `frontend/index_v2.html` direto no navegador — o CORS está liberado para o MVP.

**Variáveis do `.env`:** `DATABASE_URL`, `REDIS_URL`, `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, `SERPAPI_KEY`, `JWT_SECRET`. Opcionais: `APIFY_ACTOR_LINKEDIN`, `LINKEDIN_TTL_DIAS`, `CACHE_TTL_DAYS`.

> Se a senha do Postgres tiver caracteres especiais (`@`, `$`, `%`), escreva-os URL-encoded no `DATABASE_URL` (`@` → `%40`, `$` → `%24`).

---

## Regras de operação

- **Mudou código do worker → reinicie o worker.** O `--reload` do uvicorn só cobre a API.
- **Um worker por vez.** Workers esquecidos em outros terminais disputam a fila e processam com código velho. O worker loga `pipeline vAAAA-MM-DD.N` no startup e em cada job — se a versão não bater, há processo antigo vivo.
- **Redis parado?** No Windows: `Start-Service Memurai` como administrador.
- **Testes:** `pytest` — 125 testes com SQLite em memória e mocks; não gastam API nem exigem Postgres/Redis.

---

## Custos

**Por dossiê novo: ≈ US$ 0,20** (cerca de R$ 1,00–1,20).

| Item | Custo |
|---|---|
| Perfil do LinkedIn (Apify) | US$ 0,005 |
| IA — ~20 extrações + 1 síntese | ~US$ 0,17 |
| Buscas (SerpAPI) | ~US$ 0,03 |
| Dossiê já no acervo (cache 7 dias) | **zero** |

**Mensal em escala de equipe:** ≈ US$ 125–235 (SerpAPI ~75, Anthropic 20–60, Apify 10–49, servidor 20–50). O gargalo hoje não é dinheiro, é cota: o plano gratuito do SerpAPI (250 buscas/mês) banca ~100 pesquisas novas.

---

## Legalidade e ética

- **Só fontes públicas.** Perfil público do LinkedIn (sem login, sem conta, sem página protegida) e imprensa publicada. Nada de dados sensíveis, contatos ou bases privadas.
- **Jurisprudência.** No caso *hiQ Labs vs. LinkedIn*, a Corte do 9º Circuito (EUA) decidiu que raspar dado público não viola o CFAA; o caso terminou em acordo desfavorável à hiQ por **quebra de termos de uso** e uso de contas falsas. Nossa fronteira é exatamente essa: sem login, sem conta falsa. Zona cinzenta de termos de uso, não risco zero.
- **LGPD.** Dado tornado manifestamente público pelo titular dispensa consentimento (art. 7º, §4º), resguardados os direitos do titular e os princípios da lei. Base legal adequada: legítimo interesse (art. 7º, IX).
- **Paywall.** A ferramenta **não** contorna paywall. Exibe o trecho da versão pública já coletada, com link para a íntegra no veículo. Reproduzir matéria integral é território de licenciamento (clipping).
- **Fluxo internacional.** Os textos vão para a API da Anthropic (EUA) para análise. São dados públicos e a Anthropic não treina com dados de API por padrão — mas o jurídico deve conhecer o fluxo.
- **Recomendações para produção:** política de retenção, canal de exclusão a pedido do titular, uso interno e profissional (sem decisão automatizada sobre a pessoa), e revisão do jurídico da casa.

---

## Decisões arquiteturais

- **Seleção de identidade obrigatória** em vez de busca livre. Adicionar um clique foi decisão consciente: dossiê errado é pior que dossiê nenhum.
- **Extração separada da síntese.** O modelo preenche campos verificáveis antes de escrever qualquer texto — reduz drasticamente o espaço para invenção.
- **Grafo por co-ocorrência e cargos**, nunca por scraping de rede social. Mais ético, mais defensável e auditável.
- **Postgres em vez de Neo4j.** CTEs recursivas atendem o volume do MVP sem operar um segundo banco.
- **Curadoria humana acima de heurística.** Fusão de entidades e conexões manuais colocam a decisão no analista em vez de tentar adivinhar — com marcação visual para nunca confundir conhecimento da casa com fato apurado.
- **RQ em vez de Celery**, **local-first**, **frontend em arquivo único**: simplicidade vence em MVP.

---

## Limitações conhecidas

1. **Não está instalado.** Roda na máquina do desenvolvedor, sem servidor.
2. **Sem autenticação.** Qualquer pessoa com acesso à rede acessaria.
3. **Não monitora.** É fotografia sob demanda, não vigilância contínua com alertas.
4. **Não exporta.** Dá para copiar o briefing; não há PDF/DOCX.
5. **Cobertura nacional.** 10 portais brasileiros — executivo internacional rende pouco.
6. **A rede começa rala.** O grafo engorda conforme a equipe pesquisa.
7. **Briefing gerado por IA.** Tem fonte e a arquitetura reduz muito o risco, mas **recomenda-se revisão humana antes de uso externo**.
8. **Identidade é o nome.** Dois homônimos reais colidem no mesmo registro; mitigado por confirmação humana e fusão, não resolvido na raiz.
9. **Busca sensível a grafia.** Não há correspondência aproximada — a seleção obrigatória mitiga.

---

## Roadmap

**Fase 2 — colocar de pé (4 a 6 semanas)**
1. Deploy em servidor + banco gerenciado
2. Autenticação e perfis de usuário
3. Exportação em PDF/DOCX
4. Log de uso e controle de cota

**Fase 3 — virar rotina (6 a 10 semanas)**
5. Monitoramento contínuo com alertas
6. Cobertura internacional (GDELT)
7. Posts públicos do LinkedIn como sinal de relacionamento
8. Quadros societários e conselhos de companhias abertas
9. Integração com as ferramentas da casa

---

**Projeto desenvolvido por Erik Rolin (FGV ECMI — Comunicação) para a FSB Holding.**
