# Contrato da API — Deeper (MVP para a FSB Holding)

Base URL local: `http://localhost:8000` · Docs interativas: `http://localhost:8000/docs`
CORS: aberto (`*`) — qualquer origem pode chamar a API no MVP.
Auth: NENHUMA no MVP (JWT planejado; não construir telas de login ainda).

Atualizado em 29/09/2026 a partir do código (pipeline `2026-09-28.3`). 16 rotas.

## Visão geral

| Rota | Função |
|---|---|
| `GET /sugestoes` | Candidatos do acervo + LinkedIn; detecta URL colada |
| `POST /busca` | Inicia coleta → `job_id` (ou `cache_hit`) |
| `GET /job/{id}` | Status da coleta |
| `GET /perfil/{id}` | Dossiê completo |
| `DELETE /perfil/{id}` | Exclui a pessoa e dados derivados |
| `POST /perfil/{id}/fundir` | Funde dois registros da mesma pessoa |
| `POST /perfil/{id}/desvincular` | "Não é esta pessoa": remove o que veio do LinkedIn |
| `POST /perfil/{id}/foto` | Troca a foto por URL de imagem |
| `POST /perfil/{id}/foto/arquivo` | Troca a foto por arquivo enviado |
| `GET /grafo/{id}` | Nós, arestas com evidências e layout salvo |
| `POST /grafo/relacao` | Cria conexão manual |
| `PATCH /grafo/relacao/{id}` | Anota ou oculta uma conexão |
| `PUT /grafo/{id}/layout` | Salva ou limpa a disposição do grafo |
| `GET /acervo` | Lista os executivos já pesquisados |
| `GET /foto/{id}` | Imagem da foto guardada localmente |
| `GET /` | Health check |

Erros seguem o padrão do FastAPI: `{"detail": "mensagem legível"}`. Os `detail` de 400/422 foram escritos para o analista e podem ser exibidos direto na interface.

## Fluxo principal do frontend (seleção obrigatória)

```
1. GET  /sugestoes?q=...&externas=true  → usuário ESCOLHE a pessoa (obrigatório p/ pessoa nova)
2. POST /busca {nome, linkedin_url}     → job_id (ou cache_hit=true com pessoa_id direto)
   ⚠ pessoa nova SEM linkedin_url → 422 (busca livre desabilitada)
   ⚠ nó do grafo sem identidade → 422, a menos que venha linkedin_url OU somente_imprensa=true
3. GET  /job/{job_id}                   → polling 2-3s até "done" (~20-60s) | "failed" traz .erro
4. GET  /perfil/{pessoa_id}             → dossiê completo
5. GET  /grafo/{pessoa_id}              → nós e arestas COM evidências (Cytoscape.js)
```

Fluxos de correção de identidade:

```
Perfil errado (homônimo):  POST /perfil/{id}/desvincular → escolher outro LinkedIn (POST /busca com linkedin_url)
                           ou coletar só pela imprensa (POST /busca com somente_imprensa=true)
Dossiê só imprensa que ganhou LinkedIn: POST /busca {nome, linkedin_url} → enriquece, preserva matérias e conexões
```

---

## GET /sugestoes

Parâmetros:
- `q` (2 a 300 chars): nome, cargo, empresa **ou um link de perfil do LinkedIn**.
- `externas=false` (default): só o acervo local — grátis, pode chamar com debounce.
- `externas=true`: também busca candidatos no LinkedIn — custa 1 busca SerpAPI, chamar apenas em ação explícita (Enter/botão).
- `contexto` (opcional, até 160 chars): pistas de identidade vindas do grafo (o descritor da imprensa). **Presente, mesmo vazio, significa "pessoa vinda do grafo"**: a busca no LinkedIn usa o nome entre aspas, só devolve quem tem o mesmo primeiro nome e último sobrenome, e as pistas apenas ordenam os candidatos. Lista vazia é resposta legítima (muito executivo não tem perfil público indexado) e o frontend deve oferecer "coletar só pela imprensa".

