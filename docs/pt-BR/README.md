> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../../README.md) takes precedence.
> **Nota:** Esta é uma versão autotraduzida da documentação original em inglês.
> Em caso de discrepâncias, a [versão em inglês](../../README.md) prevalece.

# Claude & Codex Discord Bridge

*Nome do pacote: `claude-code-discord-bridge` (kebab-case)*

[![CI](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Use Claude Code _ou_ OpenAI Codex no seu celular. Múltiplas threads. Tudo ao mesmo tempo. Desenvolvimento real incluído.**

Abra o Claude Code ou o OpenAI Codex no app do Discord do seu smartphone, inicie múltiplas threads e execute sessões de desenvolvimento em paralelo — tudo sem tocar em um teclado. Cada thread do Discord se torna uma sessão de IA totalmente isolada. Trabalhe em uma feature em uma thread, revise um PR em outra e execute uma tarefa em segundo plano em uma terceira — simultaneamente, misturando até mesmo backends por thread. A bridge cuida de toda a coordenação para que as sessões nunca atropelem umas às outras.

**Use suas assinaturas existentes. Sem malabarismos com API keys.** ccdb roda sobre as CLIs oficiais — Claude Code (inclusa na sua [assinatura Claude Pro/Max](https://claude.ai/pricing)) e OpenAI Codex (incluso no [ChatGPT Plus/Pro/Business](https://chatgpt.com)). Troque de backend com `/backend` ou defina uma substituição por thread — seu time acessa ambas as IAs pelo Discord a um custo previsível.

**[English](../../README.md)** | **[日本語](../ja/README.md)** | **[简体中文](../zh-CN/README.md)** | **[한국어](../ko/README.md)** | **[Español](../es/README.md)** | **[Français](../fr/README.md)**

> **Aviso:** Este projeto não é afiliado, endossado ou oficialmente conectado à Anthropic ou OpenAI. "Claude" e "Claude Code" são marcas registradas da Anthropic, PBC; "OpenAI", "Codex" e "ChatGPT" são marcas registradas da OpenAI. Esta é uma ferramenta de código aberto independente que faz interface com o Claude Code CLI e o OpenAI Codex CLI.

> **Construído inteiramente pelo Claude Code.** Todo este codebase — arquitetura, implementação, testes, documentação — foi escrito pelo próprio Claude Code. O autor humano forneceu requisitos e direcionamento via linguagem natural. Veja [Como Este Projeto Foi Construído](#como-este-projeto-foi-construído).

---

## A Grande Ideia: Sessões Paralelas Sem Medo

Quando você envia tarefas para o Claude Code ou o OpenAI Codex em threads separadas do Discord, a bridge faz quatro coisas automaticamente — independentemente de qual backend você escolheu:

1. **Injeção de aviso de concorrência** — O system prompt de cada sessão inclui instruções obrigatórias: criar um git worktree, trabalhar apenas dentro dele, nunca tocar o diretório de trabalho principal diretamente.

2. **Registro de sessões ativas** — Cada sessão em execução conhece as outras. Se duas sessões estão prestes a tocar o mesmo repositório, elas podem coordenar em vez de conflitar.

3. **AI Lounge** — Uma "sala de descanso" sessão-a-sessão injetada em cada prompt. Antes de começar, cada sessão lê as mensagens recentes do lounge para ver o que as outras sessões estão fazendo, e reivindica o repositório, issue ou arquivo que está prestes a tocar (veja [Reivindicações de Recursos](#reivindicações-de-recursos)) para que uma segunda sessão seja rejeitada antes de duplicar o trabalho. Antes de operações destrutivas (force push, reinício do bot, drop de DB), as sessões verificam o lounge primeiro para não atropelar o trabalho umas das outras.

4. **Superfície agnóstica de backend** — A mesma UI do Discord, comandos slash, agendador, API e Lounge funcionam da mesma forma quer uma thread rode Claude ou Codex. Misture backends entre threads se quiser — por exemplo, Claude para refatorações, Codex para revisão de código — usando `/backend` por thread.

```
Thread A (feature)    ──→  Claude Code  (worktree-A)  ─┐
Thread B (PR review)  ──→  OpenAI Codex (worktree-B)   ├─→  #ai-lounge
Thread C (docs)       ──→  Claude Code  (worktree-C)  ─┘    "A: auth refactor in progress"
                                                             "B: PR #42 review done (codex)"
                                                             "C: updating README"
```

Sem race conditions. Sem trabalho perdido. Sem surpresas no merge. Sem lock-in de backend.

---

## O Que Você Pode Fazer

### Chat Interativo (Mobile / Desktop)

Use Claude Code _ou_ OpenAI Codex de qualquer lugar onde o Discord rode — celular, tablet ou desktop. Cada mensagem cria ou continua uma thread que mapeia 1:1 para uma sessão de IA persistente. Troque de backend a qualquer momento com `/backend claude` ou `/backend codex` — por thread, ou globalmente como o novo padrão.

### Desenvolvimento Paralelo

Abra múltiplas threads simultaneamente. Cada uma é uma sessão de IA independente — Claude Code ou Codex — com seu próprio contexto, diretório de trabalho e git worktree. Padrões úteis:

- **Feature + revisão em paralelo**: Inicie uma feature com o Claude em uma thread enquanto o Codex revisa o PR em outra.
- **Múltiplos contribuidores**: Cada membro da equipe tem sua própria thread (e seu backend preferido); as sessões permanecem cientes umas das outras via AI Lounge.
- **Experimente com segurança**: Tente uma abordagem na thread A enquanto mantém a thread B em código estável.
- **Compare o mesmo prompt nas duas IAs**: Crie duas threads com a mesma tarefa, uma em `/backend claude` e outra em `/backend codex`, e depois compare os diffs lado a lado.

### Tarefas Agendadas (SchedulerCog)

Registre tarefas periódicas do Claude Code a partir de uma conversa do Discord ou via REST API — sem alterações de código, sem redeploys. As tarefas são armazenadas em SQLite e executadas em um agendamento configurável. O Claude pode auto-registrar tarefas durante uma sessão usando `POST /api/tasks`.

```
/skill name:goodmorning         → runs immediately
Claude calls POST /api/tasks    → registers a periodic task
SchedulerCog (30s master loop)  → fires due tasks automatically
```

### Automação CI/CD

Dispare tarefas do Claude Code a partir do GitHub Actions via webhooks do Discord. O Claude roda autonomamente — lê código, atualiza documentação, cria PRs, habilita auto-merge.

```
GitHub Actions → Discord Webhook → Bridge → Claude Code CLI
                                                  ↓
GitHub PR ←── git push ←── Claude Code ──────────┘
```

**Exemplo real:** A cada push para `main`, o Claude analisa o diff, atualiza a documentação em inglês + japonês, cria um PR bilíngue e habilita auto-merge. Zero interação humana.

### Sincronização de Sessões

Já usa o Claude Code CLI diretamente? Sincronize suas sessões de terminal existentes em threads do Discord com `/sync-sessions`. Retroalimenta mensagens de conversa recentes para que você possa continuar uma sessão CLI do seu celular sem perder contexto.

### AI Lounge

Um canal compartilhado de "sala de descanso" onde todas as sessões concorrentes se anunciam, leem as atualizações umas das outras e coordenam antes de operações destrutivas.

Cada sessão recebe o contexto do lounge automaticamente como instruções efêmeras de sistema/desenvolvedor (`--append-system-prompt` para o Claude, `developer_instructions` para o Codex), em vez de como parte do histórico da conversa. Isso evita que o contexto se acumule ao longo dos turnos, o que de outra forma causaria erros de "Prompt is too long" em sessões de longa duração. O contexto injetado inclui as mensagens recentes de outras sessões mais a regra de verificar antes de fazer qualquer coisa destrutiva.

```bash
# Sessions post their intentions before starting:
curl -X POST "$CCDB_API_URL/api/lounge" \
  -H "Content-Type: application/json" \
  -d '{"message": "Starting auth refactor on feature/oauth — worktree-A", "label": "feature dev"}'

# Read recent lounge messages (also injected into each session automatically):
curl "$CCDB_API_URL/api/lounge"
```

O canal do lounge também funciona como um feed de atividade visível para humanos — abra-o no Discord para ver rapidamente o que cada sessão ativa do Claude está fazendo no momento.

### Observabilidade Entre Sessões

Uma nota no lounge diz a uma sessão *que* outra thread existe. Estes dois endpoints somente-leitura permitem que ela vá olhar — para que duas sessões que começaram a mesma tarefa possam descobrir a sobreposição em vez de ambas seguirem em frente.

```bash
# Who else is alive, where are they working, what did they last announce?
curl "$CCDB_API_URL/api/sessions?exclude_thread=$DISCORD_THREAD_ID"

# Read that thread's actual conversation
curl "$CCDB_API_URL/api/threads/1529338965000192110/messages?limit=30"
```

`/api/sessions` mescla três fontes: a tabela `sessions` (created_at, diretório de trabalho, backend), o registro em memória (o que cada sessão viva está fazendo *agora*) e a nota de lounge mais recente de cada thread. Uma sessão aparece com `"state": "running"` enquanto um turno está em andamento — incluindo sessões que nunca publicaram no lounge, que é exatamente quando isso importa. Uma conversa salva sem um turno em andamento aparece com `"state": "history"`: ela pode ser retomada, mas isso não significa que um agente esteja aguardando trabalho ou entrada do usuário. As sessões não têm token do Discord próprio, então o bot realiza a leitura e os endpoints permanecem no plano de controle localhost.

### Reivindicações de Recursos

A observabilidade diz a uma sessão que uma colisão *aconteceu*. Uma reivindicação a previne — sem leitura, sem negociação, sem ida e volta ao LLM. Uma sessão reivindica aquilo em que está prestes a trabalhar; a próxima sessão que pedir a mesma coisa é recusada antes de fazer qualquer trabalho.

```bash
# Before starting: claim it
curl -X POST "$CCDB_API_URL/api/claims" \
  -H "Content-Type: application/json" \
  -d '{"resource": "repo:ccdb#issue-123", "thread_id": "'$DISCORD_THREAD_ID'", "note": "fixing the parser"}'
# 201 {"status": "acquired", ...}

# A second session asking for the same resource:
# 409 {"status": "held", "claim": {"thread_id": ..., "note": "fixing the parser",
#      "holder_state": "running", "holder_thread_name": "..."}}

# When done
curl -X DELETE "$CCDB_API_URL/api/claims?resource=repo:ccdb%23issue-123&thread_id=$DISCORD_THREAD_ID"
```

As reivindicações são **consultivas** — nada as impõe no nível do git ou do sistema de arquivos — e cada reivindicação carrega um TTL (padrão 2h, máximo 24h) para que uma sessão que morre não possa fixar um recurso para sempre. O corpo do 409 informa se o detentor ainda está em execução, que é como quem chama decide se espera, trabalha em outra coisa ou assume com `force=true`. Os nomes dos recursos são de forma livre e normalizados (maiúsculas/minúsculas e espaços em branco), então `repo:ccdb` e `Repo: CCDB` são a mesma reivindicação.

O prompt do lounge instrui cada sessão a reivindicar antes de começar e a liberar ao terminar.

### Relay Entre Sessões

A observabilidade permite que uma sessão veja um par; uma reivindicação as mantém separadas. Quando duas sessões já colidiram, elas precisam realmente conversar — e uma delas precisa parar.

```bash
curl -X POST "$CCDB_API_URL/api/threads/<their_thread_id>/message" \
  -H "Content-Type: application/json" \
  -d '{"text": "I started this at 13:02 on branch fix/parser and already pushed 3 commits.",
       "from_thread": "'$DISCORD_THREAD_ID'", "mode": "queue", "hop": 0}'
```

`on_message` ignora qualquer coisa que um bot escreveu — essa proteção é o que impede o bot de falar consigo mesmo — então os relays passam por este endpoint, da mesma forma que `/api/spawn` faz.

- **`mode: "queue"`** (padrão) aguarda o turno atual do receptor terminar.
- **`mode: "interrupt"`** envia SIGINT ao turno em andamento, para que o "pare agora" chegue em segundos. Pode custar ao receptor trabalho não commitado, então é reservado para conflitos reais.
- O texto retransmitido é **publicado na thread** antes de chegar ao Claude, para que os humanos que assistem vejam toda a troca IA-a-IA. Um relay nunca é um canal secreto.
- Cada mensagem é **envolvida em um marcador** que nomeia a thread remetente e afirma que não vem do humano — uma instrução sem marcação seria obedecida como se o dono a tivesse escrito.

Loops são o risco real (duas sessões respondendo uma à outra queimam tokens e se interrompem indefinidamente), então uma proteção limita cada cadeia: **máximo de 2 saltos (hops)**, um cooldown de 60s por par de threads, 5 relays por remetente a cada 10 minutos, e nenhum auto-envio. Recusas voltam como 429 com o motivo.

O prompt do lounge também dá às sessões uma regra de desempate para que a conversa convirja em vez de terminar em cortesia mútua: quem tem commits ou um PR vence quem ainda está investigando; caso contrário, a sessão mais antiga continua; empates vão para o menor ID de thread. Quem recua faz push do seu branch primeiro e entrega o que aprendeu.

### Detecção Automática de Colisão

O lounge e as reivindicações dependem ambos de uma sessão *dizer* algo. Isto captura as sobreposições que ninguém anunciou, a partir do que as sessões realmente fizeram: se duas sessões vivas escrevem no mesmo arquivo em até 15 minutos, elas estão trabalhando na mesma coisa, quer alguma delas tenha mencionado ou não.

`EventProcessor` registra o caminho de cada chamada de ferramenta do tipo escrita (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`); `CollisionWatchCog` compara esses conjuntos entre as sessões vivas uma vez por minuto.

> Por que caminhos de arquivo e não diretórios de trabalho: em um host de usuário único, cada sessão tende a começar no mesmo diretório home, então a igualdade de `working_dir` marca todos os pares e não significa nada. Um *arquivo editado* compartilhado quase nunca é coincidência. Leituras são deliberadamente ignoradas — duas sessões lendo o mesmo arquivo é normal e afogaria o sinal.

Quando uma sobreposição é encontrada, o observador publica:

- uma linha no **AI Lounge**, que é injetada no próximo turno de cada sessão sem custo de token e sem interromper nada, e
- uma mensagem em **cada thread em colisão**, nomeando o par, os arquivos compartilhados e os endpoints que a resolvem.

Ele nunca retransmite para uma sessão em execução — preemptar um turno por mera suspeita custaria mais do que a colisão. Escalar é decisão das sessões, usando o endpoint de relay acima. Cada par é anunciado no máximo uma vez a cada 30 minutos, porque um aviso repetido a cada minuto é um aviso que todos aprendem a ignorar.

Habilitado automaticamente; permanece dormente até que duas sessões realmente se sobreponham.

### Criação de Sessão Programática

Crie novas sessões do Claude Code a partir de scripts, GitHub Actions ou outras sessões do Claude — sem interação com mensagens do Discord.

```bash
# From another Claude session or a CI script:
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Run security scan on the repo", "thread_name": "Security Scan"}'
# Returns immediately with the thread ID; Claude runs in the background
```

**Início adiado (`auto_start=false`)** — Crie uma thread e publique uma mensagem semente sem iniciar o Claude imediatamente. O Claude inicia apenas quando um usuário responde, e recebe a mensagem semente como contexto automaticamente.

```bash
# Post a notification; Claude starts when the user replies
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Good morning! Here is your daily summary: ...",
    "thread_name": "Morning Briefing",
    "auto_start": false
  }'
```

Isso é útil para fluxos de trabalho no estilo de notificação (por exemplo, briefings diários, alertas de CI) onde você quer exibir informações de antemão e deixar o usuário decidir se quer engajar o Claude.

Os subprocessos do Claude recebem `DISCORD_THREAD_ID` como variável de ambiente, para que uma sessão em execução possa criar sessões filhas para paralelizar o trabalho.

### Ingestão Externa Autenticada com Recuperação de Resultados (`/api/ingest`)

`POST /api/ingest` é o **spawn autenticado e compatível com anexos** para clientes externos não confiáveis (extensões de navegador, atalhos móveis, webhooks). Ao contrário de `/api/spawn` (confiável, localhost), ele requer um `ingest_token` dedicado (configure `CCDB_INGEST_TOKEN`; independente de `api_secret`) e pode carregar anexos de arquivo em base64 que são gravados em disco para que a sessão criada possa lê-los. Ele cria uma thread real do Discord, então toda a interação permanece observável.

A sessão é **interativa** (uma thread real do Discord onde você pode continuar respondendo) — mas você ainda pode obter a resposta final dela programaticamente. Quando a recuperação de resultados está configurada (conectada automaticamente via `setup_bridge()`), a resposta inclui um `result_id`, e `GET /api/ingest/{result_id}` faz polling da resposta final da sessão. A mesma resposta final também é anexada à thread do Discord como `ccdb-answer.md`, para que integrações possam tratar o anexo como o payload de resposta canônico. Este é o padrão de ida-e-volta: publique uma thread + anexos → aguarde → leia o arquivo de resposta ou faça polling do resultado → grave-o de volta no seu próprio sistema (por exemplo, uma thread do Teams), enquanto o Discord mantém o histórico.

```bash
# Post work (optionally with attachments); returns immediately
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "Summarize this thread and draft a reply",
       "attachments": [{"filename": "thread.txt", "data": "<base64>"}]}'
# → {"status": "spawned", "thread_id": "…", "result_id": "ab12…", "attachments_saved": 1}

# Poll for the final reply
curl "$CCDB_API_URL/api/ingest/ab12…" -H "Authorization: Bearer $CCDB_INGEST_TOKEN"
# → {"status": "done", "result": "…", "error": null, "thread_id": "…", "thread_name": "…"}
```

O endpoint é opt-in: sem `ingest_token` configurado, `POST` responde `503`. Quando a recuperação de resultados está indisponível, `POST` simplesmente omite `result_id` e `GET /api/ingest/{id}` retorna `503` — o comportamento de spawn permanece de resto inalterado. O corpo da requisição e os anexos **não** são persistidos no armazenamento de resultados (apenas status, o texto final e o id da thread); os resultados são limitados a 200 linhas.

#### Entrega de anexos verificada (`attachments_manifest`)

Um anexo perdido do lado do cliente era invisível: o ccdb salvava o que recebia e reportava uma contagem que ninguém podia conferir, então a sessão respondia como se tivesse um arquivo que nunca chegou. Envie um **manifesto** e o ccdb **verifica** a entrega em vez de presumi-la — uma entrada para cada anexo que você encontrou na origem, com `status` informando se os bytes dele estão nesta requisição:

```bash
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "Redija uma resposta",
       "attachments": [{"filename": "bundle.zip", "data": "<base64>"}],
       "attachments_manifest": [
         {"name": "shot.png",  "status": "embedded", "sha256": "…", "message": "返信 11"},
         {"name": "debug.log", "status": "linked", "url": "https://…", "message": "返信 12",
          "reason": "SharePoint download returned 403"}
       ]}'
# → {"status": "spawned", …, "attachments": {"verified": true, "complete": false,
#      "missing": [], "not_delivered": [{"name": "debug.log", "message": "返信 12", …}]}}
```

| `status` | Significado |
|---|---|
| `embedded` (padrão) | Os bytes estão nesta requisição — o ccdb espera encontrar um arquivo correspondente |
| `linked` | Só foi possível obter uma URL (sem permissão no host, barreira de autenticação, …) |
| `skipped` | Deliberadamente não enviado (limite de tamanho, filtrado) |
| `failed` | A captura ou o download falhou |

O ccdb casa cada entrada `embedded` com um arquivo entregue usando **primeiro o sha256**, depois o nome exato, depois o prefixo de índice no formato `4_image.png` que um empacotador adiciona em caso de nomes conflitantes, e por fim o tamanho — consumindo cada arquivo no máximo uma vez, de modo que dois anexos chamados `image.png` não possam ambos reivindicar o único arquivo que chegou. Tudo o que ficar sem explicação aparece de quatro formas: um bloco ⚠️ no **topo do prompt da sessão** nomeando os arquivos ausentes e instruindo a sessão a não inventar seu conteúdo (com um aviso extra quando a perda está na mensagem mais recente), um registro `ATTACHMENTS-REPORT.md` gravado ao lado dos arquivos, o veredito `attachments` na resposta acima, e um `WARNING` no log. Fornecer `message` também agrupa a lista de caminhos do prompt por mensagem de origem e marca o grupo mais recente como o que deve ser lido primeiro.

Defina `CCDB_INGEST_REQUIRE_COMPLETE=1` (ou `ingest_require_complete=True`) para **recusar** uma ingestão com perdas retornando `409`, em vez de iniciar uma sessão com evidências parciais — apropriado quando os anexos *são* a requisição. Omita o manifesto por completo e nada muda: a ingestão é reportada como `verified: false`, nunca como verificada e completa.

### Retomada na Inicialização

Se o bot reiniciar no meio de uma sessão, as sessões Claude interrompidas são automaticamente retomadas quando o bot volta a ficar online. As sessões são marcadas para retomada de três formas:

- **Automático (reinício por atualização)** — `AutoUpgradeCog` captura um snapshot de todas as sessões ativas logo antes de um reinício por atualização de pacote e as marca automaticamente.
- **Automático (qualquer desligamento)** — `ClaudeChatCog.cog_unload()` marca todas as sessões em execução sempre que o bot é desligado por qualquer mecanismo (`systemctl stop`, `bot.close()`, SIGTERM, etc.).
- **Manual** — Qualquer sessão pode chamar `POST /api/mark-resume` diretamente.

### Troca de Backend — Claude / Codex Sob Demanda

O ccdb 3.0 introduz três comandos slash que mudam qual IA lida com a próxima sessão, sem reiniciar o bot:

- `/backend [name] [scope]` — mostra ou troca o backend. `name` é `claude` ou `codex`. `scope` é `thread` (apenas esta thread) ou `global` (padrão de todo o servidor). Quando você omite `scope`, o comando resolve automaticamente: em uma thread, ele se aplica àquela thread; caso contrário, define o padrão global.
- `/model [name] [scope]` — mostra ou troca o modelo usado pelo backend **atual**. Cada backend lembra sua própria preferência de modelo, então alternar o backend de um lado para o outro mantém seus modelos favoritos intactos. Deixe o modelo de um backend sem definir para adiar ao padrão da própria CLI (por exemplo, o Codex usa o `model` em `~/.codex/config.toml`, então o ccdb rastreia o padrão do console em vez de fixar uma versão).
- `/effort [level] [scope]` — mostra ou troca o **esforço de raciocínio (reasoning effort)** usado pelo backend atual. Os níveis válidos são específicos de cada backend: o Claude aceita `low/medium/high/max`; o Codex aceita `low/medium/high/xhigh/max/ultra` (mapeado para o `model_reasoning_effort` da CLI). Deixe sem definir para adiar ao padrão da CLI.

Todos os três comandos persistem no SQLite via `SettingsRepository`, então a escolha sobrevive a reinícios do bot. Chamá-los sem argumentos imprime o padrão global atual mais qualquer substituição por thread.

**O que acontece com uma thread que já tem uma sessão?** Os IDs de sessão não são interoperáveis entre as duas CLIs — passar um ID de rollout do Codex para `claude --resume` (ou um UUID do Claude para `codex exec resume`) falha no nível da CLI. O ccdb registra qual backend gerou cada ID de sessão, então uma troca nunca deixa uma thread órfã:

- **Troca com escopo de thread** — o ID de sessão armazenado é descartado para que a próxima mensagem comece do zero no novo backend, *a menos que* o registro seja conhecido por pertencer ao backend para o qual você trocou. Trocar de volta é, portanto, uma forma válida de retomar a conversa anterior de uma thread.
- **Troca global** — os registros por thread são deliberadamente deixados intocados. Se uma thread ainda estiver mantendo o ID de sessão do outro backend, a próxima mensagem inicia uma sessão nova e publica um aviso de uma linha explicando o porquê, em vez de retomar.

Registros escritos antes de o ccdb rastrear a propriedade de backend não têm backend armazenado. Uma troca global os retoma exatamente como sempre fez; uma troca com escopo de thread os limpa em vez de arriscar uma retomada quebrada.

Pistas visuais para você nunca esquecer com qual está falando:

- **Sessões Claude** abrem com um embed blurple intitulado "🤖 Claude Code session started".
- **Sessões Codex** abrem com um embed em teal-OpenAI intitulado "🌀 OpenAI Codex session started".
- O embed de conclusão adiciona um chip `🧠 Claude · sonnet` / `🧠 Codex · gpt-5.6-sol` ao lado das métricas usuais de duração / custo / token / contexto. (Quando o modelo de um backend é deixado no padrão da CLI, o chip mostra apenas o nome do backend.)

Exemplo concreto:

```text
/backend codex                        # global → codex (next new sessions use codex)
/model gpt-5-codex                    # global → codex uses gpt-5-codex
/effort xhigh                          # global → codex reasons at xhigh effort
                                       # …open a thread, send a message…
/backend claude scope:thread          # this thread only → switch back to claude
/model opus scope:thread              # this thread only → claude/opus
/effort max scope:thread              # this thread only → claude reasons at max
                                       # other threads keep the global codex defaults
```

Nos bastidores:

- `BackendFactory` — captura a configuração estática no boot (caminho de comando por backend, modo de permissão, diretório de trabalho, ferramentas permitidas, timeout, append-system-prompt, effort, api_port, api_secret) e constrói um novo `ClaudeRunner` ou `CodexRunner` sob demanda. `api_port` é conectado automaticamente pelo `setup_bridge` após o servidor da REST API iniciar, então os runners construídos pela fábrica sempre têm `CCDB_API_URL` injetado no ambiente do seu subprocesso.
- `BackendSettings` — um invólucro fino sobre `SettingsRepository` que resolve o backend ativo com precedência **thread > global > env** e persiste as escritas dos comandos slash.
- Protocolo `SessionBackend` — a interface abstrata que ambos os runners satisfazem. O encanamento interno (cogs, embeds, views, agendador, gatilho de webhook) recebe um `SessionBackend`, nunca uma classe de runner concreta.

**Onde cada backend se autentica?** O Claude Code usa sua assinatura Claude Pro/Max existente via o `claude login` da CLI `claude`. O Codex usa sua assinatura ChatGPT Plus/Pro/Business existente via o `codex login` da CLI `codex`. O ccdb nunca vê API keys em texto puro — ele apenas invoca a CLI que estiver selecionada.

---

## Funcionalidades

### Chat Interativo

#### 🔗 Básico de Sessão
- **Modo somente chat** — Quando `CHAT_ONLY_CHANNEL_IDS` inclui um canal, apenas as respostas de texto do Claude são mostradas; embeds de ferramentas, blocos de pensamento, embeds de início/conclusão de sessão e listas de tarefas ficam ocultos. Solicitações de permissão e `AskUserQuestion` são sempre mostradas. Ideal para canais públicos onde usuários não técnicos estão assistindo.
- **Thread = Sessão** — Mapeamento 1:1 entre thread do Discord e sessão do Claude Code
- **Acompanhamento de objetivo** — `/goal <condition>` define uma condição de conclusão; o Claude continua trabalhando até a condição ser atendida. Omita a condição para verificar o status; passe `clear` para cancelar
- **Persistência de sessão** — Retome conversas entre mensagens via `--resume`
- **Recuperação automática de retomada do Codex** — Se uma sessão Codex retomada perde repetidamente seu WebSocket antes de produzir saída, o ccdb inicia uma sessão substituta com uma transcrição limitada e somente texto da conversa anterior; payloads de imagem e ferramenta são excluídos
- **Sessões concorrentes** — Múltiplas sessões paralelas com limite configurável
- **Parar sem limpar** — `/stop` interrompe uma sessão preservando-a para retomada
- **Interrupção de sessão** — Enviar uma nova mensagem para uma thread ativa envia SIGINT à sessão em execução e recomeça com a nova instrução; sem necessidade de `/stop` manual
- **Auto-renomear threads** — Quando `THREAD_AUTO_RENAME=true`, cada nova thread é automaticamente renomeada com um título gerado pelo Claude derivado da primeira mensagem (tarefa em segundo plano, nunca atrasa o início da sessão)

#### 📡 Feedback em Tempo Real
- **Status em tempo real** — Reações emoji: 🧠 pensando, 🛠️ lendo arquivos, 💻 editando, 🌐 busca na web
- **Texto em streaming** — Texto intermediário do assistente aparece enquanto o Claude trabalha
- **Embeds de resultado de ferramenta** — Resultados de chamadas de ferramentas ao vivo com o tempo decorrido mostrado imediatamente (0s) e incrementando a cada 5s; saídas de uma linha mostradas inline, saídas de várias linhas recolhidas atrás de um botão de expandir
- **Pensamento estendido** — Raciocínio mostrado como embeds com tag de spoiler (clique para revelar)
- **Dashboard de thread** — Embed fixado ao vivo mostrando quais threads estão ativas vs. aguardando; o dono é @-mencionado quando uma entrada é necessária

#### 🤝 Human-in-the-Loop
- **Perguntas interativas** — `AskUserQuestion` renderiza como Botões ou Menu de Seleção do Discord; a sessão retoma com sua resposta; os botões sobrevivem a reinícios do bot; o solicitante é @mencionado quando uma entrada é necessária
- **Modo Plan** — Quando o Claude chama `ExitPlanMode`, um embed do Discord mostra o plano completo com botões Aprovar/Cancelar; o Claude prossegue apenas após a aprovação; o solicitante é @mencionado no prompt; auto-cancelamento após timeout de 5 minutos
- **Solicitações de permissão de ferramenta** — Quando o Claude precisa de permissão para executar uma ferramenta, o Discord mostra botões Permitir/Negar com o nome da ferramenta e a entrada; o solicitante é @mencionado; auto-negação após 2 minutos
- **MCP Elicitation** — Servidores MCP podem solicitar entrada do usuário via Discord (modo formulário: até 5 campos de Modal a partir do JSON schema; modo url: botão de URL + confirmação Done); o solicitante é @mencionado; timeout de 5 minutos
- **Progresso do TodoWrite ao vivo** — Quando o Claude chama `TodoWrite`, um único embed do Discord é publicado e editado no lugar a cada atualização; mostra ✅ concluído, 🔄 ativo (com o rótulo `activeForm`), ⬜ itens pendentes

#### 📊 Observabilidade
- **Uso de tokens** — Taxa de acerto de cache e contagens de tokens mostradas no embed de conclusão de sessão
- **Uso de contexto** — Percentual da janela de contexto (tokens de entrada + cache, excluindo saída) e capacidade restante até o auto-compact mostrados no embed de conclusão de sessão; aviso ⚠️ quando acima de 83.5%
- **Detecção de compactação** — Notifica na thread quando ocorre compactação de contexto (tipo do gatilho + contagem de tokens antes da compactação)
- **Notificação de travamento forte (hard stall)** — Mensagem na thread após nenhuma atividade (pensamento estendido ou compressão de contexto); reseta automaticamente quando o Claude retoma. Os limiares consideram o modelo: 30 s para modelos padrão, 120 s para o Opus (que tem pausas de pensamento mais longas)
- **Notificações de timeout** — Embed com tempo decorrido e orientação de retomada no timeout
- **Exibição de StatusLine** — Quando o Claude configura uma `statusLine` (via `/statusline-setup`), o status atual é mostrado no Discord após cada sessão como um indicador conciso e sempre visível
- **Indicador de provedor de API** — Após cada sessão, uma linha `🔗 API: <provider>` mostra qual endpoint a CLI está realmente usando (`Anthropic API (direct)`, `AWS Bedrock`, `Google Vertex AI`, `Azure AI Foundry`, ou uma base URL personalizada), derivado do ambiente real do subprocesso para que sobreposições de env da CLI sejam refletidas. Sempre mostrado — mesmo sem uma `statusLine` configurada.
- **Caixa de entrada de thread** — Quando `THREAD_INBOX_ENABLED=true`, o dashboard mostra uma seção de caixa de entrada 📬 persistente: após cada sessão terminar, o Claude classifica a mensagem final (`waiting` / `done` / `ambiguous`) via uma chamada leve `claude -p`; as threads aguardando sua resposta sobrevivem a reinícios do bot e são exibidas até você responder

#### 🔌 Entrada e Habilidades
- **Suporte a anexos** — Arquivos de texto adicionados automaticamente ao prompt (até 5 arquivos, 200 KB cada / 500 KB no total; arquivos grandes demais são truncados com um aviso em vez de ignorados); imagens enviadas como URLs de CDN do Discord via `--input-format stream-json` (até 4 × 5 MB); mensagens longas coladas que o Discord converte automaticamente em anexos de arquivo (sem `content_type`) são tratadas via detecção baseada em extensão
- **Entrega de arquivos sob demanda** — Peça ao Claude para "me enviar" ou "anexar" um arquivo e ele escreve o caminho em `.ccdb-attachments`; o bot o lê e entrega o arquivo como um anexo do Discord quando a sessão termina. Instruções locais também podem exigir que entregáveis escritos substanciais sejam salvos como Markdown e anexados.
- **Execução de habilidades** — Comando `/skill` com autocompletar, argumentos opcionais, retomada na thread; habilidades de plugins instalados também são descobertas automaticamente
- **Hot reload** — Novas habilidades adicionadas a `~/.claude/skills/` são detectadas automaticamente (atualização de 60s, sem reinício)

### Concorrência e Coordenação
- **Instruções de worktree auto-injetadas** — Cada sessão é instruída a usar `git worktree` antes de tocar em qualquer arquivo
- **Limpeza automática de worktree** — Worktrees de sessão (`wt-{thread_id}`) são removidos automaticamente ao fim da sessão e na inicialização do bot; worktrees sujos nunca são removidos automaticamente (invariante de segurança)
- **Registro de sessões ativas** — Registro em memória; cada sessão vê o que as outras estão fazendo
- **AI Lounge** — Canal compartilhado de "sala de descanso"; contexto injetado como instruções de sistema/desenvolvedor específicas do backend (efêmeras, nunca se acumulam no histórico) para que sessões longas nunca atinjam "Prompt is too long"; sessões publicam intenções, leem o status umas das outras e verificam antes de operações destrutivas; humanos veem isso como um feed de atividade ao vivo
- **Observabilidade entre sessões** — `GET /api/sessions` lista cada sessão (viva e armazenada) com seu estado, diretório de trabalho e nota de lounge mais recente; `GET /api/threads/{thread_id}/messages` lê a conversa de outra thread. Somente-leitura, para que uma sessão possa olhar antes de editar — inclusive sessões que nunca publicaram no lounge
- **Reivindicações de recursos** — `POST /api/claims` reserva um repositório, issue ou arquivo antes de o trabalho começar; uma segunda sessão que pede o mesmo recurso recebe 409 com a thread, nota e estado ao vivo do detentor. Consultivas e limitadas por TTL (padrão 2h, máximo 24h), para que uma sessão morta não possa fixar um recurso para sempre
- **Relay entre sessões** — `POST /api/threads/{thread_id}/message` permite que uma sessão fale com outra quando já colidiram; `queue` aguarda o turno do receptor, `interrupt` envia SIGINT a ele. Cada relay é publicado na thread (nunca um canal secreto), envolvido em um marcador para não ser confundido com o humano, e limitado por saltos/cooldown/limites de taxa para que duas sessões não entrem em loop
- **Detecção automática de colisão** — `CollisionWatchCog` compara os arquivos que as sessões vivas realmente escreveram (registrados de `Write`/`Edit`/`MultiEdit`/`NotebookEdit`) uma vez por minuto; duas sessões escrevendo o mesmo arquivo em até 15 minutos são anunciadas no AI Lounge e em ambas as threads. Captura as sobreposições que ninguém anunciou; um alerta por par a cada 30 minutos, e nunca interrompe um turno em execução
- **Canal de coordenação** — A variável de ambiente `COORDINATION_CHANNEL_ID` é usada como fallback padrão para o canal do AI Lounge (sem eventos de ciclo de vida separados do lado do bot)

### Tarefas Agendadas
- **SchedulerCog** — Executor de tarefas periódicas com suporte SQLite e um loop mestre de 30 segundos
- **Auto-registro** — O Claude registra tarefas via `POST /api/tasks` durante uma sessão de chat
- **Sem alterações de código** — Adicione, remova ou modifique tarefas em tempo de execução
- **Ativar/desativar** — Pause tarefas sem excluí-las (`PATCH /api/tasks/{id}`)

### Automação CI/CD
- **Gatilhos de webhook** — Dispare tarefas do Claude Code a partir do GitHub Actions ou de qualquer sistema CI/CD
- **Auto-upgrade** — Atualiza o bot automaticamente quando pacotes upstream são lançados
- **Reinício DrainAware** — Aguarda sessões ativas terminarem antes de reiniciar
- **Marcação de auto-retomada** — Sessões ativas são automaticamente marcadas para retomada em qualquer desligamento (reinício por atualização via `AutoUpgradeCog`, ou qualquer outro desligamento via `ClaudeChatCog.cog_unload()`); na reinicialização, o Claude relata seu estado anterior e reconfirma com o usuário antes de retomar qualquer trabalho de implementação
- **Aprovação de reinício** — Barreira opcional para confirmar atualizações; aprove via reação ✅ na thread de atualização ou via botão publicado no canal pai; o botão se republica no final à medida que novas mensagens chegam para permanecer visível
- **Gatilho manual de atualização** — O comando slash `/upgrade` permite que usuários autorizados disparem o pipeline de atualização diretamente do Discord (opt-in via `slash_command_enabled=True`)

### Gerenciamento de Sessão
- **Ajuda integrada** — `/help` mostra todos os comandos slash disponíveis e o uso básico (efêmero, visível apenas para quem chama)
- **Sincronização de sessão** — Importa sessões CLI como threads do Discord (`/sync-sessions`); `/sync-settings` para ver ou alterar as preferências de sincronização (estilo de thread, janela de tempo, resultados mínimos)
- **Lista de sessões** — `/sessions` com filtragem por origem (Discord / CLI / todas) e janela de tempo
- **Retomar sessão** — `/resume` mostra um menu de seleção de sessões recentes (até 25) e retoma a selecionada em uma nova thread; parâmetro `query` opcional para busca por palavra-chave (compara resumo e diretório de trabalho); `filter=orphaned` opcional para mostrar apenas sessões de threads excluídas; funciona de qualquer canal ou thread — sempre cria uma nova thread no canal principal configurado
- **Info de retomada** — `/resume-info` mostra o comando CLI para continuar a sessão atual em um terminal (apenas em thread)
- **Limpar sessão** — `/clear` redefine a sessão do Claude Code para a thread atual, começando do zero sem criar uma nova thread
- **Retomada na inicialização** — Sessões interrompidas reiniciam automaticamente após qualquer reboot do bot; `AutoUpgradeCog` (reinícios por atualização) e `ClaudeChatCog.cog_unload()` (todos os outros desligamentos) as marcam automaticamente, ou use `POST /api/mark-resume` manualmente
- **Spawn programático** — `POST /api/spawn` cria uma nova thread do Discord + sessão do Claude a partir de qualquer script ou subprocesso do Claude; retorna 201 não bloqueante imediatamente após a criação da thread
- **Injeção de ID de thread** — A variável de ambiente `DISCORD_THREAD_ID` é passada a cada subprocesso do Claude, permitindo que sessões criem sessões filhas via `$CCDB_API_URL/api/spawn`
- **Exibição de StatusLine** — Se o seu `settings.json` do Claude Code tiver uma `statusLine` configurada, sua saída é mostrada no Discord após cada resposta de sessão
- **Gerenciamento de worktree** — `/worktree-list` mostra todos os worktrees de sessão ativos com status limpo/sujo; `/worktree-cleanup` remove worktrees limpos órfãos (suporta preview `dry_run`)
- **Troca de modelo em tempo de execução** — `/model-show` exibe o modelo global atual e o modelo de sessão por thread; `/model-set` altera o modelo para todas as novas sessões sem reiniciar
- **Permissões de ferramenta em tempo de execução** — `/tools-show` exibe as ferramentas permitidas atuais; `/tools-set` abre um menu de seleção para ligar/desligar ferramentas; `/tools-reset` reverte ao padrão do `.env` — tudo sem reiniciar
- **Uso de contexto** — `/context` mostra o percentual da janela de contexto com uma barra de progresso visual; aviso ⚠️ ao se aproximar do limiar de auto-compact de 83.5%; efêmero (visível apenas para quem chama)
- **Uso de limite de taxa** — `/usage` mostra a utilização do limite de taxa da API do Claude com barra de percentual e contagem regressiva do tempo até o reset para as janelas de 5 horas e 7 dias; sinalização ⚠️ quando a utilização ≥ 80%
- **Rebobinagem de conversa** — `/rewind` mostra um menu de seleção de turnos anteriores do usuário e trunca o JSONL da sessão no ponto escolhido, removendo aquela mensagem e tudo depois dela para que a sessão retome do estado exato anterior àquele turno; mantém todos os arquivos de trabalho que o Claude criou; útil quando uma sessão saiu dos trilhos
- **Bifurcação de conversa** — `/fork` ramifica a thread atual em uma nova thread que continua do mesmo estado de sessão via `--fork-session`, criando uma cópia de sessão verdadeiramente independente; permite explorar uma direção diferente sem afetar a original

### Segurança
- **Sem injeção de shell** — Apenas `asyncio.create_subprocess_exec`, nunca `shell=True`
- **Validação de ID de sessão** — Regex estrita antes de passar para `--resume`
- **Prevenção de injeção de flags** — Separador `--` antes de todos os prompts
- **Isolamento de secrets** — Token do bot removido do ambiente do subprocesso
- **Autorização de usuário** — `allowed_user_ids` restringe quem pode invocar o Claude
- **Prevenção de injeção de log** — Valores de API fornecidos pelo usuário são sanitizados (quebras de linha removidas) antes de serem escritos nos logs

---

## Início Rápido — Claude ou Codex no Discord em 5 Minutos

**Pré-requisitos:**

- Python 3.12+
- Pelo menos um dos seguintes:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — instalado e autenticado (`claude login`). Recomendado para assinantes Anthropic Pro/Max.
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` e depois `codex login`. Usa sua assinatura ChatGPT Plus/Pro/Business existente.
- Você pode instalar ambos. Alterne entre eles em tempo de execução com `/backend` (veja [Troca de Backend](#troca-de-backend--claude--codex-sob-demanda)).

**Suporte de plataforma:** Principalmente desenvolvido e testado no **Linux**. macOS e Windows são suportados e passam no CI, mas recebem menos testes no mundo real — relatos de bugs são bem-vindos.

### Passo 1 — Criar um Bot do Discord (uma vez, ~2 minutos)

1. Vá para [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. Navegue para **Bot** → habilite **Message Content Intent** em Privileged Gateway Intents
3. Copie o **Token** do bot
4. Vá para **OAuth2 → URL Generator**: Escopos `bot` + `applications.commands`, Permissões: Send Messages, Create Public Threads, Send Messages in Threads, Add Reactions, Manage Messages, Read Message History
5. Abra a URL gerada → convide o bot para seu servidor

### Passo 2 — Execute o Assistente de Configuração

Sem necessidade de clonar ou editar `.env` — o assistente faz isso por você:

```bash
# With uvx (no install needed):
uvx --from "git+https://github.com/ebibibi/ebi-agent-chat-relay.git" ccdb setup

# Or after cloning:
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv run ccdb setup
```

O assistente irá:
1. Validar seu token de bot na API do Discord
2. **Listar automaticamente os canais disponíveis** — basta escolher um número (sem copiar ID)
3. Perguntar seu diretório de trabalho e preferência de modelo
4. Escrever `.env` e oferecer para iniciar o bot imediatamente

```
╔══════════════════════════════════════════════════════╗
║          ccdb setup — interactive wizard             ║
╚══════════════════════════════════════════════════════╝

Step 1 — Claude Code CLI
  ✅  claude found

Step 2 — Discord Bot Token
  Bot Token: [paste here]
  Validating token… ✅  Logged in as MyBot#1234

Step 3 — Discord Channel ID
  Fetching channels via Discord API… ✅  Found 5 text channel(s)

   1. #general        (My Server)
   2. #claude-code    (My Server)
   3. #dev            (My Server)
   ...

  Select channel [1-5]: 2
  ✅  #claude-code (123456789012345678)

  ...

  ✅  Written: .env
  Start the bot now? [Y/n]: y
```

### Iniciar / Parar

```bash
ccdb start    # start the bot (reads .env in current dir)
ccdb start --env /path/to/.env   # custom .env location
```

Envie uma mensagem no canal configurado — o Claude responderá em uma nova thread.

### Executar como Serviço systemd (Produção)

Para implantações em produção, execute o bot sob systemd para que ele inicie no boot e reinicie automaticamente em caso de falha.

O repositório inclui um template pronto para adaptar (`discord-bot.service`) e um script de pré-início (`scripts/pre-start.sh`). Copie e personalize-os:

```bash
# 1. Edit the service file — replace /home/ebi and User=ebi with your paths/user
sudo cp discord-bot.service /etc/systemd/system/mybot.service
sudo nano /etc/systemd/system/mybot.service

# 2. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable mybot.service
sudo systemctl start mybot.service

# 3. Check status
sudo systemctl status mybot.service
journalctl -u mybot.service -f
```

**O que o `scripts/pre-start.sh` faz** (roda como `ExecStartPre` antes do processo do bot):

1. **`git pull --ff-only`** — puxa o código mais recente de `origin main`
2. **`uv sync`** — mantém as dependências em sincronia com `uv.lock`
3. **Validação de import** — verifica se `claude_discord.main` importa sem erros
4. **Auto-rollback** — se o import falhar, reverte para o commit anterior e tenta novamente; publica uma notificação via webhook do Discord em caso de falha ou sucesso
5. **Limpeza de worktree** — remove worktrees git obsoletos deixados por sessões que travaram

O script detecta a raiz do repositório dinamicamente (via `readlink -f` em `$0`), então funciona para qualquer usuário independentemente de onde clonaram o repositório — sem necessidade de editar caminhos no próprio script. Ele também descobre automaticamente o binário `uv` a partir do `PATH`; substitua via variável de ambiente `CCDB_UV_BIN` se necessário.

O script requer a variável `DISCORD_WEBHOOK_URL` no `.env` para notificações de falha (opcional — o script funciona sem ela).

#### PATH da Toolchain — configure no `.env`

O systemd inicia uma unit com um `PATH` padrão mínimo (tipicamente `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`) e nunca faz source de `~/.bashrc` ou `~/.profile`. O bot herda esse `PATH`, e o mesmo acontece com cada sessão Claude/Codex que ele cria — as sessões rodam com o ambiente do bot menos os secrets removidos.

O resultado é confuso: um build que funciona no seu terminal falha dentro de uma sessão do Discord, ou silenciosamente roda contra um binário mais antigo de todo o sistema, porque ferramentas instaladas em `~/.local/bin` ou `~/.npm-global/bin` são invisíveis para o serviço.

Como o serviço carrega `.env` via `EnvironmentFile=`, definir `PATH` ali corrige o bot e cada sessão de uma vez:

```bash
# .env — match your interactive shell's PATH
PATH=/home/you/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
```

Reinicie o serviço (`sudo systemctl restart mybot.service`), depois confirme a partir de uma sessão do Discord pedindo ao Claude para rodar `which node && node --version`.

### Cogs Personalizados (Estenda Sem Fork)

Adicione suas próprias funcionalidades colocando arquivos Python em um diretório — sem fork, sem subclasse, sem pacote necessário:

```bash
ccdb start --cogs-dir ./my-cogs/
# Or: CUSTOM_COGS_DIR=./my-cogs ccdb start
```

Cada arquivo `.py` no diretório deve expor um `async def setup(bot, runner, components)`:

```python
from discord.ext import commands

class GreeterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = self.bot.get_channel(self.bot.channel_id)
        await channel.send(f"Welcome {member.mention}!")

async def setup(bot, runner, components):
    await bot.add_cog(GreeterCog(bot))
```

Arquivos prefixados com `_` são ignorados. Se um Cog falhar ao carregar, os outros ainda carregam normalmente.

Veja [`examples/ebibot/`](examples/ebibot/) para um exemplo completo do mundo real com lembretes, watchdog do Todoist, auto-upgrade e sincronização de docs.

**Exemplos integrados em `examples/ebibot/cogs/`:**

| Cog | Finalidade |
|-----|------------|
| `ReminderCog` | Agendamento de lembretes baseado no Discord |
| `WatchdogCog` | Watchdog do Todoist / serviço externo |
| `AutoUpgradeCog` | Atualização de pacote disparada por webhook |
| `DocsSyncCog` | Sincronização automática de documentação no push |
| `AlertResponderCog` | Monitoramento genérico de alertas — encaminha alertas de sistemas de monitoramento para o Discord e dispara uma sessão de investigação do Claude Code |

---

### Bot Mínimo (Instalar como Pacote)

Se você já tem um bot discord.py, adicione o ccdb como um pacote:

```bash
uv add git+https://github.com/ebibibi/ebi-agent-chat-relay.git
```

Crie um `bot.py`:

```python
import asyncio
import os
from dotenv import load_dotenv
import discord
from discord.ext import commands
from claude_discord import ClaudeRunner, setup_bridge

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
runner = ClaudeRunner(
    command="claude",
    model="sonnet",
    working_dir="/path/to/your/project",
)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await setup_bridge(
        bot,
        runner,
        claude_channel_id=int(os.environ["DISCORD_CHANNEL_ID"]),
        allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
    )

asyncio.run(bot.start(os.environ["DISCORD_BOT_TOKEN"]))
```

`setup_bridge()` conecta todos os Cogs automaticamente. Atualize para a última versão:

```bash
uv lock --upgrade-package claude-code-discord-bridge && uv sync
```

#### Configuração Multi-Canal

Para implantar o bot em múltiplos canais do Discord, passe `claude_channel_ids` além de (ou em vez de) `claude_channel_id`:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_id=int(os.environ["DISCORD_CHANNEL_ID"]),   # primary (fallback for thread creation)
    claude_channel_ids={
        int(os.environ["DISCORD_CHANNEL_ID"]),
        int(os.environ["DISCORD_CHANNEL_ID_2"]),
    },
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Cada canal é totalmente independente — mensagens em qualquer um dos canais configurados criam uma nova thread de sessão do Claude, e os comandos `/skill` funcionam em todos eles.  `claude_channel_id` é mantido por compatibilidade e é usado como o alvo de criação de thread de fallback quando o comando `/skill` é invocado fora de um canal configurado.

#### Canais Somente-Menção (Mention-Only)

Para fazer o bot responder **apenas quando @mencionado** em canais específicos (útil para canais compartilhados onde você não quer que o bot reaja a cada mensagem):

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 222},
    mention_only_channel_ids={222},  # bot ignores messages in #222 unless @mentioned
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Ou via variável de ambiente (IDs de canal separados por vírgula):

```
MENTION_ONLY_CHANNEL_IDS=222,333
```

As threads **herdam a política do canal pai**. Uma thread que um humano cria em um canal somente-menção não inicia uma sessão do Claude — caso contrário, qualquer um poderia burlar a configuração apenas abrindo uma thread. O Claude engaja em tal thread apenas quando:

- o bot é explicitamente **@mencionado** na mensagem, ou
- o ccdb **já é dono da thread** — uma thread de sessão que o bot criou, ou uma criada via `/api/spawn`. Uma vez que uma sessão existe, cada resposta é tratada normalmente sem precisar de menção.

Threads em canais que *não* estão listados em `mention_only_channel_ids` não são afetadas e são sempre tratadas.

#### Canais de Resposta Inline (Inline-Reply)

Para fazer o bot responder **diretamente no canal** (sem criar uma thread) em canais específicos (útil para canais pessoais de comando onde threads adicionam desordem desnecessária):

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 333},
    inline_reply_channel_ids={333},  # bot replies inline in #333, no thread created
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Ou via variável de ambiente (IDs de canal separados por vírgula):

```
INLINE_REPLY_CHANNEL_IDS=333,444
```

No modo de resposta inline, a resposta do Claude é enviada diretamente como uma mensagem no canal em vez de criar uma nova thread. As sessões ainda são rastreadas internamente, então mensagens de acompanhamento no canal continuam a mesma sessão do Claude.

#### Canais Somente-Chat (Chat-Only)

Para ocultar a UI técnica (embeds de ferramentas, blocos de pensamento, avisos de início/conclusão de sessão, listas de tarefas) e mostrar **apenas as respostas de texto do Claude** em canais específicos — útil para canais voltados ao público onde usuários não técnicos estão assistindo:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 444},
    chat_only_channel_ids={444},  # only text shown in #444; tool details hidden
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Ou via variável de ambiente (IDs de canal separados por vírgula):

```
CHAT_ONLY_CHANNEL_IDS=444,555
```

No modo somente-chat, solicitações de permissão e prompts de `AskUserQuestion` são **sempre mostrados** independentemente da configuração — eles requerem entrada humana e devem ser visíveis.

---

## Configuração

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `DISCORD_BOT_TOKEN` | Seu token de bot do Discord | (obrigatório) |
| `DISCORD_CHANNEL_ID` | ID do canal para o chat do Claude | (obrigatório) |
| `CCDB_BACKEND` | Backend CLI a usar: `claude` (Claude Code CLI) ou `codex` (OpenAI Codex CLI) | `claude` |
| `CCDB_COMMAND` | Caminho ou nome do binário da CLI (substitui `CLAUDE_COMMAND`). Usado pelo runner inicial escolhido a partir de `CCDB_BACKEND`; substituído pelas duas variáveis por backend abaixo quando `/backend` troca em tempo de execução. | _(auto: `claude` ou `codex`)_ |
| `CCDB_CLAUDE_COMMAND` | Caminho explícito para o binário da CLI do Claude. Usado por `BackendFactory` sempre que `/backend claude` está ativo, independentemente do `CCDB_BACKEND` inicial. Recorre a `CLAUDE_COMMAND`, depois a `claude` (PATH). | (opcional) |
| `CCDB_CODEX_COMMAND` | Caminho explícito para o binário da CLI do OpenAI Codex. Obrigatório ao rodar o bot sob systemd (o PATH padrão do serviço não inclui `~/.npm-global/bin`). Recorre a `codex` (PATH). | (opcional) |
| `PATH` | Caminho de busca de binários para o bot **e cada sessão CLI que ele cria** — as sessões herdam o ambiente do bot. Defina-o no `.env` ao rodar sob systemd, que inicia units com um PATH mínimo e nunca lê `~/.bashrc` / `~/.profile`. Veja [PATH da Toolchain](#path-da-toolchain--configure-no-env). | (herdado do processo pai) |
| `CCDB_MODEL` | Modelo a usar (substitui `CLAUDE_MODEL`) | `sonnet` |
| `CCDB_PERMISSION_MODE` | Modo de permissão para a CLI (substitui `CLAUDE_PERMISSION_MODE`) | `acceptEdits` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | Pular todas as verificações de permissão — substitui `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | `false` |
| `CCDB_WORKING_DIR` | Diretório de trabalho para a CLI (substitui `CLAUDE_WORKING_DIR`) | diretório atual |
| `CCDB_ALLOWED_TOOLS` | Lista separada por vírgula de ferramentas permitidas (substitui `CLAUDE_ALLOWED_TOOLS`) | (opcional) |
| `CCDB_CHANNEL_IDS` | IDs de canal adicionais, separados por vírgula (substitui `CLAUDE_CHANNEL_IDS`) | (opcional) |
| `CLAUDE_COMMAND` | Caminho ou nome do binário da CLI do Claude (nome legado — prefira `CCDB_COMMAND`). Use para fixar uma versão específica (por exemplo, `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`) — útil para evitar regressões em versões mais novas da CLI. | `claude` |
| `CLAUDE_MODEL` | Modelo a usar (legado — prefira `CCDB_MODEL`) | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | Modo de permissão para a CLI (legado — prefira `CCDB_PERMISSION_MODE`) | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | Pular todas as verificações de permissão (legado — prefira `CCDB_DANGEROUSLY_SKIP_PERMISSIONS`) | `false` |
| `CLAUDE_WORKING_DIR` | Diretório de trabalho para o Claude (legado — prefira `CCDB_WORKING_DIR`) | diretório atual |
| `MAX_CONCURRENT_SESSIONS` | Máximo de sessões CLI paralelas do Claude em todos os caminhos de código (chat, habilidades, agendador, webhooks) | `3` |
| `SESSION_TIMEOUT_SECONDS` | Timeout de inatividade da sessão | `300` |
| `DISCORD_OWNER_ID` | ID de usuário a @-mencionar quando o Claude precisa de entrada | (opcional) |
| `COORDINATION_CHANNEL_ID` | ID de canal usado como fallback padrão para o canal do AI Lounge | (opcional) |
| `MENTION_ONLY_CHANNEL_IDS` | IDs de canal separados por vírgula onde o bot só responde quando @mencionado (as threads sob eles herdam a política) | (opcional) |
| `INLINE_REPLY_CHANNEL_IDS` | IDs de canal separados por vírgula onde o bot responde inline (sem criar thread) | (opcional) |
| `CHAT_ONLY_CHANNEL_IDS` | IDs de canal separados por vírgula em modo somente-chat — apenas as respostas de texto do Claude são mostradas; todos os embeds técnicos (ferramentas, pensamento, info de sessão, tarefas) são ocultados | (opcional) |
| `WORKTREE_BASE_DIR` | Diretório base para escanear worktrees de sessão (habilita limpeza automática) | (opcional) |
| `CLI_SESSIONS_PATH` | Caminho para `~/.claude/projects` para descoberta de sessões CLI (habilita `/sync-sessions`) | (opcional) |
| `CUSTOM_COGS_DIR` | Diretório contendo arquivos Cog personalizados a carregar na inicialização (veja [Cogs Personalizados](#cogs-personalizados-estenda-sem-fork)) | (opcional) |
| `CLAUDE_ALLOWED_TOOLS` | Lista separada por vírgula de ferramentas permitidas para a CLI do Claude (legado — prefira `CCDB_ALLOWED_TOOLS`) | (opcional) |
| `CLAUDE_CHANNEL_IDS` | IDs de canal adicionais (separados por vírgula) para configuração multi-canal (legado — prefira `CCDB_CHANNEL_IDS`) | (opcional) |
| `THREAD_INBOX_ENABLED` | Habilita a caixa de entrada de thread persistente (classifica sessões como `waiting`/`done`/`ambiguous` via `claude -p`; mostrada no dashboard de threads) | `false` |
| `THREAD_AUTO_RENAME` | Auto-renomear títulos de novas threads usando IA do Claude — gera um título curto e descritivo a partir da primeira mensagem do usuário via uma chamada `claude -p` em segundo plano (nunca atrasa o início da sessão) | `false` |
| `CCDB_CLI_ENV_FILE` | Caminho para um arquivo `KEY=VALUE` cujas variáveis são mescladas no ambiente do subprocesso da CLI em cada invocação. As mudanças têm efeito imediato sem reiniciar o bot. Útil para roteamento temporário de API (por exemplo, Azure Foundry) | (opcional) |
| `CCDB_LOG_FILE` | Caminho para um arquivo de log. Quando definido, um manipulador de arquivo rotativo (10 MB × 5 backups) é adicionado ao lado do manipulador stdout padrão. Útil para monitoramento e alertas. | (opcional) |
| `API_HOST` | Endereço de bind da REST API | `127.0.0.1` |
| `API_PORT` | Porta da REST API (habilita a REST API quando definida) | (opcional) |
| `CCDB_INGEST_TOKEN` | Token Bearer para `POST /api/ingest` (independente de `api_secret`); se não definido, o endpoint responde `503` | (opcional) |
| `CCDB_INGEST_REQUIRE_COMPLETE` | Defina como `1` para recusar uma ingestão com `409` quando seu `attachments_manifest` provar que anexos se perderam, em vez de iniciar uma sessão com evidências parciais | `0` |

### Modos de Permissão — O Que Funciona no Modo `-p`

A CLI do Claude Code roda em **modo `-p` (não interativo)** quando usada através do ccdb. Nesse modo, a CLI **não pode solicitar permissão** — ferramentas que exigem aprovação são imediatamente rejeitadas. Isso é uma [restrição de design da CLI](https://code.claude.com/docs/en/headless), não uma limitação do ccdb.

| Modo | Comportamento no modo `-p` | Recomendação |
|------|----------------------------|--------------|
| `default` | ❌ **Todas as ferramentas rejeitadas** — inutilizável | Não usar |
| `acceptEdits` | ⚠️ Edit/Write auto-aprovados, Bash rejeitado (o Claude recorre a Write para operações de arquivo) | Opção mínima viável |
| `bypassPermissions` | ✅ Todas as ferramentas aprovadas | Funciona, mas prefira a flag abaixo |
| **`auto`** | ✅ **Segurança classificada por IA** — operações seguras auto-aprovadas, operações perigosas bloqueadas | **Recomendado** — melhor equilíbrio entre segurança e usabilidade |
| `plan` | ✅ Classificada por IA (viés somente-leitura) — similar ao auto, mas mais conservador | Para fluxos com muita leitura |
| **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`** | ✅ **Todas as ferramentas aprovadas, sem verificações de segurança** | Modo "yolo" legado — use quando o modo auto for restritivo demais |

**Nossa recomendação:** Defina `CLAUDE_PERMISSION_MODE=auto`. O modo auto usa um classificador de IA para aprovar automaticamente operações seguras (edições de arquivo, testes locais, git push para branch de trabalho) enquanto bloqueia as perigosas (force push, deploys em produção, vazamento de credenciais). Isso dá ao Claude total autonomia para o trabalho de desenvolvimento normal sem o risco de "vale tudo" do modo yolo.

**Fallback para o modo yolo:** Se o modo auto bloquear operações de que você precisa, defina `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`. Como o ccdb controla quem pode interagir com o Claude via `allowed_user_ids`, as verificações de permissão no nível da CLI adicionam atrito sem benefício de segurança significativo. O "dangerously" no nome reflete o aviso de propósito geral da CLI; no contexto do ccdb, onde o acesso já é restrito, é uma escolha prática.

> **Nota:** Quando `CLAUDE_PERMISSION_MODE` é definido como `auto` ou `plan`, `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` é automaticamente ignorado — esses modos têm seus próprios classificadores de segurança que seriam sobrepostos pela flag yolo.

**Para controle granular**, use `CLAUDE_ALLOWED_TOOLS` para permitir ferramentas específicas sem burlar totalmente as permissões:

```env
# Example: allow file operations and code execution, but not web access
CLAUDE_ALLOWED_TOOLS=Bash,Read,Write,Edit,Glob,Grep

# Example: read-only mode — Claude can explore but not modify
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
```

Nomes comuns de ferramentas: `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`. Defina `CLAUDE_PERMISSION_MODE=default` ao usar isso (outros modos podem sobrepor).

**Mudanças em tempo de execução via Discord:** Use `/tools-set` para alterar as ferramentas permitidas em tempo de execução sem reiniciar o bot. A configuração é persistida e tem efeito imediato para todas as novas sessões. Use `/tools-show` para ver a configuração atual, ou `/tools-reset` para reverter ao padrão do `.env`.

> **Botões de permissão no Discord:** Quando `CLAUDE_PERMISSION_MODE=default`, o Claude emite eventos `permission_request` e o ccdb exibe botões Permitir/Negar na thread. O stdin é sempre mantido aberto (modo de entrada stream-json) para que o bot possa enviar respostas de volta ao Claude. Se você estiver usando o modo `auto` ou `plan`, o Claude lida com as permissões automaticamente sem exigir interação do usuário. Quando `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true` (modo yolo), o ccdb **auto-aprova** qualquer evento `permission_request` imediatamente — nenhum botão Permitir/Negar é mostrado. Isso é uma solução alternativa para uma regressão da CLI (v2.1.78+, upstream [#35895](https://github.com/anthropics/claude-code/issues/35895)) onde `--dangerously-skip-permissions` falha em burlar a verificação de caminho sensível no nível de arquivo.

---

## Configuração do Bot do Discord

1. Crie uma nova aplicação no [Discord Developer Portal](https://discord.com/developers/applications)
2. Crie um bot e copie o token
3. Habilite **Message Content Intent** em Privileged Gateway Intents
4. Convide o bot com estas permissões:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Manage Messages (para limpeza de reações)
   - Read Message History

---

## Automação GitHub + Claude Code

### Exemplo: Sincronização Automática de Documentação

A cada push para `main`, o Claude Code:
1. Puxa as últimas mudanças e analisa o diff
2. Atualiza a documentação em inglês
3. Traduz para o japonês (ou quaisquer idiomas alvo)
4. Cria um PR com um resumo bilíngue
5. Habilita auto-merge — faz merge automaticamente quando o CI passa

**GitHub Actions:**

```yaml
# .github/workflows/docs-sync.yml
name: Documentation Sync
on:
  push:
    branches: [main]
jobs:
  trigger:
    if: "!contains(github.event.head_commit.message, '[docs-sync]')"
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -X POST "${{ secrets.DISCORD_WEBHOOK_URL }}" \
            -H "Content-Type: application/json" \
            -d '{"content": "🔄 docs-sync"}'
```

**Configuração do bot:**

```python
from claude_discord import WebhookTriggerCog, WebhookTrigger, ClaudeRunner

runner = ClaudeRunner(command="claude", model="sonnet")

triggers = {
    "🔄 docs-sync": WebhookTrigger(
        prompt="Analyze changes, update docs, create a PR with bilingual summary, enable auto-merge.",
        working_dir="/home/user/my-project",
        timeout=600,
    ),
}

await bot.add_cog(WebhookTriggerCog(
    bot=bot,
    runner=runner,
    triggers=triggers,
    channel_ids={YOUR_CHANNEL_ID},
))
```

**Segurança:** Os prompts são definidos no lado do servidor. Os webhooks apenas selecionam qual gatilho disparar — sem injeção arbitrária de prompt.

### Exemplo: Auto-Aprovar PRs do Dono

```yaml
# .github/workflows/auto-approve.yml
name: Auto Approve Owner PRs
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  auto-approve:
    if: github.event.pull_request.user.login == 'your-username'
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: write
    steps:
      - env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          gh pr review "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --approve
          gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --auto --squash
```

---

## Tarefas Agendadas

Registre tarefas periódicas do Claude Code em tempo de execução — sem alterações de código, sem redeploys.

De dentro de uma sessão do Discord, o Claude pode registrar uma tarefa:

```bash
# Claude calls this inside a session:
curl -X POST "$CCDB_API_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Check for outdated deps and open an issue if found", "interval_seconds": 604800}'
```

Ou registre a partir dos seus próprios scripts:

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Weekly security scan", "interval_seconds": 604800}'
```

O loop mestre de 30 segundos captura as tarefas vencidas e cria sessões do Claude Code automaticamente.

---

## Auto-Upgrade

Atualize automaticamente o bot quando um novo release é publicado:

```python
from claude_discord import AutoUpgradeCog, UpgradeConfig

config = UpgradeConfig(
    package_name="claude-code-discord-bridge",
    trigger_prefix="🔄 bot-upgrade",
    working_dir="/home/user/my-bot",
    restart_command=["sudo", "systemctl", "restart", "my-bot.service"],
    restart_approval=True,       # React ✅ in thread, or click button in channel
    slash_command_enabled=True,  # Enable /upgrade slash command (opt-in, default False)
)

await bot.add_cog(AutoUpgradeCog(bot, config))
```

#### Gatilho Manual via `/upgrade`

Quando `slash_command_enabled=True`, qualquer usuário autorizado pode rodar `/upgrade` diretamente no Discord para disparar o mesmo pipeline de atualização — sem necessidade de webhook. O comando funciona tanto em canais de texto quanto em threads (rodá-lo dentro de uma thread cria a thread de atualização no canal pai). Ele respeita as barreiras `upgrade_approval` e `restart_approval`, cria uma thread de progresso e lida graciosamente com execuções concorrentes (responde de forma efêmera se uma atualização já estiver em andamento).

Antes de reiniciar, o `AutoUpgradeCog`:

1. **Captura snapshot das sessões ativas** — Coleta todas as threads com sessões do Claude em execução (duck-typed: qualquer Cog com um dict `_active_runners` é descoberto automaticamente).
2. **Drena** — Aguarda as sessões ativas terminarem naturalmente.
3. **Marca para retomada** — Salva os IDs das threads ativas na tabela de retomadas pendentes. Na próxima inicialização, essas sessões são retomadas com um prompt de segurança em primeiro lugar: o Claude relata no que estava trabalhando e pede ao usuário para reconfirmar antes de retomar qualquer trabalho de implementação (mudanças de código, commits, PRs). Isso evita ações não intencionais depois que a compressão de contexto pode ter apagado o estado de aprovação da tarefa.
4. **Reinicia** — Executa o comando de reinício configurado.

Qualquer Cog com uma propriedade `active_count` é descoberto automaticamente e drenado:

```python
class MyCog(commands.Cog):
    @property
    def active_count(self) -> int:
        return len(self._running_tasks)
```

A marcação de sessão é totalmente opt-in — ela só ativa quando `setup_bridge()` inicializou o banco de dados de sessões (o padrão). Quando habilitada, as sessões retomam com a continuidade `--resume` para que o Claude Code possa continuar exatamente a conversa de onde parou.

> **Cobertura:** `AutoUpgradeCog` cobre reinícios disparados por atualização. Para *todos os outros* desligamentos (`systemctl stop`, `bot.close()`, SIGTERM), `ClaudeChatCog.cog_unload()` fornece uma segunda rede de segurança automática.

---

## REST API

REST API opcional para notificações e gerenciamento de tarefas. Requer aiohttp:

```bash
uv add "claude-code-discord-bridge[api]"
```

### Endpoints

| Método | Caminho | Descrição |
|--------|---------|-----------|
| GET | `/api/health` | Verificação de saúde |
| POST | `/api/notify` | Enviar notificação imediata |
| POST | `/api/schedule` | Agendar uma notificação |
| GET | `/api/scheduled` | Listar notificações pendentes |
| DELETE | `/api/scheduled/{id}` | Cancelar uma notificação |
| POST | `/api/tasks` | Registrar uma tarefa agendada do Claude Code |
| GET | `/api/tasks` | Listar tarefas registradas |
| DELETE | `/api/tasks/{id}` | Remover uma tarefa |
| PATCH | `/api/tasks/{id}` | Atualizar uma tarefa (ativar/desativar, mudar agendamento) |
| POST | `/api/spawn` | Criar uma nova thread do Discord e iniciar uma sessão do Claude Code (não bloqueante); passe `auto_start: false` para adiar o Claude até a primeira resposta do usuário |
| POST | `/api/ingest` | Spawn externo autenticado (extensão de navegador / webhook) com anexos base64; retorna um `result_id` quando a recuperação de resultados está configurada |
| GET | `/api/ingest/{result_id}` | Fazer polling da resposta final da sessão criada (`status`/`result`/`error`/`thread_id`) |
| POST | `/api/mark-resume` | Marcar uma thread para retomada automática na próxima inicialização do bot |
| GET | `/api/lounge` | Ler mensagens recentes do AI Lounge |
| POST | `/api/lounge` | Publicar uma mensagem no AI Lounge (com `label` opcional) |
| GET | `/api/sessions` | Listar cada sessão — viva e armazenada — com estado, diretório de trabalho e nota de lounge mais recente (`state=running`, `exclude_thread`, `limit`) |
| GET | `/api/threads/{thread_id}/messages` | Ler a conversa de outra thread, das mais antigas primeiro (`limit`) |
| POST | `/api/claims` | Reivindicar um recurso antes de trabalhar nele — 201 quando adquirido, 409 com o detentor quando já tomado |
| GET | `/api/claims` | Listar reivindicações ao vivo (filtro `resource` opcional) |
| DELETE | `/api/claims` | Liberar uma reivindicação (`resource`, `thread_id`, `force=true` opcional) |
| POST | `/api/threads/{thread_id}/message` | Retransmitir uma mensagem de uma sessão para outra (`text`, `from_thread`, `mode`, `hop`) |

```bash
# Send notification (embed format, default)
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "Build succeeded!", "title": "CI/CD"}'

# Send plain text notification (no embed)
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "Deployment done!", "format": "text"}'

# Send a Discord Poll
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Vote now",
    "poll": {
      "question": "Which release track?",
      "answers": ["Stable", "Beta", "Nightly"],
      "duration_hours": 24,
      "allow_multiselect": false
    }
  }'

# Register a recurring task
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Daily standup summary", "interval_seconds": 86400}'
```

---

## Arquitetura

```
claude_code_core/          # Shared core library (backend-agnostic)
  backend.py               # SessionBackend protocol + create_backend() factory
  codex_runner.py          # OpenAI Codex CLI backend
  runner.py                # Claude CLI subprocess manager
  parser.py                # stream-json event parser
  types.py                 # Type definitions for SDK messages
  models.py                # SQLite schema
  session_repo.py          # Session CRUD
  lounge_repo.py           # AI Lounge message CRUD
  rewind.py                # Session rewind helpers
claude_discord/
  main.py                  # Standalone entry point (setup_bridge + custom cog loader)
  cli.py                   # CLI entry point (ccdb setup/start commands)
  setup.py                 # setup_bridge() — one-call Cog wiring
  cog_loader.py            # Dynamic custom Cog loader (CUSTOM_COGS_DIR)
  bot.py                   # Discord Bot class
  protocols.py             # Shared protocols (DrainAware)
  frontend.py              # DiscordFrontend — resolve/create a conversation by key
  stores.py                # build_session_stores() — every repo, no frontend
  concurrency.py           # Worktree instructions + active session registry
  collision.py             # File-write tracking + collision rules (pure, clock-injected)
  lounge.py                # AI Lounge prompt builder
  session_view.py          # Cross-session views for GET /api/sessions (pure merge logic)
  relay.py                 # RelayGuard + relay prompt wrapper (hop/cooldown/rate limits)
  session_sync.py          # CLI session discovery and import
  worktree.py              # WorktreeManager — safe git worktree lifecycle
  cogs/
    claude_chat.py         # Interactive chat (thread creation, message handling)
    skill_command.py       # /skill slash command with autocomplete
    session_manage.py      # /sessions, /sync-sessions, /resume, /resume-info, /sync-settings
    session_sync.py        # Thread-creation and message-posting logic for sync-sessions
    prompt_builder.py      # build_prompt_and_images() — pure function, no Cog/Bot state
    scheduler.py           # Periodic Claude Code task executor
    webhook_trigger.py     # Webhook → Claude Code task execution (CI/CD)
    auto_upgrade.py        # Webhook → package upgrade + drain-aware restart
    collision_watch.py     # Announces sessions writing the same files (60s loop)
    event_processor.py     # EventProcessor — state machine for stream-json events
    run_config.py          # RunConfig dataclass — bundles all CLI execution params
    _run_helper.py         # Thin orchestration layer (run_claude_with_config + shim)
  claude/
    runner.py              # Re-exports ClaudeRunner from claude_code_core
    parser.py              # Re-exports parse_line from claude_code_core
    types.py               # Re-exports type definitions from claude_code_core
  database/
    models.py              # SQLite schema
    repository.py          # Session CRUD
    task_repo.py           # Scheduled task CRUD
    ask_repo.py            # Pending AskUserQuestion CRUD
    notification_repo.py   # Scheduled notification CRUD
    lounge_repo.py         # AI Lounge message CRUD
    claims_repo.py         # Advisory resource claim CRUD (TTL-bound)
    resume_repo.py         # Startup resume CRUD (pending resumes across bot restarts)
    settings_repo.py       # Per-guild settings
    frontend_thread_repo.py  # ThreadKey → where the conversation lives
    inbox_repo.py          # Thread inbox CRUD (THREAD_INBOX_ENABLED)
  discord_ui/
    status.py              # Emoji reaction manager (debounced)
    chunker.py             # Fence- and table-aware message splitting
    embeds.py              # Discord embed builders
    views.py               # Stop button and shared UI components
    prompt_views.py        # ChoiceView / FormModal — renders the protocol's prompts
    mentions.py            # user_mention_kwargs() — notify requester when Claude pauses for input
    ask_bus.py             # Event bus for AskUserQuestion communication
    ask_view.py            # Buttons/Select Menus for AskUserQuestion
    ask_handler.py         # collect_ask_answers() — AskUserQuestion UI + DB lifecycle
    streaming_manager.py   # StreamingMessageManager — debounced in-place message edits
    tool_timer.py          # LiveToolTimer — elapsed time counter for long-running tools
    thread_dashboard.py    # Live pinned embed showing session states
    file_sender.py         # File delivery via .ccdb-attachments
    inbox_classifier.py    # classify() — lightweight claude -p call to label sessions
    thread_renamer.py      # suggest_title() — background claude -p call for auto thread naming
  ext/
    api_server.py          # REST API (optional, requires aiohttp)
    ingest_manifest.py     # Concilia attachments_manifest com os arquivos entregues
  utils/
    logger.py              # Logging setup
examples/
  ebibot/                  # Real-world example: personal bot with custom Cogs
    cogs/
      reminder.py          # /remind slash command + scheduled notifications
      watchdog.py          # Todoist overdue task monitor
      auto_upgrade.py      # Self-update via GitHub webhook
      docs_sync.py         # Auto-translate docs on push
```

### Filosofia de Design

- **CLI spawn, não API** — Invoca `claude -p --output-format stream-json`, oferecendo todos os recursos do Claude Code (CLAUDE.md, habilidades, ferramentas, memória) sem reimplementá-los. Roda na sua assinatura Claude Pro/Max — sem API key, sem cobrança por token
- **Concorrência em primeiro lugar** — Múltiplas sessões simultâneas são o caso esperado, não um caso excepcional; cada sessão recebe instruções de worktree, e o registro e o AI Lounge cuidam do resto
- **Discord como cola** — O Discord fornece UI, threading, reações, webhooks e notificações persistentes; nenhum frontend personalizado necessário
- **Framework, não aplicação** — Instale como pacote, adicione Cogs ao seu bot existente, configure via código
- **Extensibilidade sem código** — Adicione tarefas agendadas e gatilhos de webhook sem tocar no código-fonte
- **Segurança por simplicidade** — ~8000 linhas de Python auditável; apenas subprocess exec, sem expansão de shell

---

## Testes

```bash
uv run pytest tests/ -v --cov=claude_discord
```

Mais de 1690 testes cobrindo analisador (parser), chunker, repositório, runner, streaming, gatilhos de webhook, auto-upgrade (incluindo o comando slash `/upgrade`, invocação em thread e botão de aprovação), REST API, UI do AskUserQuestion, dashboard de thread, tarefas agendadas, sincronização de sessão, AI Lounge, observabilidade entre sessões, reivindicações de recursos, relay entre sessões, retomada na inicialização, troca de modelo, detecção de compactação, embeds de progresso do TodoWrite, carregador de Cogs personalizados, análise de eventos de permissão/elicitation/modo-plan, classificação de caixa de entrada de thread, comportamento de lock por thread, protocolo SessionBackend, CodexRunner, fábrica de backends e propriedade de sessão entre backends.

---

## Como Este Projeto Foi Construído

**Este codebase é desenvolvido pelo [Claude Code](https://docs.anthropic.com/en/docs/claude-code)**, o agente de codificação de IA da Anthropic, sob a direção de [@ebibibi](https://github.com/ebibibi). O autor humano define os requisitos, revisa os pull requests e aprova todas as mudanças — o Claude Code faz a implementação.

Isso significa:

- **A implementação é gerada por IA** — arquitetura, código, testes, documentação
- **A revisão humana é aplicada no nível do PR** — cada mudança passa por pull requests do GitHub e CI antes do merge
- **Relatos de bugs e PRs são bem-vindos** — o Claude Code será usado para tratá-los
- **Este é um exemplo do mundo real de software de código aberto dirigido por humanos e implementado por IA**

O projeto começou em 2026-02-18 e continua a evoluir através de conversas iterativas com o Claude Code.

---

## Exemplo do Mundo Real

**[`examples/ebibot/`](examples/ebibot/)** — Um bot pessoal do Discord construído sobre este framework, incluído diretamente neste repositório. Demonstra o carregador de Cogs personalizados com:

- **ReminderCog** — Comando slash `/remind HH:MM "message"` + loop de envio de 30 segundos
- **WatchdogCog** — Monitor de tarefas vencidas do Todoist (verificação a cada 30 minutos, dedup diária, alertas baseados em severidade)
- **AutoUpgradeCog** — Auto-atualização via webhook do GitHub + systemctl restart
- **DocsSyncCog** — Traduz automaticamente a documentação no push via webhook
- **AlertResponderCog** — Cog genérico de monitoramento de alertas; observa uma fonte configurável e publica notificações anotadas por severidade no Discord

Execute com: `ccdb start --cogs-dir examples/ebibot/cogs/`

> Os Cogs personalizados do EbiBot eram mantidos anteriormente em um [repositório separado](https://github.com/ebibibi/discord-bot). Agora estão co-localizados aqui para que o Claude Code sempre tenha contexto completo tanto do framework quanto das customizações — evitando duplicação acidental de funcionalidades.

---

## Inspirado Por

- [OpenClaw](https://github.com/openclaw/openclaw) — Reações de status emoji, debouncing de mensagens, chunking ciente de fence
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — Abordagem CLI spawn + stream-json
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — Padrões de controle de permissão
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — Modelo de conversa por thread

---

## Licença

MIT
