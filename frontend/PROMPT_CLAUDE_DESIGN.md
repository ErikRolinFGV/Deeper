# Prompt pronto para o Claude design

Como usar: abra o Claude design, cole o prompt abaixo e **anexe os 2 arquivos JSON** desta pasta (`exemplo_perfil.json` e `exemplo_grafo.json`). Itere no visual lá; a integração com a API real fica para quando o protótipo voltar para cá.

---

## PROMPT (copiar daqui para baixo)

Crie um protótipo de frontend em um único arquivo HTML (CSS e JS inline) para o "FSB Executive Intelligence" — uma ferramenta interna da FSB Holding (maior agência de comunicação corporativa do Brasil) onde analistas pesquisam executivos brasileiros e recebem um dossiê de inteligência gerado a partir de fontes públicas (LinkedIn + imprensa).

**Usuário-alvo:** analista de comunicação preparando aproximação com um executivo. Precisa responder rápido: quem é, o que pensa, como a mídia o trata, quem ele conhece.

**Dados:** anexei dois JSONs REAIS da API. Use-os como mock embutido no protótipo (hardcode). Não invente campos que não existem neles; trate campos null com elegância (ex.: datas ausentes, foto quebrada → avatar com iniciais).

**Três estados de tela:**

1. **Busca** — campo único centralizado ("Pesquisar executivo..."), logo/título discreto. Ao buscar, transição para o estado de carregamento.

2. **Carregando** — a coleta real leva 20–60s. Mostre progresso com etapas nomeadas ("Buscando na imprensa...", "Coletando LinkedIn...", "Analisando com IA...", "Montando grafo...") avançando de forma simulada. É a tela que vende a mágica — capriche.

3. **Dossiê** — o coração. Layout sugerido:
   - Cabeçalho: foto, nome, cargo atual, localização, link para o LinkedIn, data de atualização, botão "Atualizar dossiê".
   - Briefing executivo em destaque: 3 parágrafos de texto (campo `briefing`, parágrafos separados por \n\n). É o produto principal — tipografia generosa.
   - Coluna/seção Trajetória: timeline vertical dos `cargos` (função, empresa, período, badge "atual").
   - Seção Menções na imprensa: cards com título (link), fonte, data e um indicador visual de sentimento (-1 a +1: vermelho→cinza→verde) + chips dos temas.
   - Seção Rede de conexões: grafo interativo com Cytoscape.js (CDN) usando o JSON do grafo — nó raiz destacado, espessura da aresta proporcional ao `peso`, tooltip com tipo da relação ("co-mencionados em N matérias"). Clicar num nó mostra nome+cargo.
   - Seção Eventos: lista simples dos `eventos`.

**Estética:** produto B2B sério e premium — paleta escura ou neutra sóbria, uma cor de acento, sem infantilidade. Português do Brasil em toda a UI. Responsivo o suficiente para demo em notebook e projetor.

**Estrutura do código:** isole os dados mockados em duas constantes no topo do JS (`MOCK_PERFIL`, `MOCK_GRAFO`) e o acesso a eles em funções `carregarPerfil(nome)` / `carregarGrafo(pessoaId)` — depois vou trocar o corpo dessas funções por chamadas fetch à API real (`POST /busca` → polling `GET /job/{id}` → `GET /perfil/{id}` e `GET /grafo/{id}` em http://localhost:8000).

---

## Quando o protótipo estiver pronto

Traga o HTML de volta para a pasta do projeto (ex.: `frontend/index.html`). Na próxima sessão eu (Claude do Cowork) troco os mocks pelas chamadas reais usando o `CONTRATO_API.md` — a API já está com CORS liberado, então o arquivo funciona aberto direto no navegador com `uvicorn app.main:app --reload` rodando.