Response `200`:
```json
{
  "por_url": false,
  "locais": [
    { "pessoa_id": 1, "nome": "Eduardo Bartolomeo", "cargo_atual": "Membro do conselho — Boston Metal",
      "foto_url": "http://localhost:8000/foto/1?v=1790600000", "tem_briefing": true,
      "contexto_origem": null, "identidade_confirmada": true, "tem_linkedin": true }
  ],
  "linkedin": [
    { "nome": "Eduardo Bartolomeo", "headline": "Board Member — Boston Metal",
      "linkedin_url": "https://br.linkedin.com/in/eduardobartolomeo" }
  ]
}
```
- `por_url: true` quando `q` contém um link de perfil. Se alguém do acervo já tem esse perfil, ele vem em `locais` e `linkedin` fica vazio (sem custo); senão `linkedin` traz um único candidato resolvido (1 busca SerpAPI; se a busca falhar, o nome é derivado do endereço).
- `headline` pode ser `null`.

Clicar num item `linkedin` → `POST /busca` com `nome` + `linkedin_url` (identidade confirmada).
Clicar num item `locais` → `POST /busca` só com `nome` (pessoa já existe; cache/recoleta normais).
Item `locais` com `identidade_confirmada: false` e `tem_linkedin: false` é um nó do grafo: precisa de LinkedIn escolhido ou `somente_imprensa`.

---

## POST /busca

Inicia (ou recupera do cache) a coleta de um executivo.

Request body:
```json
{ "nome": "Eduardo Bartolomeo", "linkedin_url": "https://br.linkedin.com/in/eduardobartolomeo",
  "force_refresh": false, "somente_imprensa": false }
```
- `nome` (obrigatório, min 2): usado para gerar o slug. Apelidos criados por fusão são respeitados ("Dani Braun" abre a "Daniela Braun").
- `linkedin_url` (opcional): perfil confirmado pelo usuário. É normalizado para a forma canônica (`https://…linkedin.com/in/usuario`); endereço inválido → `422`.
- `force_refresh`: `true` ignora o cache de 7 dias e recoleta.
- `somente_imprensa`: para nó do grafo sem LinkedIn público, o analista confirma que o dossiê sai só das matérias.

Regras de identidade:
- Pessoa nova sem `linkedin_url` → `422`.
- Nó do grafo sem identidade, sem `linkedin_url` e sem `somente_imprensa` → `422`.
- Pessoa que **já tinha** LinkedIn recebe outro → é homônimo: o dossiê anterior é zerado e recoletado.
- Nó do grafo que **nunca teve** LinkedIn recebe um → enriquecimento: matérias e conexões são preservadas.

Response `200`:
```json
{
  "job_id": 15,          // null quando cache_hit=true
  "pessoa_id": 1,        // null quando a pessoa ainda não existe
  "cache_hit": false,
  "mensagem": "Coleta de 'Eduardo Bartolomeo' enfileirada. Acompanhe em GET /job/15."
}
```
- `cache_hit: true` → pular direto para `GET /perfil/{pessoa_id}` (sem polling).
- `cache_hit: false` → fazer polling em `GET /job/{job_id}`.
- Redis fora do ar: `200` com `job_id`, mas o job já nasce `failed` e a `mensagem` explica.

## GET /job/{job_id}

Response `200`:
```json
{
  "id": 15,
  "termo_busca": "Eduardo Bartolomeo",
  "status": "done",          // "queued" | "running" | "done" | "failed"
  "pessoa_id": 1,            // preenchido quando o worker inicia
  "iniciado_em": "2026-07-12T04:12:10.123456+00:00",
  "finalizado_em": "2026-07-12T04:13:08.987654+00:00",
  "erro": null               // string com a causa quando status="failed"
}
```
`404` se o job não existe.

---

## GET /perfil/{pessoa_id}?limite_mencoes=50

