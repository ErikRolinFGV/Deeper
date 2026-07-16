# Contrato da API — FSB Executive Intelligence (MVP)

Base URL local: `http://localhost:8000` · Docs interativas: `http://localhost:8000/docs`
CORS: aberto (`*`) — qualquer origem pode chamar a API no MVP.

## Fluxo principal do frontend

```
1. POST /busca {nome}            → job_id (ou cache_hit=true com pessoa_id direto)
2. GET  /job/{job_id}            → polling a cada 2-3s até status "done" (leva ~20-60s)
3. GET  /perfil/{pessoa_id}      → dossiê completo (renderizar)
4. GET  /grafo/{pessoa_id}       → nós e arestas (renderizar com Cytoscape.js)
```

---

## POST /busca

Inicia (ou recupera do cache) a coleta de um executivo.

Request body:
```json
{ "nome": "eduardo bartolomeo", "force_refresh": false }
```
- `nome`: mínimo 2 caracteres. ATENÇÃO: typos criam pessoas duplicadas (fuzzy match ainda não implementado) — o frontend deve incentivar confirmação visual do nome.
- `force_refresh`: `true` ignora o cache de 7 dias e recoleta.

Response `200`:
```json
{
  "job_id": 15,          // null quando cache_hit=true
  "pessoa_id": 1,        // null quando a pessoa ainda não existe
  "cache_hit": false,
  "mensagem": "Coleta de 'eduardo bartolomeo' enfileirada. Acompanhe em GET /job/15."
}
```
- `cache_hit: true` → pular direto para `GET /perfil/{pessoa_id}` (sem polling).
- `cache_hit: false` → fazer polling em `GET /job/{job_id}`.

## GET /job/{job_id}

Response `200`:
```json
{
  "id": 15,
  "termo_busca": "eduardo bartolomeo",
  "status": "done",          // "queued" | "running" | "done" | "failed"
  "pessoa_id": 1,            // preenchido quando o worker inicia
  "iniciado_em": "2026-07-12T04:12:10.123456+00:00",
  "finalizado_em": "2026-07-12T04:13:08.987654+00:00",
  "erro": null               // string com a causa quando status="failed"
}
```
`404` se o job não existe.

## GET /perfil/{pessoa_id}?limite_mencoes=50

Dossiê completo. Ver `exemplo_perfil.json` (resposta REAL do banco, executivo Eduardo Bartolomeo). Estrutura:

```
{
  pessoa: {
    id, slug, nome, nome_completo, cargo_atual, bio,
    linkedin_url, foto_url, atualizado_em
  },
  briefing: string | null,     // 3 parágrafos gerados por LLM (texto corrido, \n\n separa parágrafos)
  cargos: [                    // histórico profissional do LinkedIn; atuais primeiro
    { funcao, empresa, empresa_id, inicio, fim, eh_atual }
  ],                           // inicio/fim: "AAAA-MM-DD" ou null
  mencoes: [                   // notícias da imprensa BR; mais recentes primeiro
    { id, fonte, url, titulo, data_publicacao, sentimento, temas }
  ],                           // sentimento: -1.0 a +1.0 ou null (não processada ainda)
                               // fonte: "valor" | "estadao" | "oglobo" | "exame" | "infomoney" | "outros"...
  eventos: [
    { id, nome, tipo, data, local, fonte_url }   // tipo/data/local frequentemente null no MVP
  ]
}
```
`404` se a pessoa não existe. Campos de pessoa podem ser `null` (perfil ainda não enriquecido).

## GET /grafo/{pessoa_id}?profundidade=2&peso_minimo=1

Rede de conexões, formato pronto para Cytoscape.js. Ver `exemplo_grafo.json`.

- `profundidade`: 1 a 3 saltos a partir da raiz (default 2).
- `peso_minimo`: filtra arestas fracas (default 1; aumentar para redes densas).

```json
{
  "nodes": [
    { "id": 1, "label": "Eduardo Bartolomeo", "cargo_atual": "Membro do conselho de administração — Boston Metal", "foto_url": "https://...", "raiz": true },
    { "id": 7, "label": "Eliezer Batista", "cargo_atual": null, "foto_url": null, "raiz": false }
  ],
  "edges": [
    { "source": 1, "target": 7, "tipo": "co_mencionado", "peso": 2 }
  ]
}
```
- `tipo` das arestas: `co_mencionado` (citados na mesma matéria) — futuramente `co_evento`, `co_board`.
- `peso`: nº de evidências; usar na espessura da aresta.
- `404` se a pessoa não existe.

## GET /

Health check: `{ "status": "ok", "env": "development", "version": "0.1.0" }`

---

## Notas para o frontend

- Datas em ISO 8601; `atualizado_em` vem com timezone.
- `sentimento` null = menção coletada mas ainda não analisada (um novo force_refresh recupera).
- `foto_url` do LinkedIn expira (parâmetro `e=` na URL) — sempre ter fallback de avatar.
- `bio` e `briefing` usam `\n\n` entre parágrafos.
- Polling: jobs levam 20–60s; status `failed` traz `erro` legível para exibir.
- Auth: NENHUMA no MVP (JWT planejado; não construir telas de login ainda).
