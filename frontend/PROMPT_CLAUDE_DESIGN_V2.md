# Prompt v2 para o Claude design — redesign com ênfase no grafo

Como usar: cole o prompt abaixo no Claude design e **anexe os 3 JSONs** desta pasta
(`exemplo_perfil.json`, `exemplo_grafo.json`, `exemplo_sugestoes.json`). O protótipo
volta para cá para integração com a API real (que já está pronta e testada).

---

## PROMPT (copiar daqui para baixo)

Redesenhe o frontend do "FSB Executive Intelligence" (marca: **nexus.**) — ferramenta
interna da FSB Holding onde analistas de comunicação pesquisam executivos brasileiros e
recebem um dossiê de inteligência de fontes públicas (LinkedIn + imprensa). Já existe um
protótipo funcional (dark, laranja #F5921E, Nunito) que validamos com usuários reais — o
redesign deve **evoluir essa identidade**, não trocá-la. Objetivo do redesign: sair da
cara de MVP e fazer do **grafo de conexões o protagonista do produto**.

Entrega: um único arquivo HTML (CSS/JS inline, Cytoscape.js via CDN permitido). Dados:
use os 3 JSONs anexos como mocks embutidos (são respostas REAIS da API). Não invente
campos; trate null com elegança (avatar de iniciais, "período não informado" etc.).

### A dinâmica do produto (mudou desde o protótipo v1 — respeitar!)

1. **Busca com seleção obrigatória.** O usuário digita nome, cargo ou empresa ("CEO do
   Nubank"). Enter/botão NÃO inicia coleta: abre um painel de sugestões com duas seções —
   "No acervo" (pessoas já pesquisadas, com foto/cargo/badge "dossiê pronto", resposta
   instantânea) e "LinkedIn" (candidatos reais com headline). **A coleta só começa quando
   o usuário clica em um candidato** — é assim que o produto garante que "CEO do Nubank"
   não vira o dossiê de um homônimo. Ver `exemplo_sugestoes.json`. Capriche neste
   momento de escolha: é a decisão mais importante do fluxo, o headline é o que permite
   distinguir "CEO da Friboi" de "CIO da Odontoprev".
2. **Carregando (20–60s reais)** com etapas nomeadas (imprensa → LinkedIn → IA → grafo),
   barra de progresso que NUNCA chega a 100% sozinha (trava em 99% até o backend
   concluir) e **botão "Cancelar e voltar"** (com nota: a coleta continua no servidor e
   fica no cache). Buscas repetidas caem no cache e pulam direto ao dossiê.
3. **Tela de erro** (dedicada): coleta falhou no servidor (mostra a causa), API fora do
   ar, timeout de 3 min. Botões "Tentar novamente" (repete a MESMA seleção) e "Voltar".
4. **Dossiê**: cabeçalho (foto, nome, cargo atual, localização, link LinkedIn, data,
   botão Atualizar), briefing executivo de 3 parágrafos (o texto principal do produto),
   menções na imprensa (título-link, fonte, data, indicador de sentimento −1..+1, chips
   de temas), trajetória (timeline de cargos com badge ATUAL), eventos, e o GRAFO.

### O GRAFO — protagonista (foco nº 1 do redesign)

Hoje ele é um painel de 380px espremido entre seções. Queremos que ele seja o momento
"uau" da demo e uma ferramenta de leitura de rede de verdade. Dados em
`exemplo_grafo.json` — repare no que o backend entrega agora:

- **Tipos de aresta**: `co_mencionado` (imprensa), `colega_empresa` e `co_board` (laços
  FORMAIS inferidos de períodos sobrepostos no LinkedIn), `co_evento`. Pode haver mais
  de uma aresta entre o mesmo par (ex.: co-mencionados NA IMPRENSA e ex-colegas na
  Vale). Diferencie os tipos visualmente (cor/estilo do traço — ex.: laços formais
  sólidos e "quentes", co-menções mais neutras) e dê uma **legenda**.
- **Evidências por aresta** (até 8): para co-menção, a lista de matérias (título + URL +
  `contexto`); para laço formal, empresa e as funções dos dois lados. Clicar/hover na
  aresta deve contar a HISTÓRIA da relação ("ex-colegas na Vale: Diretor Presidente /
  CFO" · "co-mencionados em 3 matérias").
- **Contexto "lista" vs "direta"**: evidência com `contexto: "lista"` significa "citados
  juntos num ranking tipo 50 mais ricos" — NÃO é relação genuína. Arestas cujas
  evidências são só "lista" devem nascer visualmente enfraquecidas (esmaecidas/
  tracejadas) e rotuladas; um **filtro "ocultar conexões de lista/ranking"** deve
  existir. Esta distinção é um diferencial do produto — deixe-a óbvia sem poluir.
- **Ideias de protagonismo** (escolha e componha o que fizer sentido): grafo em seção
  full-width generosa ou modo expandido/fullscreen; nós com FOTO quando `foto_url`
  existir (raiz maior, com anel laranja); tamanho do nó proporcional ao nº de conexões;
  espessura da aresta pelo `peso`; hover destaca o nó e seus vizinhos escurecendo o
  resto; clique no nó abre mini-card (nome, cargo, botão "abrir dossiê" quando for
  pessoa do acervo); controles visíveis de profundidade (1–3) e peso mínimo; contadores
  ("12 conexões · 3 laços formais · 4 de listas"); animação de entrada do layout;
  fundo com textura/glow sutil que valorize a rede. Performance: as redes reais têm
  5–40 nós — dá para caprichar sem medo.

### Flare geral (foco nº 2)

Elevar a percepção de produto sem trair o design system: microinterações (hover states,
transições entre telas, skeleton/stagger na entrada do dossiê), hierarquia tipográfica
mais confiante, sensação "intelligence tool" premium (referências: Linear, Palantir,
dashboards Bloomberg — sobriedade com momentos de brilho). Tela de busca pode ganhar
mais presença de marca. Português do Brasil. Responsivo para notebook e projetor
(demo para cliente).

### Estrutura do código (para a integração ser barata)

- Mocks isolados em constantes no topo: `MOCK_PERFIL`, `MOCK_GRAFO`, `MOCK_SUGESTOES`.
- Acesso a dados APENAS via funções: `buscarSugestoes(q)`, `iniciarColeta(nome,
  linkedinUrl)`, `carregarPerfil(pessoaId)`, `carregarGrafo(pessoaId, profundidade,
  pesoMinimo)` — vou trocar os corpos por fetch (API em http://localhost:8000, contrato
  já definido).
- Estados de tela: 'busca' | 'carregando' | 'erro' | 'dossie' — todos os quatro devem
  existir e ser demonstráveis.
- Design tokens do sistema atual: fundo #0E0B07, superfícies #14100A/#161109/#18130C,
  bordas #211A11→#463822, texto #F5EFE6→#8A7A64, acento #F5921E (claro #FFB25C, escuro
  #E06D10), sentimento #E5484D/#8A7A64/#46A758, Nunito 400–900, radius 8–10px.

---

## Quando o protótipo voltar

Salve como `frontend/index_v2.html` na pasta do projeto e me chame (Claude do Cowork).
Integro com a API real usando o `CONTRATO_API.md` — o backend de sugestões, seleção
obrigatória, evidências e contexto já está pronto e coberto por 67 testes.