Dossiê completo (`limite_mencoes` de 1 a 200). Ver `exemplo_perfil.json` (resposta real, pode estar sem os campos mais novos). Estrutura:

```
{
  pessoa: {
    id, slug, nome, nome_completo, cargo_atual, bio,
    linkedin_url, foto_url,
    identidade_confirmada,     // bool
    contexto_origem,           // descritor da imprensa quando nasceu de co-menção ("CFO da Vale") ou null
    atualizado_em
  },
  briefing: string | null,     // 3 parágrafos gerados por LLM (\n\n separa parágrafos)
  cargos: [                    // histórico profissional do LinkedIn; atuais primeiro, depois mais recentes
    { funcao, empresa, empresa_id, inicio, fim, eh_atual }
  ],                           // inicio/fim: "AAAA-MM-DD" ou null
  mencoes: [                   // imprensa; mais recentes primeiro
    { id, fonte, url, titulo, data_publicacao, sentimento, temas,
      trecho,                  // até 600 caracteres do texto coletado, ou null
      trecho_truncado,         // bool: o texto coletado era maior que o trecho
      papel }                  // "autor" = matéria assinada pela pessoa; senão null
  ],                           // sentimento: -1.0 a +1.0 ou null (não processada ainda)
                               // temas: lista de strings
  eventos: [
    { id, nome, tipo, data, local, fonte_url }   // tipo/data/local frequentemente null no MVP
  ]
}
```
- `foto_url` aponta para a cópia local (`/foto/{id}?v=…`) quando existe; sem cópia, é a URL original, que pode ter expirado.
- O `trecho` é deliberadamente curto: citação para contexto, não substituto da matéria (a ferramenta não contorna paywall).
- Menção com `papel: "autor"` não gera conexões e fica fora do sentimento do briefing.
- `404` se a pessoa não existe. Campos de pessoa podem ser `null` (perfil ainda não enriquecido).

## DELETE /perfil/{pessoa_id}?limpar_orfaos=true

Remove a pessoa, cargos, menções e relações. Com `limpar_orfaos=true` (default), também remove os nós que só existiam por causa dela. Ação definitiva.

Response `200`: `{ "removido": true, ...resumo do que foi apagado }` · `404` se não existe.

## POST /perfil/{pessoa_id}/fundir

Diz que `duplicada_id` é a mesma pessoa da rota ("Dani Braun" da imprensa = "Daniela Braun" do LinkedIn). As redes se unem e o nome absorvido vira apelido.

Request: `{ "duplicada_id": 42 }`

Response `200`:
```json
{ "fundido": true, "pessoa_id": 7, "principal": "Daniela Braun", "absorvida": "Dani Braun",
  "cargos": 0, "mencoes": 3, "relacoes": 5, "eventos": 0 }
```
`400` se os dois IDs forem iguais · `404` se algum não existe.

## POST /perfil/{pessoa_id}/desvincular

"Não é esta pessoa?": o LinkedIn coletado é de um homônimo. Apaga cargos, foto e os campos vindos do perfil (`linkedin_url`, `nome_completo`, `cargo_atual`, `bio`, `foto_url`, `localizacao`, `briefing`) e marca `identidade_confirmada = false`. **Preserva menções e conexões** (foram coletadas pelo nome, no contexto certo). Sem body.

Response `200`:
```json
{ "desvinculado": true, "pessoa_id": 7, "contexto_origem": "CFO da Vale" }
```
Em seguida o frontend oferece escolher o perfil certo (`/sugestoes` com `contexto`) ou coletar só pela imprensa. `404` se não existe.

## POST /perfil/{pessoa_id}/foto

Troca a foto por uma imagem da web escolhida pelo analista. A imagem é baixada na hora e guardada localmente.

Request: `{ "url": "https://exemplo.com/retrato.jpg" }` (8 a 2048 chars, `http(s)`)

