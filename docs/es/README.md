> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../../README.md) takes precedence.
> **Nota:** Esta es una versión autotraducida de la documentación original en inglés.
> En caso de discrepancias, la [versión en inglés](../../README.md) prevalece.

# Claude & Codex Discord Bridge

*Nombre del paquete: `claude-code-discord-bridge` (kebab-case)*

[![CI](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Usa Claude Code _o_ OpenAI Codex desde tu teléfono. Múltiples hilos. Todo a la vez. Desarrollo real incluido.**

Abre Claude Code o OpenAI Codex desde la aplicación Discord de tu smartphone, inicia múltiples hilos y ejecuta sesiones de desarrollo en paralelo — todo sin tocar un teclado. Cada hilo de Discord se convierte en una sesión de IA completamente aislada. Trabaja en una función en un hilo, revisa un PR en otro y ejecuta una tarea en segundo plano en un tercero — simultáneamente, incluso mezclando backends por hilo. El bridge gestiona toda la coordinación para que las sesiones nunca se pisen entre sí.

**Usa tus suscripciones existentes. Sin lidiar con API keys.** ccdb funciona sobre las CLIs oficiales — Claude Code (incluida en tu [suscripción Claude Pro/Max](https://claude.ai/pricing)) y OpenAI Codex (incluida en [ChatGPT Plus/Pro/Business](https://chatgpt.com)). Cambia de backend con `/backend` o define una anulación por hilo — tu equipo obtiene ambas IAs a través de Discord a un costo predecible.

**[English](../../README.md)** | **[日本語](../ja/README.md)** | **[简体中文](../zh-CN/README.md)** | **[한국어](../ko/README.md)** | **[Português](../pt-BR/README.md)** | **[Français](../fr/README.md)**

> **Descargo de responsabilidad:** Este proyecto no está afiliado, respaldado ni tiene relación oficial con Anthropic u OpenAI. "Claude" y "Claude Code" son marcas comerciales de Anthropic, PBC; "OpenAI", "Codex" y "ChatGPT" son marcas comerciales de OpenAI. Esta es una herramienta de código abierto independiente que interactúa con Claude Code CLI y OpenAI Codex CLI.

> **Construido enteramente por Claude Code.** Todo este codebase — arquitectura, implementación, pruebas, documentación — fue escrito por el propio Claude Code. El autor humano proporcionó requisitos y dirección mediante lenguaje natural. Ver [Cómo se construyó este proyecto](#cómo-se-construyó-este-proyecto).

---

## La Gran Idea: Sesiones Paralelas Sin Miedo

Cuando envías tareas a Claude Code o a OpenAI Codex en hilos de Discord separados, el bridge hace cuatro cosas automáticamente — sin importar qué backend hayas elegido:

1. **Inyección de aviso de concurrencia** — El prompt del sistema de cada sesión incluye instrucciones obligatorias: crear un git worktree, trabajar solo dentro de él, nunca tocar el directorio de trabajo principal directamente.

2. **Registro de sesiones activas** — Cada sesión en ejecución conoce a las demás. Si dos sesiones están por tocar el mismo repositorio, pueden coordinarse en lugar de entrar en conflicto.

3. **AI Lounge** — Una "sala de descanso" de sesión a sesión inyectada en cada prompt. Antes de empezar, cada sesión lee los mensajes recientes del lounge para ver qué están haciendo las otras sesiones, y reclama el repositorio, issue o archivo que está por tocar (ver [Reclamaciones de Recursos](#reclamaciones-de-recursos)) para que una segunda sesión sea rechazada antes de duplicar el trabajo. Antes de operaciones disruptivas (force push, reinicio del bot, eliminación de DB), las sesiones consultan primero el lounge para no arruinar el trabajo de las demás.

4. **Superficie agnóstica al backend** — La misma UI de Discord, comandos slash, scheduler, API y Lounge funcionan igual tanto si un hilo ejecuta Claude como Codex. Mezcla backends entre hilos si quieres — p. ej. Claude para refactorizaciones, Codex para revisión de código — usando `/backend` por hilo.

```
Hilo A (función)    ──→  Claude Code  (worktree-A)  ─┐
Hilo B (PR review)  ──→  OpenAI Codex (worktree-B)   ├─→  #ai-lounge
Hilo C (docs)       ──→  Claude Code  (worktree-C)  ─┘    "A: refactor de auth en progreso"
                                                          "B: revisión del PR #42 lista (codex)"
                                                          "C: actualizando README"
```

Sin condiciones de carrera. Sin trabajo perdido. Sin sorpresas en los merges. Sin quedar atado a un solo backend.

---

## Qué Puedes Hacer

### Chat Interactivo (Móvil / Escritorio)

Usa Claude Code _o_ OpenAI Codex desde cualquier lugar donde Discord funcione — teléfono, tablet o escritorio. Cada mensaje crea o continúa un hilo que mapea 1:1 a una sesión de IA persistente. Cambia de backend en cualquier momento con `/backend claude` o `/backend codex` — por hilo, o globalmente como el nuevo valor por defecto.

### Desarrollo Paralelo

Abre múltiples hilos simultáneamente. Cada uno es una sesión de IA independiente — Claude Code o Codex — con su propio contexto, directorio de trabajo y git worktree. Patrones útiles:

- **Función + revisión en paralelo**: Inicia una función con Claude en un hilo mientras Codex revisa el PR en otro.
- **Múltiples contribuidores**: Cada miembro del equipo obtiene su propio hilo (y su backend preferido); las sesiones se mantienen conscientes entre sí a través del AI Lounge.
- **Experimenta con seguridad**: Prueba un enfoque en el hilo A mientras mantienes el hilo B en código estable.
- **A/B del mismo prompt en ambas IAs**: Genera dos hilos con la misma tarea, uno en `/backend claude` y otro en `/backend codex`, y luego compara los diffs lado a lado.

### Tareas Programadas (SchedulerCog)

Registra tareas periódicas de Claude Code desde una conversación de Discord o mediante REST API — sin cambios de código, sin redespliegues. Las tareas se almacenan en SQLite y se ejecutan en un horario configurable. Claude puede autorregistrar tareas durante una sesión usando `POST /api/tasks`.

```
/skill name:goodmorning         → ejecuta inmediatamente
Claude llama POST /api/tasks    → registra una tarea periódica
SchedulerCog (ciclo maestro 30s)→ dispara tareas vencidas automáticamente
```

### Automatización CI/CD

Dispara tareas de Claude Code desde GitHub Actions a través de webhooks de Discord. Claude se ejecuta de forma autónoma — lee código, actualiza documentación, crea PRs, habilita auto-merge.

```
GitHub Actions → Discord Webhook → Bridge → Claude Code CLI
                                                  ↓
GitHub PR ←── git push ←── Claude Code ──────────┘
```

**Ejemplo real:** En cada push a `main`, Claude analiza el diff, actualiza la documentación en inglés + japonés, crea un PR bilingüe y habilita auto-merge. Cero interacción humana.

### Sincronización de Sesiones

¿Ya usas Claude Code CLI directamente? Sincroniza tus sesiones de terminal existentes en hilos de Discord con `/sync-sessions`. Rellena los mensajes de conversación recientes para que puedas continuar una sesión CLI desde tu teléfono sin perder el contexto.

### AI Lounge

Un canal compartido de "sala de descanso" donde todas las sesiones concurrentes se anuncian, leen las actualizaciones de las demás y se coordinan antes de operaciones disruptivas.

Cada sesión recibe el contexto del lounge automáticamente como instrucciones efímeras de sistema/desarrollador (`--append-system-prompt` para Claude, `developer_instructions` para Codex), en lugar de como parte del historial de conversación. Esto evita que el contexto se acumule turno a turno, lo que de otro modo causaría errores de "Prompt is too long" en sesiones de larga duración. El contexto inyectado incluye los mensajes recientes de otras sesiones más la regla de verificar antes de hacer cualquier cosa destructiva.

```bash
# Sessions post their intentions before starting:
curl -X POST "$CCDB_API_URL/api/lounge" \
  -H "Content-Type: application/json" \
  -d '{"message": "Starting auth refactor on feature/oauth — worktree-A", "label": "feature dev"}'

# Read recent lounge messages (also injected into each session automatically):
curl "$CCDB_API_URL/api/lounge"
```

El canal del lounge funciona además como un feed de actividad visible para humanos — ábrelo en Discord para ver de un vistazo qué está haciendo actualmente cada sesión activa de Claude.

### Observabilidad Entre Sesiones

Una nota del lounge le dice a una sesión *que* existe otro hilo. Estos dos endpoints de solo lectura le permiten ir a mirar — para que dos sesiones que empezaron en la misma tarea puedan descubrir el solapamiento en lugar de avanzar ambas a ciegas.

```bash
# Who else is alive, where are they working, what did they last announce?
curl "$CCDB_API_URL/api/sessions?exclude_thread=$DISCORD_THREAD_ID"

# Read that thread's actual conversation
curl "$CCDB_API_URL/api/threads/1529338965000192110/messages?limit=30"
```

`/api/sessions` combina tres fuentes: la tabla `sessions` (created_at, directorio de trabajo, backend), el registro en memoria (lo que cada sesión activa está haciendo *justo ahora*) y la última nota del lounge de cada hilo. Una sesión aparece con `"state": "running"` mientras un turno está en curso — incluidas las sesiones que nunca publicaron en el lounge, que es exactamente cuando esto importa. Una conversación guardada sin un turno en curso aparece con `"state": "history"`: está disponible para reanudarse, pero no significa que un agente esté esperando trabajo o la intervención del usuario. Las sesiones no tienen token propio de Discord, así que el bot realiza la lectura y los endpoints permanecen en el plano de control de localhost.

### Reclamaciones de Recursos

La observabilidad le dice a una sesión que *ocurrió* una colisión. Una reclamación la previene — sin lecturas, sin negociación, sin ida y vuelta al LLM. Una sesión reclama aquello en lo que está por trabajar; la siguiente sesión que pida lo mismo es rechazada antes de hacer trabajo alguno.

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

Las reclamaciones son **consultivas** — nada las hace cumplir a nivel de git ni del sistema de archivos — y cada reclamación lleva un TTL (por defecto 2 h, máximo 24 h) para que una sesión que muere no pueda inmovilizar un recurso para siempre. El cuerpo del 409 informa si el titular sigue en ejecución, que es como quien llama decide si esperar, trabajar en otra cosa o tomar el control con `force=true`. Los nombres de recursos son de forma libre y se normalizan (mayúsculas/minúsculas y espacios en blanco), así que `repo:ccdb` y `Repo: CCDB` son la misma reclamación.

El prompt del lounge indica a cada sesión que reclame antes de empezar y que libere al terminar.

### Retransmisión de Sesión a Sesión

La observabilidad permite que una sesión vea a un par; una reclamación las mantiene separadas. Cuando dos sesiones ya han colisionado, necesitan hablar de verdad — y una de ellas necesita detenerse.

```bash
curl -X POST "$CCDB_API_URL/api/threads/<their_thread_id>/message" \
  -H "Content-Type: application/json" \
  -d '{"text": "I started this at 13:02 on branch fix/parser and already pushed 3 commits.",
       "from_thread": "'$DISCORD_THREAD_ID'", "mode": "queue", "hop": 0}'
```

`on_message` ignora todo lo que escriba un bot — esa protección es lo que impide que el bot se hable a sí mismo — así que las retransmisiones pasan por este endpoint en su lugar, del mismo modo que lo hace `/api/spawn`.

- **`mode: "queue"`** (por defecto) espera a que termine el turno actual del receptor.
- **`mode: "interrupt"`** envía SIGINT al turno en curso, de modo que "detente ahora" llega en segundos. Puede costarle al receptor trabajo sin commit, así que se reserva para conflictos reales.
- El texto retransmitido se **publica en el hilo** antes de llegar a Claude, para que los humanos que observan vean todo el intercambio entre IAs. Una retransmisión nunca es un canal encubierto.
- Cada mensaje se **envuelve en un marcador** que nombra el hilo emisor e indica que no proviene del humano — una instrucción sin marcar sería obedecida como si la hubiera escrito el propietario.

Los bucles son el riesgo real (dos sesiones respondiéndose entre sí queman tokens y se interrumpen indefinidamente), así que una protección acota cada cadena: **máximo 2 saltos**, un enfriamiento de 60 s por par de hilos, 5 retransmisiones por emisor cada 10 minutos, y sin autoenvíos. Los rechazos vuelven como 429 con el motivo.

El prompt del lounge también da a las sesiones una regla de desempate para que la conversación converja en lugar de terminar en cortesía mutua: quien tenga commits o un PR gana a quien todavía está investigando; en caso contrario, continúa la sesión anterior; los empates se resuelven a favor del ID de hilo más bajo. Quien se retira empuja primero su rama y entrega lo que aprendió.

### Detección Automática de Colisiones

Tanto el lounge como las reclamaciones dependen de que una sesión *diga* algo. Esto atrapa los solapamientos que nadie anunció, a partir de lo que las sesiones realmente hicieron: si dos sesiones activas escriben en el mismo archivo dentro de 15 minutos, están trabajando en lo mismo, lo mencionaran o no.

`EventProcessor` registra la ruta de cada llamada a herramienta de tipo escritura (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`); `CollisionWatchCog` compara esos conjuntos entre sesiones activas una vez por minuto.

> Por qué rutas de archivos y no directorios de trabajo: en un host de un solo usuario, cada sesión tiende a empezar en el mismo directorio home, así que la igualdad de `working_dir` marca cada par y no significa nada. Un *archivo editado* compartido casi nunca es coincidencia. Las lecturas se ignoran deliberadamente — dos sesiones leyendo el mismo archivo es normal y ahogaría la señal.

Cuando se encuentra un solapamiento, el vigilante publica:

- una línea en el **AI Lounge**, que se inyecta en el siguiente turno de cada sesión sin costo de tokens y sin interrumpir nada, y
- un mensaje en **cada hilo en colisión**, nombrando al par, los archivos compartidos y los endpoints que lo resuelven.

Nunca retransmite dentro de una sesión en ejecución — interrumpir un turno por una mera sospecha costaría más que la colisión. Escalar es decisión de las sesiones, usando el endpoint de retransmisión de arriba. Cada par se anuncia como mucho una vez cada 30 minutos, porque una advertencia repetida cada minuto es una advertencia que todos aprenden a ignorar.

Se habilita automáticamente; permanece inactivo hasta que dos sesiones realmente se solapan.

### Creación de Sesiones Programática

Genera nuevas sesiones de Claude Code desde scripts, GitHub Actions u otras sesiones de Claude — sin interacción con mensajes de Discord.

```bash
# From another Claude session or a CI script:
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Run security scan on the repo", "thread_name": "Security Scan"}'
# Returns immediately with the thread ID; Claude runs in the background
```

**Inicio diferido (`auto_start=false`)** — Crea un hilo y publica un mensaje inicial sin iniciar Claude de inmediato. Claude inicia solo cuando un usuario responde, y recibe el mensaje inicial como contexto automáticamente.

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

Esto es útil para flujos de trabajo tipo notificación (p. ej. briefings diarios, alertas de CI) donde quieres mostrar información de antemano y dejar que el usuario decida si involucra a Claude.

Los subprocesos de Claude reciben `DISCORD_THREAD_ID` como variable de entorno, así que una sesión en ejecución puede generar sesiones hijas para paralelizar el trabajo.

### Ingesta Externa Autenticada con Recuperación de Resultados (`/api/ingest`)

`POST /api/ingest` es el **spawn autenticado y compatible con adjuntos** para clientes externos no confiables (extensiones de navegador, atajos móviles, webhooks). A diferencia de `/api/spawn` (confiable, localhost), requiere un `ingest_token` dedicado (configura `CCDB_INGEST_TOKEN`; independiente de `api_secret`) y puede llevar archivos adjuntos en base64 que se escriben en disco para que la sesión generada los lea. Crea un hilo real de Discord, por lo que toda la interacción permanece observable.

La sesión es **interactiva** (un hilo real de Discord en el que puedes seguir respondiendo) — pero aún puedes recuperar su respuesta final de forma programática. Cuando la recuperación de resultados está configurada (conectada automáticamente vía `setup_bridge()`), la respuesta incluye un `result_id`, y `GET /api/ingest/{result_id}` sondea la respuesta final de la sesión. Esa misma respuesta final también se adjunta al hilo de Discord como `ccdb-answer.md`, así que las integraciones pueden tratar el adjunto como la carga útil canónica de la respuesta. Este es el patrón de ida y vuelta: publicar un hilo + adjuntos → esperar → leer el archivo de respuesta o sondear el resultado → escribirlo de vuelta en tu propio sistema (p. ej. un hilo de Teams), mientras Discord conserva el historial.

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

El endpoint es opcional: sin un `ingest_token` configurado, `POST` responde `503`. Cuando la recuperación de resultados no está disponible, `POST` simplemente omite `result_id` y `GET /api/ingest/{id}` devuelve `503` — por lo demás, el comportamiento del spawn no cambia. El cuerpo de la solicitud y los adjuntos **no** se persisten en el almacén de resultados (solo el estado, el texto final y el ID del hilo); los resultados están limitados a 200 filas.

#### Entrega de adjuntos verificada (`attachments_manifest`)

Un adjunto que se perdía del lado del cliente era invisible: ccdb guardaba lo que le entregaban y reportaba un conteo que nadie podía comprobar, así que la sesión respondía como si tuviera un archivo que nunca recibió. Envía un **manifiesto** y ccdb **verifica** la entrega en lugar de darla por hecha — una entrada por cada adjunto que encontraste en el origen, con `status` indicando si sus bytes vienen en esta solicitud:

```bash
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "Redacta una respuesta",
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
| `embedded` (por defecto) | Los bytes vienen en esta solicitud — ccdb espera encontrar un archivo que coincida |
| `linked` | Solo se obtuvo una URL (sin permiso sobre el host, muro de autenticación, …) |
| `skipped` | No se envió deliberadamente (límite de tamaño, filtrado) |
| `failed` | La captura o la descarga falló |

ccdb empareja cada entrada `embedded` con un archivo entregado usando **primero el sha256**, luego el nombre exacto, luego el prefijo de índice tipo `4_image.png` que un empaquetador añade ante nombres en conflicto, y por último el tamaño — consumiendo cada archivo como máximo una vez, de modo que dos adjuntos llamados `image.png` no puedan reclamar ambos el único archivo que llegó. Todo lo que quede sin explicar se expone por cuatro vías: un bloque ⚠️ **al principio del prompt de la sesión** que nombra los archivos faltantes y le indica a la sesión que no invente su contenido (con un aviso adicional cuando la pérdida está en el mensaje más reciente), un libro mayor `ATTACHMENTS-REPORT.md` escrito junto a los archivos, el veredicto `attachments` de la respuesta anterior, y un `WARNING` en el log. Proporcionar `message` también agrupa la lista de rutas del prompt por mensaje de origen y marca el grupo más reciente como el que debe leerse primero.

Define `CCDB_INGEST_REQUIRE_COMPLETE=1` (o `ingest_require_complete=True`) para **rechazar** con `409` una ingesta con pérdidas en lugar de iniciar una sesión con evidencia parcial — apropiado cuando los adjuntos *son* la solicitud. Omite el manifiesto por completo y nada cambia: la ingesta se reporta como `verified: false`, nunca como verificada y completa.

### Reanudación al Inicio

Si el bot se reinicia a mitad de una sesión, las sesiones de Claude interrumpidas se reanudan automáticamente cuando el bot vuelve a estar en línea. Las sesiones se marcan para reanudar de tres formas:

- **Automática (reinicio por actualización)** — `AutoUpgradeCog` captura una instantánea de todas las sesiones activas justo antes de un reinicio por actualización de paquete y las marca automáticamente.
- **Automática (cualquier apagado)** — `ClaudeChatCog.cog_unload()` marca todas las sesiones en curso cada vez que el bot se apaga por cualquier mecanismo (`systemctl stop`, `bot.close()`, SIGTERM, etc.).
- **Manual** — Cualquier sesión puede llamar `POST /api/mark-resume` directamente.

### Cambio de Backend — Claude / Codex Bajo Demanda

ccdb 3.0 introduce tres comandos slash que cambian qué IA maneja la siguiente sesión, sin reiniciar el bot:

- `/backend [name] [scope]` — muestra o cambia el backend. `name` es `claude` o `codex`. `scope` es `thread` (solo este hilo) o `global` (predeterminado a nivel de servidor). Cuando omites `scope`, el comando se autorresuelve: dentro de un hilo lo aplica a ese hilo, de lo contrario establece el predeterminado global.
- `/model [name] [scope]` — muestra o cambia el modelo usado por el backend **actual**. Cada backend recuerda su propia preferencia de modelo, así que alternar de un backend a otro mantiene intactos tus modelos favoritos. Deja sin definir el modelo de un backend para diferir al valor por defecto de ese CLI (p. ej. Codex usa el `model` de `~/.codex/config.toml`, así que ccdb rastrea el valor por defecto de la consola en lugar de fijar una versión).
- `/effort [level] [scope]` — muestra o cambia el **esfuerzo de razonamiento** usado por el backend actual. Los niveles válidos son específicos del backend: Claude acepta `low/medium/high/max`; Codex acepta `low/medium/high/xhigh/max/ultra` (mapeado al `model_reasoning_effort` del CLI). Déjalo sin definir para diferir al valor por defecto del CLI.

Los tres comandos persisten en SQLite vía `SettingsRepository`, así que la elección sobrevive a los reinicios del bot. Llamarlos sin argumentos imprime el valor por defecto global actual más cualquier anulación por hilo.

**¿Qué le pasa a un hilo que ya tiene una sesión?** Los IDs de sesión no son interoperables entre los dos CLIs — entregar un ID de rollout de Codex a `claude --resume` (o un UUID de Claude a `codex exec resume`) falla a nivel del CLI. ccdb registra qué backend acuñó cada ID de sesión, así que un cambio nunca deja un hilo abandonado:

- **Cambio con alcance de hilo** — el ID de sesión almacenado se descarta para que el siguiente mensaje empiece de cero en el nuevo backend, *a menos que* se sepa que el registro pertenece al backend al que cambiaste. Por tanto, volver a cambiar es una forma válida de retomar la conversación anterior de un hilo.
- **Cambio global** — los registros por hilo se dejan deliberadamente intactos. Si un hilo todavía retiene el ID de sesión del otro backend, el siguiente mensaje inicia una sesión nueva y publica un aviso de una línea que explica por qué, en lugar de reanudar.

Los registros escritos antes de que ccdb rastreara la propiedad de backend no tienen backend almacenado. Un cambio global los reanuda exactamente como siempre lo hizo; un cambio con alcance de hilo los limpia en lugar de arriesgar una reanudación rota.

Pistas visuales para que nunca olvides con cuál estás hablando:

- Las **sesiones de Claude** abren con un embed color blurple titulado "🤖 Claude Code session started".
- Las **sesiones de Codex** abren con un embed color teal de OpenAI titulado "🌀 OpenAI Codex session started".
- El embed de finalización antepone un chip `🧠 Claude · sonnet` / `🧠 Codex · gpt-5.6-sol` junto a las métricas habituales de duración / costo / tokens / contexto. (Cuando el modelo de un backend se deja en el valor por defecto del CLI, el chip muestra solo el nombre del backend.)

Ejemplo concreto:

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

Tras bambalinas:

- `BackendFactory` — captura la configuración estática al arrancar (ruta del comando por backend, modo de permisos, directorio de trabajo, herramientas permitidas, timeout, append-system-prompt, effort, api_port, api_secret) y construye un `ClaudeRunner` o `CodexRunner` nuevo bajo demanda. `api_port` se conecta automáticamente mediante `setup_bridge` después de que arranca el servidor REST API, así que los runners construidos por la fábrica siempre tienen `CCDB_API_URL` inyectado en el entorno de su subproceso.
- `BackendSettings` — un envoltorio fino sobre `SettingsRepository` que resuelve el backend activo con precedencia **thread > global > env** y persiste las escrituras de los comandos slash.
- Protocolo `SessionBackend` — la interfaz abstracta que ambos runners satisfacen. La fontanería interna (cogs, embeds, views, scheduler, disparador de webhook) recibe un `SessionBackend`, nunca una clase de runner concreta.

**¿Dónde se autentica cada backend?** Claude Code usa tu suscripción Claude Pro/Max existente vía el `claude login` del CLI `claude`. Codex usa tu suscripción ChatGPT Plus/Pro/Business existente vía el `codex login` del CLI `codex`. ccdb nunca ve API keys en crudo — simplemente delega al CLI que esté seleccionado.

---

## Características

### Chat Interactivo

#### 🔗 Conceptos Básicos de Sesión
- **Modo solo chat** — Cuando `CHAT_ONLY_CHANNEL_IDS` incluye un canal, solo se muestran las respuestas de texto de Claude; se ocultan los embeds de herramientas, los bloques de pensamiento, los embeds de inicio/fin de sesión y las listas de tareas. Las solicitudes de permisos y `AskUserQuestion` siempre se muestran. Ideal para canales públicos donde observan usuarios no técnicos.
- **Hilo = Sesión** — Mapeo 1:1 entre un hilo de Discord y una sesión de Claude Code
- **Seguimiento de objetivos** — `/goal <condición>` establece una condición de finalización; Claude sigue trabajando hasta cumplirla. Omite la condición para consultar el estado; pasa `clear` para cancelar
- **Persistencia de sesión** — Reanuda conversaciones entre mensajes mediante `--resume`
- **Recuperación automática de reanudación de Codex** — Si una sesión de Codex reanudada pierde repetidamente su WebSocket antes de producir salida, ccdb inicia una sesión de reemplazo con una transcripción acotada y solo de texto de la conversación previa; se excluyen las cargas útiles de imágenes y herramientas
- **Sesiones concurrentes** — Múltiples sesiones en paralelo con límite configurable
- **Detener sin borrar** — `/stop` detiene una sesión preservándola para reanudarla
- **Interrupción de sesión** — Enviar un mensaje nuevo a un hilo activo envía SIGINT a la sesión en ejecución y comienza de cero con la nueva instrucción; no se necesita `/stop` manual
- **Auto-renombrado de hilos** — Con `THREAD_AUTO_RENAME=true`, cada hilo nuevo se renombra automáticamente con un título generado por Claude a partir del primer mensaje (tarea en segundo plano, nunca retrasa el inicio de la sesión)

#### 📡 Retroalimentación en Tiempo Real
- **Estado en tiempo real** — Reacciones emoji: 🧠 pensando, 🛠️ leyendo archivos, 💻 editando, 🌐 búsqueda web
- **Texto en streaming** — El texto intermedio del asistente aparece mientras Claude trabaja
- **Embeds de resultados de herramientas** — Resultados de llamadas a herramientas en vivo con el tiempo transcurrido mostrado de inmediato (0s) y aumentando cada 5s; las salidas de una sola línea se muestran en línea, las de múltiples líneas se colapsan tras un botón de expandir
- **Pensamiento extendido** — El razonamiento se muestra como embeds etiquetados con spoiler (haz clic para revelar)
- **Panel de hilos** — Embed fijado en vivo que muestra qué hilos están activos vs. esperando; se @menciona al propietario cuando se necesita entrada

#### 🤝 Human-in-the-Loop
- **Preguntas interactivas** — `AskUserQuestion` se renderiza como botones o menú de selección de Discord; la sesión se reanuda con tu respuesta; los botones sobreviven a los reinicios del bot; se @menciona al solicitante cuando se necesita entrada
- **Modo Plan** — Cuando Claude llama a `ExitPlanMode`, un embed de Discord muestra el plan completo con botones Aprobar/Cancelar; Claude continúa solo tras la aprobación; se @menciona al solicitante en el prompt; auto-cancelación a los 5 minutos de timeout
- **Solicitudes de permisos de herramientas** — Cuando Claude necesita permiso para ejecutar una herramienta, Discord muestra botones Permitir/Denegar con el nombre de la herramienta y la entrada; se @menciona al solicitante; auto-rechazo después de 2 minutos
- **MCP Elicitation** — Los servidores MCP pueden solicitar entrada del usuario a través de Discord (modo formulario: hasta 5 campos de Modal a partir de un JSON schema; modo url: botón de URL + confirmación Done); se @menciona al solicitante; timeout de 5 minutos
- **Progreso TodoWrite en vivo** — Cuando Claude llama a `TodoWrite`, se publica un único embed de Discord y se edita en su lugar en cada actualización; muestra ✅ completadas, 🔄 activas (con etiqueta `activeForm`), ⬜ pendientes

#### 📊 Observabilidad
- **Uso de tokens** — Tasa de aciertos en caché y conteo de tokens mostrados en el embed de sesión completada
- **Uso de contexto** — Porcentaje de la ventana de contexto (tokens de entrada + caché, excluyendo salida) y capacidad restante hasta el auto-compact, mostrados en el embed de sesión completada; advertencia ⚠️ por encima del 83.5%
- **Detección de compact** — Notifica en el hilo cuando ocurre una compactación de contexto (tipo de disparador + conteo de tokens antes del compact)
- **Notificación de bloqueo prolongado** — Mensaje en el hilo tras un periodo sin actividad (pensamiento extendido o compresión de contexto); se reinicia automáticamente cuando Claude retoma. Los umbrales son conscientes del modelo: 30 s para modelos estándar, 120 s para Opus (que tiene pausas de pensamiento más largas)
- **Notificaciones de timeout** — Embed con el tiempo transcurrido y guía de reanudación en caso de timeout
- **Visualización de StatusLine** — Cuando Claude configura un `statusLine` (vía `/statusline-setup`), el estado actual se muestra en Discord después de cada sesión como un indicador conciso y siempre visible
- **Indicador de proveedor de API** — Después de cada sesión, una línea `🔗 API: <provider>` muestra qué endpoint está usando realmente el CLI (`Anthropic API (direct)`, `AWS Bedrock`, `Google Vertex AI`, `Azure AI Foundry`, o una URL base personalizada), derivado del entorno real del subproceso para que las superposiciones de env del CLI queden reflejadas. Siempre se muestra — incluso sin un `statusLine` configurado.
- **Bandeja de entrada de hilos** — Con `THREAD_INBOX_ENABLED=true`, el panel muestra una sección persistente 📬 de bandeja de entrada: después de que termina cada sesión, Claude clasifica el mensaje final (`waiting` / `done` / `ambiguous`) mediante una llamada ligera a `claude -p`; los hilos que esperan tu respuesta sobreviven a los reinicios del bot y se resaltan hasta que respondes

#### 🔌 Entrada y Habilidades
- **Soporte de archivos adjuntos** — Los archivos de texto se añaden automáticamente al prompt (hasta 5 archivos, 200 KB c/u / 500 KB en total; los archivos demasiado grandes se truncan con un aviso en lugar de omitirse); las imágenes se envían como URLs del CDN de Discord vía `--input-format stream-json` (hasta 4 × 5 MB); los mensajes pegados largos que Discord convierte automáticamente en archivos adjuntos (sin `content_type`) se gestionan mediante detección basada en la extensión
- **Entrega de archivos bajo demanda** — Pídele a Claude que te "envíe" o "adjunte" un archivo y escribirá la ruta en `.ccdb-attachments`; el bot la lee y entrega el archivo como adjunto de Discord cuando la sesión termina. Las instrucciones locales también pueden exigir que los entregables escritos sustanciales se guarden como Markdown y se adjunten.
- **Ejecución de habilidades** — Comando `/skill` con autocompletado, argumentos opcionales, reanudación en el hilo; las habilidades de los plugins instalados también se descubren automáticamente
- **Recarga en caliente** — Las nuevas habilidades añadidas a `~/.claude/skills/` se detectan automáticamente (actualización cada 60s, sin reiniciar)

### Concurrencia y Coordinación
- **Instrucciones de worktree auto-inyectadas** — A cada sesión se le indica usar `git worktree` antes de tocar cualquier archivo
- **Limpieza automática de worktree** — Los worktrees de sesión (`wt-{thread_id}`) se eliminan automáticamente al final de la sesión y al arrancar el bot; los worktrees sucios nunca se eliminan automáticamente (invariante de seguridad)
- **Registro de sesiones activas** — Registro en memoria; cada sesión ve lo que hacen las demás
- **AI Lounge** — Canal compartido de "sala de descanso"; el contexto se inyecta como instrucciones de sistema/desarrollador específicas del backend (efímeras, nunca se acumulan en el historial) para que las sesiones largas nunca choquen con "Prompt is too long"; las sesiones publican intenciones, leen el estado de las demás y verifican antes de operaciones disruptivas; los humanos lo ven como un feed de actividad en vivo
- **Observabilidad entre sesiones** — `GET /api/sessions` lista cada sesión (activa y almacenada) con su estado, directorio de trabajo y última nota del lounge; `GET /api/threads/{thread_id}/messages` lee la conversación de otro hilo. De solo lectura, para que una sesión pueda mirar antes de editar — incluso a sesiones que nunca publicaron en el lounge
- **Reclamaciones de recursos** — `POST /api/claims` reserva un repositorio, issue o archivo antes de empezar el trabajo; una segunda sesión que pida el mismo recurso recibe 409 con el hilo, la nota y el estado en vivo del titular. Consultivas y acotadas por TTL (por defecto 2 h, máximo 24 h), para que una sesión muerta no pueda inmovilizar un recurso para siempre
- **Retransmisión de sesión a sesión** — `POST /api/threads/{thread_id}/message` permite que una sesión hable con otra cuando ya han colisionado; `queue` espera el turno del receptor, `interrupt` le envía SIGINT. Cada retransmisión se publica en el hilo (nunca un canal encubierto), envuelta en un marcador para que no se confunda con el humano, y acotada por límites de saltos/enfriamiento/tasa para que dos sesiones no puedan entrar en bucle
- **Detección automática de colisiones** — `CollisionWatchCog` compara los archivos que las sesiones activas realmente escribieron (registrados de `Write`/`Edit`/`MultiEdit`/`NotebookEdit`) una vez por minuto; dos sesiones que escriben el mismo archivo dentro de 15 minutos se anuncian en el AI Lounge y en ambos hilos. Atrapa los solapamientos que nadie anunció; una alerta por par cada 30 minutos, y nunca interrumpe un turno en ejecución
- **Canal de coordinación** — La variable de entorno `COORDINATION_CHANNEL_ID` se usa como fallback predeterminado para el canal del AI Lounge (sin eventos de ciclo de vida separados del lado del bot)

### Tareas Programadas
- **SchedulerCog** — Ejecutor de tareas periódicas respaldado por SQLite con un ciclo maestro de 30 segundos
- **Auto-registro** — Claude registra tareas vía `POST /api/tasks` durante una sesión de chat
- **Sin cambios de código** — Añade, elimina o modifica tareas en tiempo de ejecución
- **Activar/desactivar** — Pausa tareas sin eliminarlas (`PATCH /api/tasks/{id}`)

### Automatización CI/CD
- **Disparadores de webhook** — Dispara tareas de Claude Code desde GitHub Actions o cualquier sistema CI/CD
- **Auto-actualización** — Actualiza el bot automáticamente cuando se publican paquetes upstream
- **Reinicio DrainAware** — Espera a que las sesiones activas terminen antes de reiniciar
- **Marcado automático de reanudación** — Las sesiones activas se marcan automáticamente para reanudar en cualquier apagado (reinicio por actualización vía `AutoUpgradeCog`, o cualquier otro apagado vía `ClaudeChatCog.cog_unload()`); al reiniciar, Claude informa su estado previo y vuelve a confirmar con el usuario antes de reanudar cualquier trabajo de implementación
- **Aprobación de reinicio** — Puerta opcional para confirmar actualizaciones; aprueba con una reacción ✅ en el hilo de actualización o mediante un botón publicado en el canal padre; el botón se vuelve a publicar al final a medida que llegan nuevos mensajes para que permanezca visible
- **Disparador manual de actualización** — El comando slash `/upgrade` permite a los usuarios autorizados disparar el pipeline de actualización directamente desde Discord (opt-in mediante `slash_command_enabled=True`)

### Gestión de Sesiones
- **Ayuda integrada** — `/help` muestra todos los comandos slash disponibles y el uso básico (efímero, solo visible para quien lo invoca)
- **Sincronización de sesiones** — Importa sesiones CLI como hilos de Discord (`/sync-sessions`); `/sync-settings` para ver o cambiar las preferencias de sincronización (estilo de hilo, ventana de tiempo, resultados mínimos)
- **Lista de sesiones** — `/sessions` con filtrado por origen (Discord / CLI / todos) y ventana de tiempo
- **Reanudar sesión** — `/resume` muestra un menú de selección de sesiones recientes (hasta 25) y reanuda la seleccionada en un nuevo hilo; parámetro opcional `query` para búsqueda por palabra clave (coincide con el resumen y el directorio de trabajo); filtro opcional `filter=orphaned` para mostrar solo sesiones de hilos eliminados; funciona desde cualquier canal o hilo — siempre crea un nuevo hilo en el canal principal configurado
- **Info de reanudación** — `/resume-info` muestra el comando CLI para continuar la sesión actual en una terminal (solo dentro del hilo)
- **Borrar sesión** — `/clear` reinicia la sesión de Claude Code del hilo actual, empezando de cero sin crear un nuevo hilo
- **Reanudación al inicio** — Las sesiones interrumpidas se reinician automáticamente tras cualquier reinicio del bot; `AutoUpgradeCog` (reinicios por actualización) y `ClaudeChatCog.cog_unload()` (todos los demás apagados) las marcan automáticamente, o usa `POST /api/mark-resume` manualmente
- **Generación programática** — `POST /api/spawn` crea un nuevo hilo de Discord + sesión de Claude desde cualquier script o subproceso de Claude; devuelve un 201 no bloqueante inmediatamente después de crear el hilo
- **Inyección de ID de hilo** — La variable de entorno `DISCORD_THREAD_ID` se pasa a cada subproceso de Claude, permitiendo que las sesiones generen sesiones hijas vía `$CCDB_API_URL/api/spawn`
- **Visualización de StatusLine** — Si tu `settings.json` de Claude Code tiene un `statusLine` configurado, su salida se muestra en Discord después de cada respuesta de sesión
- **Gestión de worktree** — `/worktree-list` muestra todos los worktrees de sesión activos con estado limpio/sucio; `/worktree-cleanup` elimina los worktrees limpios huérfanos (soporta la vista previa `dry_run`)
- **Cambio de modelo en tiempo de ejecución** — `/model-show` muestra el modelo global actual y el modelo de sesión por hilo; `/model-set` cambia el modelo para todas las nuevas sesiones sin reiniciar
- **Permisos de herramientas en tiempo de ejecución** — `/tools-show` muestra las herramientas permitidas actuales; `/tools-set` abre un menú de selección para activar/desactivar herramientas; `/tools-reset` revierte al valor por defecto de `.env` — todo sin reiniciar
- **Uso de contexto** — `/context` muestra el porcentaje de la ventana de contexto con una barra de progreso visual; advertencia ⚠️ al acercarse al umbral de auto-compact del 83.5%; efímero (solo visible para quien lo invoca)
- **Uso de límite de tasa** — `/usage` muestra la utilización del límite de tasa de la API de Claude con barra de porcentaje y cuenta regresiva de tiempo hasta el reinicio para las ventanas de 5 horas y 7 días; señal ⚠️ cuando la utilización ≥ 80%
- **Rebobinado de conversación** — `/rewind` muestra un menú de selección de turnos de usuario anteriores y trunca el JSONL de la sesión en el punto elegido, eliminando ese mensaje y todo lo posterior para que la sesión se reanude desde el estado exacto anterior a ese turno; conserva todos los archivos de trabajo que Claude creó; útil cuando una sesión se ha desviado
- **Bifurcación de conversación** — `/fork` ramifica el hilo actual en un nuevo hilo que continúa desde el mismo estado de sesión vía `--fork-session`, creando una copia de sesión verdaderamente independiente; te permite explorar una dirección diferente sin afectar la original

### Seguridad
- **Sin inyección de shell** — Solo `asyncio.create_subprocess_exec`, nunca `shell=True`
- **Validación de ID de sesión** — Regex estricta antes de pasar a `--resume`
- **Prevención de inyección de flags** — Separador `--` antes de todos los prompts
- **Aislamiento de secretos** — El token del bot se elimina del entorno del subproceso
- **Autorización de usuarios** — `allowed_user_ids` restringe quién puede invocar a Claude
- **Prevención de inyección en logs** — Los valores de API proporcionados por el usuario se sanean (se eliminan los saltos de línea) antes de escribir en los logs

---

## Inicio Rápido — Claude o Codex en Discord en 5 Minutos

**Prerrequisitos:**

- Python 3.12+
- Al menos uno de:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — instalado y autenticado (`claude login`). Recomendado para suscriptores de Anthropic Pro/Max.
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` y luego `codex login`. Usa tu suscripción ChatGPT Plus/Pro/Business existente.
- Puedes instalar ambos. Cambia entre ellos en tiempo de ejecución con `/backend` (ver [Cambio de Backend](#cambio-de-backend--claude--codex-bajo-demanda)).

**Soporte de plataformas:** Principalmente desarrollado y probado en **Linux**. macOS y Windows son compatibles y pasan CI, pero reciben menos pruebas en el mundo real — se agradecen los reportes de errores.

### Paso 1 — Crear un Bot de Discord (una vez, ~2 minutos)

1. Ve a [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. Navega a **Bot** → habilita **Message Content Intent** en Privileged Gateway Intents
3. Copia el **Token** del bot
4. Ve a **OAuth2 → URL Generator**: Scopes `bot` + `applications.commands`, Permisos: Send Messages, Create Public Threads, Send Messages in Threads, Add Reactions, Manage Messages, Read Message History
5. Abre la URL generada → invita al bot a tu servidor

### Paso 2 — Ejecutar el Asistente de Configuración

Sin necesidad de clonar ni editar `.env` — el asistente lo hace por ti:

```bash
# With uvx (no install needed):
uvx --from "git+https://github.com/ebibibi/ebi-agent-chat-relay.git" ccdb setup

# Or after cloning:
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv run ccdb setup
```

El asistente hará lo siguiente:
1. Validar tu token del bot contra la API de Discord
2. **Listar automáticamente los canales disponibles** — solo elige un número (sin copiar IDs)
3. Preguntar por tu directorio de trabajo y preferencia de modelo
4. Escribir `.env` y ofrecer iniciar el bot de inmediato

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

### Iniciar / Detener

```bash
ccdb start    # start the bot (reads .env in current dir)
ccdb start --env /path/to/.env   # custom .env location
```

Envía un mensaje en el canal configurado — Claude responderá en un nuevo hilo.

### Ejecutar como Servicio systemd (Producción)

Para despliegues de producción, ejecuta el bot bajo systemd para que inicie al arrancar y se reinicie automáticamente en caso de fallo.

El repositorio incluye una plantilla lista para adaptar (`discord-bot.service`) y un script de pre-arranque (`scripts/pre-start.sh`). Cópialos y personalízalos:

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

**Qué hace `scripts/pre-start.sh`** (se ejecuta como `ExecStartPre` antes del proceso del bot):

1. **`git pull --ff-only`** — trae el código más reciente de `origin main`
2. **`uv sync`** — mantiene las dependencias en sincronía con `uv.lock`
3. **Validación de imports** — verifica que `claude_discord.main` se importe sin errores
4. **Auto-rollback** — si el import falla, revierte al commit anterior y reintenta; publica una notificación de webhook de Discord en caso de fallo o éxito
5. **Limpieza de worktree** — elimina los git worktrees obsoletos dejados por sesiones que se cayeron

El script detecta la raíz del repositorio dinámicamente (vía `readlink -f` sobre `$0`), así que funciona para cualquier usuario sin importar dónde clonó el repositorio — no se necesita editar rutas en el propio script. También descubre automáticamente el binario `uv` desde `PATH`; anúlalo con la variable de entorno `CCDB_UV_BIN` si es necesario.

El script requiere la variable `DISCORD_WEBHOOK_URL` en `.env` para las notificaciones de fallo (opcional — el script funciona sin ella).

#### PATH del Toolchain — configúralo en `.env`

systemd inicia una unidad con un `PATH` por defecto mínimo (típicamente `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`) y nunca carga `~/.bashrc` ni `~/.profile`. El bot hereda ese `PATH`, y también lo hace cada sesión de Claude/Codex que genera — las sesiones se ejecutan con el entorno del bot menos los secretos eliminados.

El resultado es confuso: una compilación que funciona en tu terminal falla dentro de una sesión de Discord, o se ejecuta silenciosamente contra un binario más antiguo a nivel de sistema, porque las herramientas instaladas bajo `~/.local/bin` o `~/.npm-global/bin` son invisibles para el servicio.

Como el servicio carga `.env` vía `EnvironmentFile=`, configurar `PATH` allí arregla el bot y cada sesión de una sola vez:

```bash
# .env — match your interactive shell's PATH
PATH=/home/you/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
```

Reinicia el servicio (`sudo systemctl restart mybot.service`), luego confírmalo desde una sesión de Discord pidiéndole a Claude que ejecute `which node && node --version`.

### Cogs Personalizados (Extiende Sin Hacer Fork)

Añade tus propias características dejando archivos Python en un directorio — sin fork, sin subclase, sin paquete necesario:

```bash
ccdb start --cogs-dir ./my-cogs/
# Or: CUSTOM_COGS_DIR=./my-cogs ccdb start
```

Cada archivo `.py` del directorio debe exponer un `async def setup(bot, runner, components)`:

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

Los archivos con prefijo `_` se omiten. Si un Cog falla al cargar, los demás se cargan normalmente.

Ver [`examples/ebibot/`](examples/ebibot/) para un ejemplo completo del mundo real con recordatorios, watchdog de Todoist, auto-actualización y sincronización de documentación.

**Ejemplos integrados en `examples/ebibot/cogs/`:**

| Cog | Propósito |
|-----|-----------|
| `ReminderCog` | Programación de recordatorios basada en Discord |
| `WatchdogCog` | Watchdog de Todoist / servicios externos |
| `AutoUpgradeCog` | Actualización de paquete disparada por webhook |
| `DocsSyncCog` | Sincronización automática de documentación en cada push |
| `AlertResponderCog` | Monitoreo genérico de alertas — reenvía alertas de sistemas de monitoreo a Discord y dispara una sesión de investigación de Claude Code |

---

### Bot Mínimo (Instalar como Paquete)

Si ya tienes un bot de discord.py, añade ccdb como paquete en su lugar:

```bash
uv add git+https://github.com/ebibibi/ebi-agent-chat-relay.git
```

Crea un `bot.py`:

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

`setup_bridge()` conecta todos los Cogs automáticamente. Actualiza a la última versión:

```bash
uv lock --upgrade-package claude-code-discord-bridge && uv sync
```

#### Configuración Multi-Canal

Para desplegar el bot en múltiples canales de Discord, pasa `claude_channel_ids` además de (o en lugar de) `claude_channel_id`:

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

Cada canal es totalmente independiente — los mensajes en cualquiera de los canales configurados generan un nuevo hilo de sesión de Claude, y los comandos `/skill` funcionan en todos ellos. `claude_channel_id` se conserva por retrocompatibilidad y se usa como objetivo de fallback para la creación de hilos cuando el comando `/skill` se invoca fuera de un canal configurado.

#### Canales Solo-Mención

Para hacer que el bot responda **solo cuando se le @menciona** en canales específicos (útil para canales compartidos donde no quieres que el bot reaccione a cada mensaje):

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 222},
    mention_only_channel_ids={222},  # bot ignores messages in #222 unless @mentioned
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

O mediante una variable de entorno (IDs de canal separados por comas):

```
MENTION_ONLY_CHANNEL_IDS=222,333
```

Los hilos **heredan la política de su canal padre**. Un hilo que un humano crea en un canal solo-mención no inicia una sesión de Claude — de lo contrario cualquiera podría eludir la configuración simplemente abriendo un hilo. Claude interviene en tal hilo solo cuando:

- se **@menciona** explícitamente al bot en el mensaje, o
- ccdb **ya es dueño del hilo** — un hilo de sesión que creó el bot, o uno creado vía `/api/spawn`. Una vez que existe una sesión, cada respuesta se maneja normalmente sin necesidad de una mención.

Los hilos bajo canales que *no* están listados en `mention_only_channel_ids` no se ven afectados y siempre se manejan.

#### Canales de Respuesta en Línea

Para hacer que el bot responda **directamente en el canal** (sin crear un hilo) en canales específicos (útil para canales personales de comandos donde los hilos añaden desorden innecesario):

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 333},
    inline_reply_channel_ids={333},  # bot replies inline in #333, no thread created
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

O mediante una variable de entorno (IDs de canal separados por comas):

```
INLINE_REPLY_CHANNEL_IDS=333,444
```

En el modo de respuesta en línea, la respuesta de Claude se envía directamente como un mensaje en el canal en lugar de generar un nuevo hilo. Las sesiones aún se rastrean internamente, así que los mensajes de seguimiento en el canal continúan la misma sesión de Claude.

#### Canales Solo-Chat

Para ocultar la UI técnica (embeds de herramientas, bloques de pensamiento, avisos de inicio/fin de sesión, listas de tareas) y mostrar **solo las respuestas de texto de Claude** en canales específicos — útil para canales de cara al público donde observan usuarios no técnicos:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 444},
    chat_only_channel_ids={444},  # only text shown in #444; tool details hidden
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

O mediante una variable de entorno (IDs de canal separados por comas):

```
CHAT_ONLY_CHANNEL_IDS=444,555
```

En el modo solo-chat, las solicitudes de permisos y los prompts de `AskUserQuestion` **siempre se muestran** sin importar la configuración — requieren entrada humana y deben ser visibles.

---

## Configuración

| Variable | Descripción | Por defecto |
|----------|-------------|-------------|
| `DISCORD_BOT_TOKEN` | Tu token del bot de Discord | (requerido) |
| `DISCORD_CHANNEL_ID` | ID del canal para el chat de Claude | (requerido) |
| `CCDB_BACKEND` | Backend CLI a usar: `claude` (Claude Code CLI) o `codex` (OpenAI Codex CLI) | `claude` |
| `CCDB_COMMAND` | Ruta o nombre del binario del CLI (reemplaza a `CLAUDE_COMMAND`). Lo usa el runner inicial elegido según `CCDB_BACKEND`; queda sustituido por las dos variables por backend de abajo cuando `/backend` cambia en tiempo de ejecución. | _(auto: `claude` o `codex`)_ |
| `CCDB_CLAUDE_COMMAND` | Ruta explícita al binario del CLI de Claude. La usa `BackendFactory` siempre que `/backend claude` esté activo, sin importar el `CCDB_BACKEND` inicial. Recurre a `CLAUDE_COMMAND`, luego a `claude` (PATH). | (opcional) |
| `CCDB_CODEX_COMMAND` | Ruta explícita al binario del CLI de OpenAI Codex. Requerida al ejecutar el bot bajo systemd (el PATH por defecto del servicio no incluye `~/.npm-global/bin`). Recurre a `codex` (PATH). | (opcional) |
| `PATH` | Ruta de búsqueda de binarios para el bot **y cada sesión CLI que genera** — las sesiones heredan el entorno del bot. Configúralo en `.env` al ejecutar bajo systemd, que inicia las unidades con un PATH mínimo y nunca lee `~/.bashrc` / `~/.profile`. Ver [PATH del Toolchain](#path-del-toolchain--configúralo-en-env). | (heredado del proceso padre) |
| `CCDB_MODEL` | Modelo a usar (reemplaza a `CLAUDE_MODEL`) | `sonnet` |
| `CCDB_PERMISSION_MODE` | Modo de permisos para el CLI (reemplaza a `CLAUDE_PERMISSION_MODE`) | `acceptEdits` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | Omitir todas las verificaciones de permisos — reemplaza a `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | `false` |
| `CCDB_WORKING_DIR` | Directorio de trabajo para el CLI (reemplaza a `CLAUDE_WORKING_DIR`) | directorio actual |
| `CCDB_ALLOWED_TOOLS` | Lista separada por comas de herramientas permitidas (reemplaza a `CLAUDE_ALLOWED_TOOLS`) | (opcional) |
| `CCDB_CHANNEL_IDS` | IDs de canal adicionales, separados por comas (reemplaza a `CLAUDE_CHANNEL_IDS`) | (opcional) |
| `CLAUDE_COMMAND` | Ruta o nombre del binario del CLI de Claude (nombre heredado — prefiere `CCDB_COMMAND`). Úsalo para fijar una versión específica (p. ej. `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`) — útil para evitar regresiones en versiones más nuevas del CLI. | `claude` |
| `CLAUDE_MODEL` | Modelo a usar (heredado — prefiere `CCDB_MODEL`) | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | Modo de permisos para el CLI (heredado — prefiere `CCDB_PERMISSION_MODE`) | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | Omitir todas las verificaciones de permisos (heredado — prefiere `CCDB_DANGEROUSLY_SKIP_PERMISSIONS`) | `false` |
| `CLAUDE_WORKING_DIR` | Directorio de trabajo para Claude (heredado — prefiere `CCDB_WORKING_DIR`) | directorio actual |
| `MAX_CONCURRENT_SESSIONS` | Máximo de sesiones CLI de Claude en paralelo en todas las rutas de código (chat, habilidades, scheduler, webhooks) | `3` |
| `SESSION_TIMEOUT_SECONDS` | Timeout de inactividad de sesión | `300` |
| `DISCORD_OWNER_ID` | ID de usuario a @mencionar cuando Claude necesita entrada | (opcional) |
| `COORDINATION_CHANNEL_ID` | ID de canal usado como fallback predeterminado para el canal AI Lounge | (opcional) |
| `MENTION_ONLY_CHANNEL_IDS` | IDs de canal, separados por comas, donde el bot solo responde cuando se le @menciona (los hilos bajo ellos heredan la política) | (opcional) |
| `INLINE_REPLY_CHANNEL_IDS` | IDs de canal, separados por comas, donde el bot responde en línea (sin crear hilo) | (opcional) |
| `CHAT_ONLY_CHANNEL_IDS` | IDs de canal, separados por comas, en modo solo-chat — solo se muestran las respuestas de texto de Claude; se ocultan todos los embeds técnicos (herramientas, pensamiento, info de sesión, todos) | (opcional) |
| `WORKTREE_BASE_DIR` | Directorio base a escanear en busca de worktrees de sesión (habilita la limpieza automática) | (opcional) |
| `CLI_SESSIONS_PATH` | Ruta a `~/.claude/projects` para el descubrimiento de sesiones CLI (habilita `/sync-sessions`) | (opcional) |
| `CUSTOM_COGS_DIR` | Directorio con archivos Cog personalizados a cargar al inicio (ver [Cogs Personalizados](#cogs-personalizados-extiende-sin-hacer-fork)) | (opcional) |
| `CLAUDE_ALLOWED_TOOLS` | Lista separada por comas de herramientas permitidas para el CLI de Claude (heredado — prefiere `CCDB_ALLOWED_TOOLS`) | (opcional) |
| `CLAUDE_CHANNEL_IDS` | IDs de canal adicionales (separados por comas) para configuración multi-canal (heredado — prefiere `CCDB_CHANNEL_IDS`) | (opcional) |
| `THREAD_INBOX_ENABLED` | Habilitar la bandeja de entrada persistente de hilos (clasifica sesiones como `waiting`/`done`/`ambiguous` vía `claude -p`; se muestra en el panel de hilos) | `false` |
| `THREAD_AUTO_RENAME` | Auto-renombrar los títulos de los hilos nuevos usando Claude AI — genera un título breve y descriptivo a partir del primer mensaje del usuario mediante una llamada en segundo plano a `claude -p` (nunca retrasa el inicio de la sesión) | `false` |
| `CCDB_CLI_ENV_FILE` | Ruta a un archivo `KEY=VALUE` cuyas variables se fusionan en el entorno del subproceso CLI en cada invocación. Los cambios surten efecto inmediatamente sin reiniciar el bot. Útil para enrutamiento temporal de API (p. ej., Azure Foundry) | (opcional) |
| `CCDB_LOG_FILE` | Ruta a un archivo de log. Cuando se define, se añade un manejador de archivo rotativo (10 MB × 5 copias) junto al manejador de stdout por defecto. Útil para monitoreo y alertas. | (opcional) |
| `API_HOST` | Dirección de enlace del REST API | `127.0.0.1` |
| `API_PORT` | Puerto del REST API (habilita el REST API cuando se define) | (opcional) |
| `CCDB_INGEST_TOKEN` | Token Bearer para `POST /api/ingest` (independiente de `api_secret`); si no se define, el endpoint responde `503` | (opcional) |
| `CCDB_INGEST_REQUIRE_COMPLETE` | Ponlo en `1` para rechazar una ingesta con `409` cuando su `attachments_manifest` demuestre que se perdieron adjuntos, en vez de iniciar una sesión con evidencia parcial | `0` |

### Modos de Permisos — Qué Funciona en Modo `-p`

Claude Code CLI se ejecuta en **modo `-p` (no interactivo)** cuando se usa a través de ccdb. En este modo, el CLI **no puede pedir permiso** — las herramientas que requieren aprobación se rechazan de inmediato. Esta es una [restricción de diseño del CLI](https://code.claude.com/docs/en/headless), no una limitación de ccdb.

| Modo | Comportamiento en modo `-p` | Recomendación |
|------|-----------------------------|---------------|
| `default` | ❌ **Todas las herramientas rechazadas** — inservible | No usar |
| `acceptEdits` | ⚠️ Edit/Write auto-aprobados, Bash rechazado (Claude recurre a Write para operaciones de archivo) | Opción mínima viable |
| `bypassPermissions` | ✅ Todas las herramientas aprobadas | Funciona, pero prefiere el flag de abajo |
| **`auto`** | ✅ **Seguridad clasificada por IA** — operaciones seguras auto-aprobadas, operaciones peligrosas bloqueadas | **Recomendado** — el mejor equilibrio entre seguridad y usabilidad |
| `plan` | ✅ Clasificado por IA (sesgo de solo lectura) — similar a auto pero más conservador | Para flujos de trabajo con mucha lectura |
| **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`** | ✅ **Todas las herramientas aprobadas, sin verificaciones de seguridad** | Modo "yolo" heredado — úsalo cuando el modo auto sea demasiado restrictivo |

**Nuestra recomendación:** Configura `CLAUDE_PERMISSION_MODE=auto`. El modo auto usa un clasificador de IA para aprobar automáticamente operaciones seguras (ediciones de archivos, pruebas locales, git push a la rama de trabajo) mientras bloquea las peligrosas (force push, despliegues a producción, filtración de credenciales). Esto le da a Claude autonomía completa para el trabajo de desarrollo normal sin el riesgo de "todo vale" del modo yolo.

**Fallback al modo yolo:** Si el modo auto bloquea operaciones que necesitas, configura `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true` en su lugar. Como ccdb controla quién puede interactuar con Claude vía `allowed_user_ids`, las verificaciones de permisos a nivel del CLI añaden fricción sin un beneficio de seguridad significativo. El "dangerously" del nombre refleja la advertencia de propósito general del CLI; en el contexto de ccdb, donde el acceso ya está restringido, es una elección práctica.

> **Nota:** Cuando `CLAUDE_PERMISSION_MODE` se establece en `auto` o `plan`, `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` se ignora automáticamente — estos modos tienen sus propios clasificadores de seguridad que el flag yolo anularía.

**Para un control granular**, usa `CLAUDE_ALLOWED_TOOLS` para permitir herramientas específicas sin eludir completamente los permisos:

```env
# Example: allow file operations and code execution, but not web access
CLAUDE_ALLOWED_TOOLS=Bash,Read,Write,Edit,Glob,Grep

# Example: read-only mode — Claude can explore but not modify
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
```

Nombres de herramientas comunes: `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`. Configura `CLAUDE_PERMISSION_MODE=default` al usar esto (otros modos pueden anularlo).

**Cambios en tiempo de ejecución vía Discord:** Usa `/tools-set` para cambiar las herramientas permitidas en tiempo de ejecución sin reiniciar el bot. La configuración se persiste y surte efecto para todas las nuevas sesiones de inmediato. Usa `/tools-show` para ver la configuración actual, o `/tools-reset` para revertir al valor por defecto de `.env`.

> **Botones de permisos en Discord:** Cuando `CLAUDE_PERMISSION_MODE=default`, Claude emite eventos `permission_request` y ccdb muestra botones Permitir/Denegar en el hilo. stdin se mantiene siempre abierto (modo de entrada stream-json) para que el bot pueda enviar respuestas de vuelta a Claude. Si usas el modo `auto` o `plan`, Claude gestiona los permisos automáticamente sin requerir interacción del usuario. Cuando `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true` (modo yolo), ccdb **auto-aprueba** cualquier evento `permission_request` de inmediato — no se muestran botones Permitir/Denegar. Esto es una solución alternativa para una regresión del CLI (v2.1.78+, upstream [#35895](https://github.com/anthropics/claude-code/issues/35895)) donde `--dangerously-skip-permissions` no logra eludir la verificación de rutas sensibles a nivel de archivo.

---

## Configuración del Bot de Discord

1. Crea una nueva aplicación en el [Portal de Desarrolladores de Discord](https://discord.com/developers/applications)
2. Crea un bot y copia el token
3. Habilita **Message Content Intent** en Privileged Gateway Intents
4. Invita al bot con estos permisos:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Manage Messages (para la limpieza de reacciones)
   - Read Message History

---

## Automatización con GitHub + Claude Code

### Ejemplo: Sincronización Automática de Documentación

En cada push a `main`, Claude Code:
1. Trae los últimos cambios y analiza el diff
2. Actualiza la documentación en inglés
3. Traduce al japonés (o a cualquier idioma objetivo)
4. Crea un PR con un resumen bilingüe
5. Habilita auto-merge — hace merge automáticamente cuando pasa CI

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

**Configuración del bot:**

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

**Seguridad:** Los prompts se definen del lado del servidor. Los webhooks solo seleccionan qué disparador activar — sin inyección arbitraria de prompts.

### Ejemplo: Auto-Aprobar PRs del Propietario

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

## Tareas Programadas

Registra tareas periódicas de Claude Code en tiempo de ejecución — sin cambios de código, sin redespliegues.

Desde dentro de una sesión de Discord, Claude puede registrar una tarea:

```bash
# Claude calls this inside a session:
curl -X POST "$CCDB_API_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Check for outdated deps and open an issue if found", "interval_seconds": 604800}'
```

O regístralas desde tus propios scripts:

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Weekly security scan", "interval_seconds": 604800}'
```

El ciclo maestro de 30 segundos recoge las tareas vencidas y genera sesiones de Claude Code automáticamente.

---

## Auto-Actualización

Actualiza el bot automáticamente cuando se publica una nueva versión:

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

#### Disparador Manual vía `/upgrade`

Cuando `slash_command_enabled=True`, cualquier usuario autorizado puede ejecutar `/upgrade` directamente en Discord para disparar el mismo pipeline de actualización — sin necesidad de webhook. El comando funciona tanto desde canales de texto como desde hilos (ejecutarlo dentro de un hilo crea el hilo de actualización en el canal padre). Respeta las puertas `upgrade_approval` y `restart_approval`, crea un hilo de progreso y maneja con elegancia las ejecuciones concurrentes (responde de forma efímera si ya hay una actualización en curso).

Antes de reiniciar, `AutoUpgradeCog`:

1. **Captura una instantánea de las sesiones activas** — Recopila todos los hilos con sesiones de Claude en ejecución (mediante duck typing: cualquier Cog con un dict `_active_runners` se descubre automáticamente).
2. **Drena** — Espera a que las sesiones activas terminen de forma natural.
3. **Marca para reanudar** — Guarda los IDs de los hilos activos en la tabla de reanudaciones pendientes. En el siguiente inicio, esas sesiones se reanudan con un prompt que prioriza la seguridad: Claude informa en qué estaba trabajando y pide al usuario que vuelva a confirmar antes de reanudar cualquier trabajo de implementación (cambios de código, commits, PRs). Esto evita acciones no deseadas después de que la compresión de contexto pudo haber borrado el estado de aprobación de la tarea.
4. **Reinicia** — Ejecuta el comando de reinicio configurado.

Cualquier Cog con una propiedad `active_count` se descubre y drena automáticamente:

```python
class MyCog(commands.Cog):
    @property
    def active_count(self) -> int:
        return len(self._running_tasks)
```

El marcado de sesiones es totalmente opt-in — solo se activa cuando `setup_bridge()` ha inicializado la base de datos de sesiones (el valor por defecto). Cuando está habilitado, las sesiones se reanudan con la continuidad de `--resume` para que Claude Code pueda retomar la conversación exactamente donde la dejó.

> **Cobertura:** `AutoUpgradeCog` cubre los reinicios disparados por actualización. Para *todos los demás* apagados (`systemctl stop`, `bot.close()`, SIGTERM), `ClaudeChatCog.cog_unload()` proporciona una segunda red de seguridad automática.

---

## REST API

REST API opcional para notificaciones y gestión de tareas. Requiere aiohttp:

```bash
uv add "claude-code-discord-bridge[api]"
```

### Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/health` | Verificación de salud |
| POST | `/api/notify` | Enviar notificación inmediata |
| POST | `/api/schedule` | Programar una notificación |
| GET | `/api/scheduled` | Listar notificaciones pendientes |
| DELETE | `/api/scheduled/{id}` | Cancelar una notificación |
| POST | `/api/tasks` | Registrar una tarea programada de Claude Code |
| GET | `/api/tasks` | Listar tareas registradas |
| DELETE | `/api/tasks/{id}` | Eliminar una tarea |
| PATCH | `/api/tasks/{id}` | Actualizar una tarea (activar/desactivar, cambiar horario) |
| POST | `/api/spawn` | Crear un nuevo hilo de Discord e iniciar una sesión de Claude Code (no bloqueante); pasa `auto_start: false` para diferir Claude hasta la primera respuesta del usuario |
| POST | `/api/ingest` | Spawn externo autenticado (extensión de navegador / webhook) con adjuntos en base64; devuelve un `result_id` cuando la recuperación de resultados está configurada |
| GET | `/api/ingest/{result_id}` | Sondear la respuesta final de la sesión generada (`status`/`result`/`error`/`thread_id`) |
| POST | `/api/mark-resume` | Marcar un hilo para reanudación automática en el próximo inicio del bot |
| GET | `/api/lounge` | Leer los mensajes recientes del AI Lounge |
| POST | `/api/lounge` | Publicar un mensaje en el AI Lounge (con `label` opcional) |
| GET | `/api/sessions` | Listar cada sesión — activa y almacenada — con estado, directorio de trabajo y última nota del lounge (`state=running`, `exclude_thread`, `limit`) |
| GET | `/api/threads/{thread_id}/messages` | Leer la conversación de otro hilo, del más antiguo al más reciente (`limit`) |
| POST | `/api/claims` | Reclamar un recurso antes de trabajar en él — 201 cuando se adquiere, 409 con el titular cuando está tomado |
| GET | `/api/claims` | Listar las reclamaciones activas (filtro `resource` opcional) |
| DELETE | `/api/claims` | Liberar una reclamación (`resource`, `thread_id`, `force=true` opcional) |
| POST | `/api/threads/{thread_id}/message` | Retransmitir un mensaje de una sesión a otra (`text`, `from_thread`, `mode`, `hop`) |

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

## Arquitectura

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
    ingest_manifest.py     # Concilia attachments_manifest con los archivos entregados
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

### Filosofía de Diseño

- **Spawn de CLI, no API** — Invoca `claude -p --output-format stream-json`, obteniendo todas las características de Claude Code (CLAUDE.md, habilidades, herramientas, memoria) sin reimplementarlas. Se ejecuta sobre tu suscripción Claude Pro/Max — sin API key, sin facturación por token
- **Concurrencia primero** — Múltiples sesiones simultáneas son el caso esperado, no un caso límite; cada sesión recibe instrucciones de worktree, el registro y el AI Lounge se encargan del resto
- **Discord como pegamento** — Discord proporciona UI, hilos, reacciones, webhooks y notificaciones persistentes; no se necesita un frontend personalizado
- **Framework, no aplicación** — Instala como paquete, añade Cogs a tu bot existente, configura mediante código
- **Extensibilidad sin código** — Añade tareas programadas y disparadores de webhook sin tocar el código fuente
- **Seguridad por simplicidad** — ~8000 líneas de Python auditable; solo subprocess exec, sin expansión de shell

---

## Pruebas

```bash
uv run pytest tests/ -v --cov=claude_discord
```

Más de 1690 pruebas que cubren el analizador, chunker, repositorio, runner, streaming, disparadores de webhook, auto-actualización (incluidos el comando slash `/upgrade`, la invocación desde hilo y el botón de aprobación), REST API, AskUserQuestion UI, panel de hilos, tareas programadas, sincronización de sesiones, AI Lounge, observabilidad entre sesiones, reclamaciones de recursos, retransmisión de sesión a sesión, reanudación al inicio, cambio de modelo, detección de compact, embeds de progreso TodoWrite, cargador de Cogs personalizados, análisis de eventos de permisos/elicitation/modo-plan, clasificación de la bandeja de entrada de hilos, comportamiento de bloqueo por hilo, protocolo SessionBackend, CodexRunner, fábrica de backends y propiedad de sesión entre backends.

---

## Cómo se Construyó este Proyecto

**Este codebase es desarrollado por [Claude Code](https://docs.anthropic.com/en/docs/claude-code)**, el agente de codificación con IA de Anthropic, bajo la dirección de [@ebibibi](https://github.com/ebibibi). El autor humano define los requisitos, revisa los pull requests y aprueba todos los cambios — Claude Code hace la implementación.

Esto significa:

- **La implementación es generada por IA** — arquitectura, código, pruebas, documentación
- **La revisión humana se aplica a nivel de PR** — cada cambio pasa por pull requests de GitHub y CI antes de hacer merge
- **Los reportes de errores y PRs son bienvenidos** — Claude Code se usará para atenderlos
- **Este es un ejemplo del mundo real de software de código abierto dirigido por humanos e implementado por IA**

El proyecto comenzó el 2026-02-18 y continúa evolucionando a través de conversaciones iterativas con Claude Code.

---

## Ejemplo del Mundo Real

**[`examples/ebibot/`](examples/ebibot/)** — Un bot personal de Discord construido sobre este framework, incluido directamente en este repositorio. Demuestra el cargador de Cogs personalizado con:

- **ReminderCog** — Comando slash `/remind HH:MM "message"` + ciclo de envío de 30 segundos
- **WatchdogCog** — Monitor de tareas vencidas de Todoist (verificación cada 30 minutos, deduplicación diaria, alertas basadas en severidad)
- **AutoUpgradeCog** — Auto-actualización mediante webhook de GitHub + systemctl restart
- **DocsSyncCog** — Auto-traducción de documentación en cada push mediante webhook
- **AlertResponderCog** — Cog genérico de monitoreo de alertas; observa una fuente configurable y publica notificaciones anotadas por severidad en Discord

Ejecútalo con: `ccdb start --cogs-dir examples/ebibot/cogs/`

> Los Cogs personalizados de EbiBot se mantenían anteriormente en un [repositorio separado](https://github.com/ebibibi/discord-bot). Ahora están ubicados aquí para que Claude Code siempre tenga el contexto completo tanto del framework como de las personalizaciones — evitando la duplicación accidental de características.

---

## Inspirado Por

- [OpenClaw](https://github.com/openclaw/openclaw) — Reacciones emoji de estado, debouncing de mensajes, chunking consciente de fences
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — Enfoque de spawn de CLI + stream-json
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — Patrones de control de permisos
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — Modelo de conversación por hilo

---

## Licencia

MIT