Response `200`: `{ "foto_url": "http://localhost:8000/foto/7?v=1790687682" }`
`400` se o endereço não é http(s) ou não é uma imagem baixável (o `detail` orienta a usar "Copiar endereço da imagem", não o da página) · `404` se a pessoa não existe.

## POST /perfil/{pessoa_id}/foto/arquivo

Troca a foto por um arquivo do computador do analista. `multipart/form-data` com o campo `arquivo`. Aceita JPG, PNG ou WEBP de até 5 MB.

Response `200`: `{ "foto_url": "http://localhost:8000/foto/7?v=1790687682" }` · `400` fora do formato ou tamanho · `404`.

## GET /foto/{pessoa_id}

Serve a cópia local da foto (a URL do LinkedIn expira; esta não). `Cache-Control: max-age=86400`; o parâmetro `?v=` que vem em `foto_url` muda quando a foto é trocada, para furar o cache do navegador. `404` se não há foto guardada: o frontend mostra as iniciais.

---

## GET /grafo/{pessoa_id}?profundidade=2&peso_minimo=1

Rede de conexões, formato pronto para Cytoscape.js. Ver `exemplo_grafo.json`.

- `profundidade`: 1 a 3 saltos a partir da raiz (default 2).
- `peso_minimo`: filtra arestas fracas (default 1).
- A expansão só caminha por arestas visíveis (peso suficiente e não ocultas): todo nó devolvido tem caminho até a raiz.

```json
{
  "nodes": [
    { "id": 1, "label": "Eduardo Bartolomeo", "cargo_atual": "Membro do conselho — Boston Metal",
      "foto_url": "http://localhost:8000/foto/1?v=1790600000", "raiz": true,
      "contexto_origem": null, "identidade_confirmada": true, "tem_dossie": true },
    { "id": 7, "label": "Gustavo Pimenta", "cargo_atual": null, "foto_url": null, "raiz": false,
      "contexto_origem": "CEO da Vale", "identidade_confirmada": false, "tem_dossie": false }
  ],
  "edges": [
    { "id": 31, "source": 1, "target": 7, "tipo": "co_mencionado", "peso": 2,
      "evidencias": [
        { "mencao_url": "https://valor.globo.com/...", "titulo": "Matéria X", "contexto": "direta", "data": "2026-08-01" },
        { "mencao_url": "https://exame.com/50-mais", "titulo": "50 mais ricos", "contexto": "lista", "data": null }
      ],
      "rotulo": null, "nota": null, "anotado_em": null },
    { "id": 32, "source": 1, "target": 7, "tipo": "colega_empresa", "peso": 1,
      "evidencias": [ { "fonte": "linkedin_cargos", "empresa": "Vale", "funcao_a": "Diretor Presidente", "funcao_b": "CFO" } ],
      "rotulo": "ex-colegas", "nota": "Trabalharam juntos na diretoria", "anotado_em": "2026-09-20T14:02:11+00:00" }
  ],
  "layout": { "1": { "x": 0, "y": 0 }, "7": { "x": 140.5, "y": -80.2 } }
}
```
- `tipo` das arestas: `co_mencionado` (imprensa), `colega_empresa` e `co_board` (períodos sobrepostos no LinkedIn), `co_evento`, `manual` (registrada pelo analista). Pode haver MAIS DE UMA aresta entre o mesmo par (tipos diferentes).
- `id` da aresta: necessário para `PATCH /grafo/relacao/{id}`.
- `peso`: nº de evidências (espessura).
- `evidencias` (máx. 8, mais recentes):
  - `co_mencionado` → `{mencao_url, titulo, contexto, data}`;
  - laços formais → `{fonte: "linkedin_cargos", empresa, funcao_a, funcao_b}`;
  - `manual` → `{fonte: "analista", justificativa, registrado_em}`.
- `contexto`: `"direta"` (relação real) ou `"lista"` (citados juntos num ranking — NÃO é conexão genuína; a UI deve deixar isso visível).
- Nó com `identidade_confirmada: false` é só um nome extraído de matéria: a UI mostra borda tracejada e o `contexto_origem`.
- `layout`: posições salvas pelo analista, ou `null` (o frontend calcula o arranjo).
- Arestas ocultas não vêm na resposta.
- `404` se a pessoa não existe.

## POST /grafo/relacao

Conexão que a casa conhece e a imprensa não mostrou. Só entre pessoas já pesquisadas (com dossiê, identidade confirmada ou LinkedIn).

Request:
```json
{ "pessoa_a_id": 1, "pessoa_b_id": 7, "rotulo": "sócios", "nota": "Sócios na holding X desde 2019, segundo o time de conta" }
```
- `rotulo`: 2 a 80 chars · `nota`: mínimo 3 chars, **obrigatória** (é a evidência).
- Se já existe conexão manual entre o par, soma uma evidência e reexibe a aresta se estava oculta.

Response `200`:
```json
{ "id": 40, "pessoa_a_id": 1, "pessoa_b_id": 7, "tipo": "manual", "rotulo": "sócios", "nota": "…", "peso": 1 }
```
`400` se os IDs são iguais ou alguma pessoa ainda não foi pesquisada · `404` se não existe.

## PATCH /grafo/relacao/{relacao_id}

Anota ou oculta uma relação. Todos os campos são opcionais; só o que vier é alterado.

Request:
```json
{ "rotulo": "filho", "nota": "Citado como filho do executivo", "oculta": false }
```
- `rotulo` (até 80) e `nota`: enviados juntos; string vazia limpa.
- `oculta: true` marca como incorreta: some do grafo, mas o registro fica no banco e sobrevive a recoletas.

Response `200`:
```json
{ "id": 31, "rotulo": "filho", "nota": "Citado como filho do executivo", "oculta": false,
  "anotado_em": "2026-09-29T13:10:00+00:00" }
```
`404` se a relação não existe.

## PUT /grafo/{pessoa_id}/layout

Salva a disposição do grafo arrumada pelo analista (compartilhada com toda a equipe).

Request: `{ "posicoes": { "1": { "x": 0, "y": 0 }, "7": { "x": 140.5, "y": -80.2 } } }`
Objeto vazio (`{"posicoes": {}}`) limpa a disposição ("Reorganizar").

Response `200`: `{ "salvo": true, "nos": 2 }` · `404` se a pessoa não existe.

---

## GET /acervo?limite=200

Lista os executivos já pesquisados (aba Acervo), mais recentes primeiro. `limite` de 1 a 1000 (default 200); afeta só esta listagem, nunca o grafo.

```json
{
  "total": 57,
  "exibindo": 57,
  "pessoas": [
    { "pessoa_id": 1, "nome": "Eduardo Bartolomeo", "cargo_atual": "Membro do conselho — Boston Metal",
      "foto_url": "http://localhost:8000/foto/1?v=1790600000", "tem_briefing": true,
      "identidade_confirmada": true, "atualizado_em": "2026-07-12T01:13:07-03:00" }
  ]
}
```
- `total`: contagem real no banco · `exibindo`: tamanho desta página.

## GET /

Health check: `{ "status": "ok", "env": "development", "version": "0.1.0" }`

---

## Notas para o frontend

- Datas em ISO 8601; `atualizado_em` vem com timezone.
- `sentimento` null = menção coletada mas ainda não analisada (a próxima busca retoma as pendentes, mais recentes primeiro).
- Fotos: prefira sempre o `foto_url` devolvido pela API (cópia local, não expira). Mesmo assim mantenha o fallback de iniciais para quem não tem foto.
- `bio` e `briefing` usam `\n\n` entre parágrafos.
- Polling: jobs levam ~20–60s; status `failed` traz `erro` legível para exibir.
- Chamadas que gastam SerpAPI: `/sugestoes` com `externas=true` e coletas novas. Não dispare em debounce.
