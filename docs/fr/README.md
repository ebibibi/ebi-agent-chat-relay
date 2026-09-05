> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../../README.md) takes precedence.
> **Remarque :** Ceci est une version traduite automatiquement de la documentation originale en anglais.
> En cas de divergence, la [version anglaise](../../README.md) fait foi.

# Claude & Codex Discord Bridge

*Nom du package : `claude-code-discord-bridge` (kebab-case)*

[![CI](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Utilisez Claude Code _ou_ OpenAI Codex depuis votre téléphone. Plusieurs fils. Tout en même temps. Développement réel inclus.**

Ouvrez Claude Code ou OpenAI Codex depuis l'application Discord de votre smartphone, lancez plusieurs fils et exécutez des sessions de développement en parallèle — sans toucher un clavier. Chaque fil Discord devient une session IA complètement isolée. Travaillez sur une fonctionnalité dans un fil, révisez un PR dans un autre et exécutez une tâche en arrière-plan dans un troisième — simultanément, en mélangeant même les backends par fil. Le bridge gère toute la coordination pour que les sessions ne se chevauchent jamais.

**Utilisez vos abonnements existants. Sans configuration de clé API.** ccdb fonctionne sur les CLIs officielles — Claude Code (inclus dans votre [abonnement Claude Pro/Max](https://claude.ai/pricing)) et OpenAI Codex (inclus dans [ChatGPT Plus/Pro/Business](https://chatgpt.com)). Changez de backend avec `/backend` ou définissez une surcharge par fil — votre équipe accède aux deux IA via Discord à un coût prévisible.

**[English](../../README.md)** | **[日本語](../ja/README.md)** | **[简体中文](../zh-CN/README.md)** | **[한국어](../ko/README.md)** | **[Español](../es/README.md)** | **[Português](../pt-BR/README.md)**

> **Avertissement :** Ce projet n'est pas affilié, approuvé ni officiellement connecté à Anthropic ou OpenAI. « Claude » et « Claude Code » sont des marques commerciales d'Anthropic, PBC ; « OpenAI », « Codex » et « ChatGPT » sont des marques commerciales d'OpenAI. Il s'agit d'un outil open source indépendant qui s'interface avec Claude Code CLI et OpenAI Codex CLI.

> **Entièrement construit par Claude Code.** L'ensemble de ce codebase — architecture, implémentation, tests, documentation — a été écrit par Claude Code lui-même. L'auteur humain a fourni les exigences et la direction en langage naturel. Voir [Comment ce projet a été construit](#comment-ce-projet-a-été-construit).

---

## La Grande Idée : Sessions Parallèles Sans Crainte

Lorsque vous envoyez des tâches à Claude Code ou OpenAI Codex dans des fils Discord séparés, le bridge fait automatiquement quatre choses — quel que soit le backend choisi :

1. **Injection d'avis de concurrence** — Le system prompt de chaque session inclut des instructions obligatoires : créer un git worktree, y travailler uniquement, ne jamais toucher directement le répertoire de travail principal.

2. **Registre de sessions actives** — Chaque session en cours d'exécution connaît les autres. Si deux sessions s'apprêtent à toucher le même dépôt, elles peuvent se coordonner plutôt que d'entrer en conflit.

3. **AI Lounge** — Un « salon de repos » de session à session injecté dans chaque prompt. Avant de commencer, chaque session lit les messages récents du lounge pour voir ce que font les autres sessions, et réclame le dépôt, l'issue ou le fichier qu'elle s'apprête à toucher (voir [Réclamations de Ressources](#réclamations-de-ressources)) afin qu'une deuxième session soit écartée avant de dupliquer le travail. Avant les opérations perturbatrices (force push, redémarrage du bot, suppression de DB), les sessions vérifient d'abord le lounge pour ne pas se marcher dessus.

4. **Surface agnostique au backend** — La même interface Discord, les mêmes commandes slash, le même planificateur, la même API et le même Lounge fonctionnent de la même manière, que le fil exécute Claude ou Codex. Mélangez les backends entre les fils si vous le souhaitez — par ex. Claude pour les refactorisations, Codex pour la revue de code — via `/backend` par fil.

```
Fil A (fonctionnalité)  ──→  Claude Code  (worktree-A)  ─┐
Fil B (révision PR)     ──→  OpenAI Codex (worktree-B)   ├─→  #ai-lounge
Fil C (docs)            ──→  Claude Code  (worktree-C)  ─┘    "A : refactor auth en cours"
                                                             "B : révision PR #42 terminée (codex)"
                                                             "C : mise à jour du README"
```

Pas de conditions de course. Pas de travail perdu. Pas de surprises lors des merges. Aucun verrouillage propriétaire de backend.

---

## Ce Que Vous Pouvez Faire

### Chat Interactif (Mobile / Bureau)

Utilisez Claude Code _ou_ OpenAI Codex depuis n'importe où où Discord fonctionne — téléphone, tablette ou bureau. Chaque message crée ou continue un fil mappé 1:1 à une session IA persistante. Changez de backend à tout moment avec `/backend claude` ou `/backend codex` — par fil, ou globalement comme nouveau défaut.

### Développement Parallèle

Ouvrez plusieurs fils simultanément. Chacun est une session IA indépendante — Claude Code ou Codex — avec son propre contexte, répertoire de travail et git worktree. Modèles utiles :

- **Fonctionnalité + révision en parallèle** : Démarrez une fonctionnalité avec Claude dans un fil pendant que Codex révise le PR dans un autre.
- **Plusieurs contributeurs** : Chaque membre de l'équipe dispose de son propre fil (et de son backend préféré) ; les sessions restent informées les unes des autres via l'AI Lounge.
- **Expérimentez en sécurité** : Essayez une approche dans le fil A tout en maintenant le fil B sur du code stable.
- **Testez A/B le même prompt sur les deux IA** : Lancez deux fils avec la même tâche, l'un sur `/backend claude` et l'autre sur `/backend codex`, puis comparez les diffs côte à côte.

### Tâches Planifiées (SchedulerCog)

Enregistrez des tâches Claude Code périodiques depuis une conversation Discord ou via l'API REST — sans changements de code, sans redéploiements. Les tâches sont stockées dans SQLite et s'exécutent selon un planning configurable. Claude peut auto-enregistrer des tâches pendant une session via `POST /api/tasks`.

```
/skill name:goodmorning         → s'exécute immédiatement
Claude appelle POST /api/tasks  → enregistre une tâche périodique
SchedulerCog (boucle maître 30s)  → déclenche automatiquement les tâches dues
```

### Automatisation CI/CD

Déclenchez des tâches Claude Code depuis GitHub Actions via des webhooks Discord. Claude s'exécute de manière autonome — lit le code, met à jour la documentation, crée des PRs, active l'auto-merge.

```
GitHub Actions → Discord Webhook → Bridge → Claude Code CLI
                                                  ↓
GitHub PR ←── git push ←── Claude Code ──────────┘
```

**Exemple réel :** À chaque push sur `main`, Claude analyse le diff, met à jour la documentation en anglais + japonais, crée un PR bilingue et active l'auto-merge. Zéro interaction humaine.

### Synchronisation de Sessions

Vous utilisez déjà Claude Code CLI directement ? Synchronisez vos sessions de terminal existantes dans des fils Discord avec `/sync-sessions`. Remplit les messages de conversation récents pour que vous puissiez continuer une session CLI depuis votre téléphone sans perdre le contexte.

### AI Lounge

Un canal « salon de repos » partagé où toutes les sessions concurrentes s'annoncent, lisent les mises à jour des autres et se coordonnent avant les opérations perturbatrices.

Chaque session reçoit automatiquement le contexte du lounge sous forme d'instructions système/développeur éphémères (`--append-system-prompt` pour Claude, `developer_instructions` pour Codex), plutôt que dans l'historique de conversation. Cela évite que le contexte ne s'accumule au fil des tours, ce qui provoquerait sinon des erreurs « Prompt is too long » dans les sessions de longue durée. Le contexte injecté comprend les messages récents des autres sessions ainsi que la règle de vérification avant toute opération destructive.

```bash
# Sessions post their intentions before starting:
curl -X POST "$CCDB_API_URL/api/lounge" \
  -H "Content-Type: application/json" \
  -d '{"message": "Starting auth refactor on feature/oauth — worktree-A", "label": "feature dev"}'

# Read recent lounge messages (also injected into each session automatically):
curl "$CCDB_API_URL/api/lounge"
```

Le canal lounge fait aussi office de fil d'activité visible par les humains — ouvrez-le dans Discord pour voir d'un coup d'œil ce que fait actuellement chaque session Claude active.

### Observabilité Inter-Sessions

Une note du lounge indique à une session *qu'*un autre fil existe. Ces deux endpoints en lecture seule lui permettent d'aller regarder — ainsi deux sessions ayant démarré sur la même tâche peuvent découvrir le chevauchement au lieu de foncer toutes les deux.

```bash
# Who else is alive, where are they working, what did they last announce?
curl "$CCDB_API_URL/api/sessions?exclude_thread=$DISCORD_THREAD_ID"

# Read that thread's actual conversation
curl "$CCDB_API_URL/api/threads/1529338965000192110/messages?limit=30"
```

`/api/sessions` fusionne trois sources : la table `sessions` (created_at, répertoire de travail, backend), le registre en mémoire (ce que chaque session active fait *en ce moment même*) et la dernière note de lounge de chaque fil. Une session apparaît avec `"state": "running"` pendant qu'un tour est en cours — y compris les sessions qui n'ont jamais rien publié dans le lounge, ce qui est précisément le moment où cela compte. Une conversation enregistrée sans tour en cours apparaît avec `"state": "history"` : elle peut être reprise, mais aucun agent n'attend du travail ou une saisie utilisateur. Les sessions n'ont pas de token Discord propre, donc le bot effectue la lecture et les endpoints restent sur le plan de contrôle localhost.

### Réclamations de Ressources

L'observabilité indique à une session qu'une collision *s'est produite*. Une réclamation l'empêche — sans lecture, sans négociation, sans aller-retour LLM. Une session réclame ce sur quoi elle s'apprête à travailler ; la session suivante demandant la même chose est refusée avant d'effectuer le moindre travail.

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

Les réclamations sont **consultatives** — rien ne les impose au niveau git ou système de fichiers — et chaque réclamation porte un TTL (2 h par défaut, 24 h max) afin qu'une session qui meurt ne puisse pas bloquer une ressource indéfiniment. Le corps de la réponse 409 indique si le détenteur est toujours en cours d'exécution, ce qui permet à l'appelant de décider s'il attend, travaille sur autre chose, ou prend le relais avec `force=true`. Les noms de ressources sont libres et normalisés (casse et espaces), donc `repo:ccdb` et `Repo: CCDB` désignent la même réclamation.

Le prompt du lounge indique à chaque session de réclamer avant de commencer et de libérer une fois terminé.

### Relais de Session à Session

L'observabilité permet à une session de voir un pair ; une réclamation les tient à l'écart l'une de l'autre. Lorsque deux sessions sont déjà entrées en collision, elles doivent réellement se parler — et l'une d'elles doit s'arrêter.

```bash
curl -X POST "$CCDB_API_URL/api/threads/<their_thread_id>/message" \
  -H "Content-Type: application/json" \
  -d '{"text": "I started this at 13:02 on branch fix/parser and already pushed 3 commits.",
       "from_thread": "'$DISCORD_THREAD_ID'", "mode": "queue", "hop": 0}'
```

`on_message` ignore tout ce qu'un bot a écrit — c'est ce garde-fou qui empêche le bot de se parler à lui-même — donc les relais passent par cet endpoint à la place, de la même manière que `/api/spawn`.

- **`mode: "queue"`** (par défaut) attend la fin du tour en cours du destinataire.
- **`mode: "interrupt"`** envoie un SIGINT au tour en cours, pour qu'un « arrête maintenant » arrive en quelques secondes. Cela peut coûter au destinataire du travail non commité, il est donc réservé aux vrais conflits.
- Le texte relayé est **publié dans le fil** avant d'atteindre Claude, afin que les humains qui observent voient tout l'échange IA-à-IA. Un relais n'est jamais un canal détourné.
- Chaque message est **enveloppé dans un marqueur** nommant le fil expéditeur et précisant qu'il ne provient pas de l'humain — une instruction non marquée serait obéie comme si le propriétaire l'avait écrite.

Les boucles sont le vrai risque (deux sessions se répondant l'une l'autre consomment des tokens et s'interrompent indéfiniment), donc un garde-fou borne chaque chaîne : **2 sauts maximum**, un délai de récupération de 60 s par paire de fils, 5 relais par expéditeur toutes les 10 minutes, et aucun envoi à soi-même. Les refus reviennent en 429 avec le motif.

Le prompt du lounge donne aussi aux sessions une règle de départage pour que la conversation converge au lieu de finir en politesses mutuelles : celui qui a des commits ou un PR l'emporte sur celui qui enquête encore ; sinon, la session la plus ancienne continue ; en cas d'égalité, l'ID de fil le plus bas gagne. Celui qui se retire pousse d'abord sa branche et transmet ce qu'il a appris.

### Détection Automatique de Collisions

Le lounge et les réclamations dépendent tous deux du fait qu'une session *dise* quelque chose. Ceci attrape les chevauchements que personne n'a annoncés, à partir de ce que les sessions ont réellement fait : si deux sessions actives écrivent dans le même fichier en moins de 15 minutes, elles travaillent sur la même chose, que l'une ou l'autre l'ait mentionné ou non.

`EventProcessor` enregistre le chemin de chaque appel d'outil de type écriture (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`) ; `CollisionWatchCog` compare ces ensembles entre les sessions actives une fois par minute.

> Pourquoi les chemins de fichiers et non les répertoires de travail : sur un hôte mono-utilisateur, chaque session tend à démarrer dans le même répertoire personnel, donc l'égalité de `working_dir` signale chaque paire et ne veut rien dire. Un *fichier édité* partagé n'est presque jamais une coïncidence. Les lectures sont délibérément ignorées — deux sessions lisant le même fichier est normal et noierait le signal.

Lorsqu'un chevauchement est trouvé, le surveillant publie :

- une ligne dans l'**AI Lounge**, qui est injectée dans le prochain tour de chaque session sans coût de token et sans rien interrompre, et
- un message dans **chaque fil en collision**, nommant le pair, les fichiers partagés et les endpoints qui résolvent la situation.

Il ne relaie jamais dans une session en cours d'exécution — préempter un tour sur un simple soupçon coûterait plus cher que la collision. L'escalade est la décision des sessions, via l'endpoint de relais ci-dessus. Chaque paire est annoncée au plus une fois toutes les 30 minutes, car un avertissement répété chaque minute est un avertissement que tout le monde apprend à ignorer.

Activé automatiquement ; il reste dormant jusqu'à ce que deux sessions se chevauchent réellement.

### Création de Session Programmatique

Créez de nouvelles sessions Claude Code depuis des scripts, GitHub Actions ou d'autres sessions Claude — sans interaction par messages Discord.

```bash
# From another Claude session or a CI script:
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Run security scan on the repo", "thread_name": "Security Scan"}'
# Returns immediately with the thread ID; Claude runs in the background
```

**Démarrage différé (`auto_start=false`)** — Créez un fil et publiez un message d'amorce sans démarrer Claude immédiatement. Claude ne démarre que lorsqu'un utilisateur répond, et reçoit automatiquement le message d'amorce comme contexte.

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

C'est utile pour les flux de type notification (par ex. briefings quotidiens, alertes CI) où vous voulez afficher des informations d'emblée et laisser l'utilisateur décider d'engager Claude ou non.

Les sous-processus Claude reçoivent `DISCORD_THREAD_ID` comme variable d'environnement, donc une session en cours peut créer des sessions enfants pour paralléliser le travail.

### Ingestion Externe Authentifiée avec Récupération de Résultats (`/api/ingest`)

`POST /api/ingest` est le **spawn authentifié et compatible avec les pièces jointes** destiné aux clients externes non fiables (extensions de navigateur, raccourcis mobiles, webhooks). Contrairement à `/api/spawn` (fiable, localhost), il nécessite un `ingest_token` dédié (définissez `CCDB_INGEST_TOKEN` ; indépendant de `api_secret`) et peut transporter des pièces jointes base64 qui sont écrites sur disque pour que la session créée puisse les lire. Il crée un vrai fil Discord, donc toute l'interaction reste observable.

La session est **interactive** (un vrai fil Discord dans lequel vous pouvez continuer à répondre) — mais vous pouvez tout de même récupérer sa réponse finale de façon programmatique. Lorsque la récupération de résultats est configurée (câblée automatiquement via `setup_bridge()`), la réponse inclut un `result_id`, et `GET /api/ingest/{result_id}` interroge la réponse finale de la session. Cette même réponse finale est aussi jointe au fil Discord sous le nom `ccdb-answer.md`, afin que les intégrations puissent traiter la pièce jointe comme la charge utile canonique de la réponse. C'est le motif d'aller-retour : publier un fil + pièces jointes → attendre → lire le fichier de réponse ou interroger le résultat → le réécrire dans votre propre système (par ex. un fil Teams), pendant que Discord conserve l'historique.

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

L'endpoint est opt-in : sans `ingest_token` configuré, `POST` répond `503`. Lorsque la récupération de résultats est indisponible, `POST` omet simplement `result_id` et `GET /api/ingest/{id}` renvoie `503` — le comportement de spawn est par ailleurs inchangé. Le corps de la requête et les pièces jointes ne sont **pas** persistés dans le magasin de résultats (seulement le statut, le texte final et l'ID du fil) ; les résultats sont plafonnés à 200 lignes.

#### Livraison de pièces jointes vérifiée (`attachments_manifest`)

Une pièce jointe perdue côté client était jusqu'ici invisible : ccdb enregistrait ce qu'on lui donnait et annonçait un décompte que personne ne pouvait vérifier, si bien qu'une session répondait comme si elle disposait d'un fichier qu'elle n'avait jamais reçu. Envoyez un **manifeste** et ccdb **vérifie** la livraison au lieu de la présumer — une entrée par pièce jointe repérée en amont, avec `status` indiquant si ses octets figurent dans cette requête :

```bash
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "Rédige une réponse",
       "attachments": [{"filename": "bundle.zip", "data": "<base64>"}],
       "attachments_manifest": [
         {"name": "shot.png",  "status": "embedded", "sha256": "…", "message": "返信 11"},
         {"name": "debug.log", "status": "linked", "url": "https://…", "message": "返信 12",
          "reason": "SharePoint download returned 403"}
       ]}'
# → {"status": "spawned", …, "attachments": {"verified": true, "complete": false,
#      "missing": [], "not_delivered": [{"name": "debug.log", "message": "返信 12", …}]}}
```

| `status` | Signification |
|---|---|
| `embedded` (par défaut) | Les octets sont dans cette requête — ccdb s'attend à trouver un fichier correspondant |
| `linked` | Seule une URL a pu être obtenue (pas de permission sur l'hôte, mur d'authentification, …) |
| `skipped` | Délibérément non envoyé (limite de taille, filtré) |
| `failed` | La capture ou le téléchargement a échoué |

ccdb rapproche chaque entrée `embedded` d'un fichier livré en utilisant **d'abord le sha256**, puis le nom exact, puis le préfixe d'index de type `4_image.png` qu'un empaqueteur ajoute en cas de collision de noms, et enfin la taille — chaque fichier n'étant consommé qu'une seule fois, deux pièces jointes nommées `image.png` ne peuvent pas revendiquer toutes les deux l'unique fichier arrivé. Tout ce qui reste inexpliqué est signalé de quatre façons : un bloc ⚠️ **en tête du prompt de la session** qui nomme les fichiers manquants et enjoint à la session de ne pas inventer leur contenu (avec une mention supplémentaire lorsque la perte concerne le message le plus récent), un registre `ATTACHMENTS-REPORT.md` écrit à côté des fichiers, le verdict `attachments` dans la réponse ci-dessus, et un `WARNING` dans le log. Fournir `message` regroupe également la liste des chemins du prompt par message d'origine et marque le groupe le plus récent comme celui à lire en premier.

Définissez `CCDB_INGEST_REQUIRE_COMPLETE=1` (ou `ingest_require_complete=True`) pour **refuser** une ingestion lacunaire avec un `409` plutôt que de démarrer une session sur des preuves partielles — pertinent lorsque les pièces jointes *sont* la requête. Omettez complètement le manifeste et rien ne change : l'ingestion est rapportée comme `verified: false`, jamais comme vérifiée-complète.

### Reprise au Démarrage

Si le bot redémarre en pleine session, les sessions Claude interrompues sont automatiquement reprises quand le bot revient en ligne. Les sessions sont marquées pour reprise de trois façons :

- **Automatique (redémarrage pour mise à jour)** — `AutoUpgradeCog` capture toutes les sessions actives juste avant un redémarrage de mise à jour et les marque automatiquement.
- **Automatique (tout arrêt)** — `ClaudeChatCog.cog_unload()` marque toutes les sessions en cours d'exécution chaque fois que le bot s'arrête par n'importe quel mécanisme (`systemctl stop`, `bot.close()`, SIGTERM, etc.).
- **Manuel** — Toute session peut appeler `POST /api/mark-resume` directement.

### Changement de Backend — Claude / Codex à la Demande

ccdb 3.0 introduit trois commandes slash qui changent quelle IA gère la prochaine session, sans redémarrage du bot :

- `/backend [name] [scope]` — affiche ou change de backend. `name` vaut `claude` ou `codex`. `scope` vaut `thread` (ce fil uniquement) ou `global` (défaut à l'échelle du serveur). Quand vous omettez `scope`, la commande se résout automatiquement : dans un fil, elle s'applique à ce fil, sinon elle définit le défaut global.
- `/model [name] [scope]` — affiche ou change le modèle utilisé par le backend **actuel**. Chaque backend mémorise sa propre préférence de modèle, donc basculer d'un backend à l'autre préserve vos modèles favoris. Laissez le modèle d'un backend non défini pour vous en remettre au défaut de la CLI concernée (par ex. Codex utilise le `model` de `~/.codex/config.toml`, donc ccdb suit le défaut de la console plutôt que d'épingler une version).
- `/effort [level] [scope]` — affiche ou change l'**effort de raisonnement** utilisé par le backend actuel. Les niveaux valides sont spécifiques au backend : Claude accepte `low/medium/high/max` ; Codex accepte `low/medium/high/xhigh/max/ultra` (mappé sur le `model_reasoning_effort` de la CLI). Laissez-le non défini pour vous en remettre au défaut de la CLI.

Les trois commandes persistent dans SQLite via `SettingsRepository`, donc le choix survit aux redémarrages du bot. Les appeler sans argument affiche le défaut global actuel plus toute surcharge de fil.

**Qu'advient-il d'un fil qui a déjà une session ?** Les IDs de session ne sont pas interopérables entre les deux CLIs — transmettre un ID de rollout Codex à `claude --resume` (ou un UUID Claude à `codex exec resume`) échoue au niveau de la CLI. ccdb enregistre quel backend a créé chaque ID de session, donc un changement ne laisse jamais un fil bloqué :

- **Changement à l'échelle du fil** — l'ID de session stocké est abandonné pour que le prochain message reparte de zéro dans le nouveau backend, *sauf* si l'enregistrement est connu pour appartenir au backend vers lequel vous avez basculé. Rebasculer est donc un moyen valide de reprendre la conversation antérieure d'un fil.
- **Changement global** — les enregistrements par fil sont délibérément laissés intacts. Si un fil détient encore l'ID de session de l'autre backend, le prochain message démarre une nouvelle session et publie un avis d'une ligne expliquant pourquoi, au lieu de reprendre.

Les enregistrements écrits avant que ccdb ne suive la propriété de backend n'ont pas de backend stocké. Un changement global les reprend exactement comme il l'a toujours fait ; un changement à l'échelle du fil les efface plutôt que de risquer une reprise cassée.

Repères visuels pour ne jamais oublier à qui vous parlez :

- Les **sessions Claude** s'ouvrent avec un embed blurple intitulé « 🤖 Claude Code session started ».
- Les **sessions Codex** s'ouvrent avec un embed teal OpenAI intitulé « 🌀 OpenAI Codex session started ».
- L'embed de fin ajoute en préfixe une puce `🧠 Claude · sonnet` / `🧠 Codex · gpt-5.6-sol` aux côtés des métriques habituelles de durée / coût / tokens / contexte. (Quand le modèle d'un backend reste au défaut de la CLI, la puce n'affiche que le nom du backend.)

Exemple concret :

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

En coulisses :

- `BackendFactory` — capture la configuration statique au démarrage (chemin de commande par backend, mode de permission, répertoire de travail, outils autorisés, timeout, append-system-prompt, effort, api_port, api_secret) et construit à la demande un `ClaudeRunner` ou `CodexRunner` neuf. `api_port` est câblé automatiquement par `setup_bridge` après le démarrage du serveur API REST, donc les runners construits par la fabrique ont toujours `CCDB_API_URL` injecté dans l'environnement de leur sous-processus.
- `BackendSettings` — fine surcouche de `SettingsRepository` qui résout le backend actif avec la précédence **thread > global > env** et persiste les écritures des commandes slash.
- Protocole `SessionBackend` — l'interface abstraite que les deux runners satisfont. La plomberie interne (cogs, embeds, views, planificateur, déclencheur webhook) prend un `SessionBackend`, jamais une classe de runner concrète.

**Où chaque backend s'authentifie-t-il ?** Claude Code utilise votre abonnement Claude Pro/Max existant via `claude login` de la CLI `claude`. Codex utilise votre abonnement ChatGPT Plus/Pro/Business existant via `codex login` de la CLI `codex`. ccdb ne voit jamais de clés API brutes — il ne fait que déléguer à la CLI sélectionnée.

---

## Fonctionnalités

### Chat Interactif

#### 🔗 Bases de Session
- **Mode chat uniquement** — Lorsque `CHAT_ONLY_CHANNEL_IDS` inclut un canal, seules les réponses textuelles de Claude sont affichées ; les embeds d'outils, blocs de réflexion, embeds de début/fin de session et listes de tâches sont masqués. Les demandes de permission et `AskUserQuestion` sont toujours affichées. Idéal pour les canaux publics où des utilisateurs non techniques observent.
- **Fil = Session** — Mappage 1:1 entre un fil Discord et une session Claude Code
- **Suivi d'objectif** — `/goal <condition>` définit une condition de complétion ; Claude continue de travailler jusqu'à ce qu'elle soit remplie. Omettez la condition pour vérifier le statut ; passez `clear` pour annuler
- **Persistance de session** — Reprenez les conversations entre les messages via `--resume`
- **Récupération automatique de reprise Codex** — Si une session Codex reprise perd de manière répétée son WebSocket avant de produire une sortie, ccdb démarre une session de remplacement avec une transcription textuelle bornée de la conversation précédente ; les charges utiles d'images et d'outils sont exclues
- **Sessions concurrentes** — Plusieurs sessions parallèles avec limite configurable
- **Arrêter sans effacer** — `/stop` arrête une session tout en la préservant pour une reprise
- **Interruption de session** — Envoyer un nouveau message à un fil actif envoie un SIGINT à la session en cours et repart avec la nouvelle instruction ; pas besoin de `/stop` manuel
- **Renommage automatique des fils** — Avec `THREAD_AUTO_RENAME=true`, chaque nouveau fil est automatiquement renommé avec un titre généré par Claude à partir du premier message (tâche en arrière-plan, ne retarde jamais le démarrage de la session)

#### 📡 Retour en Temps Réel
- **Statut en temps réel** — Réactions emoji : 🧠 réflexion, 🛠️ lecture de fichiers, 💻 édition, 🌐 recherche web
- **Texte en streaming** — Le texte intermédiaire de l'assistant apparaît pendant que Claude travaille
- **Embeds de résultat d'outil** — Résultats d'appels d'outils en direct avec temps écoulé affiché immédiatement (0 s) et incrémenté toutes les 5 s ; les sorties sur une seule ligne sont affichées en ligne, les sorties multi-lignes repliées derrière un bouton d'expansion
- **Réflexion étendue** — Raisonnement affiché sous forme d'embeds à balise spoiler (cliquer pour révéler)
- **Tableau de bord des fils** — Embed épinglé en direct montrant quels fils sont actifs vs. en attente ; le propriétaire est @-mentionné quand une saisie est nécessaire

#### 🤝 Human-in-the-Loop
- **Questions interactives** — `AskUserQuestion` s'affiche sous forme de boutons ou de menu de sélection Discord ; la session reprend avec votre réponse ; les boutons survivent aux redémarrages du bot ; le demandeur est @mentionné quand une saisie est nécessaire
- **Mode Plan** — Lorsque Claude appelle `ExitPlanMode`, un embed Discord affiche le plan complet avec des boutons Approuver/Annuler ; Claude ne poursuit qu'après approbation ; le demandeur est @mentionné à l'invite ; annulation automatique après un timeout de 5 minutes
- **Demandes de permission d'outil** — Lorsque Claude a besoin d'une permission pour exécuter un outil, Discord affiche des boutons Autoriser/Refuser avec le nom de l'outil et l'entrée ; le demandeur est @mentionné ; refus automatique après 2 minutes
- **MCP Elicitation** — Les serveurs MCP peuvent demander une saisie utilisateur via Discord (mode formulaire : jusqu'à 5 champs Modal depuis un schéma JSON ; mode url : bouton URL + confirmation Done) ; le demandeur est @mentionné ; timeout de 5 minutes
- **Progression TodoWrite en direct** — Lorsque Claude appelle `TodoWrite`, un unique embed Discord est publié et édité sur place à chaque mise à jour ; affiche ✅ terminé, 🔄 actif (avec libellé `activeForm`), ⬜ éléments en attente

#### 📊 Observabilité
- **Utilisation de tokens** — Taux de succès du cache et comptages de tokens affichés dans l'embed de fin de session
- **Utilisation du contexte** — Pourcentage de la fenêtre de contexte (tokens d'entrée + cache, hors sortie) et capacité restante avant l'auto-compaction affichés dans l'embed de fin de session ; avertissement ⚠️ au-dessus de 83,5 %
- **Détection de compaction** — Notifie dans le fil lorsqu'une compaction de contexte se produit (type de déclencheur + nombre de tokens avant compaction)
- **Notification de blocage prolongé** — Message dans le fil après une absence d'activité (réflexion étendue ou compression de contexte) ; se réinitialise automatiquement quand Claude reprend. Les seuils tiennent compte du modèle : 30 s pour les modèles standard, 120 s pour Opus (qui a de plus longues pauses de réflexion)
- **Notifications de timeout** — Embed avec temps écoulé et conseils de reprise en cas de timeout
- **Affichage StatusLine** — Lorsque Claude configure un `statusLine` (via `/statusline-setup`), le statut actuel est affiché dans Discord après chaque session sous forme d'indicateur concis et toujours visible
- **Indicateur de fournisseur d'API** — Après chaque session, une ligne `🔗 API: <provider>` indique quel endpoint la CLI utilise réellement (`Anthropic API (direct)`, `AWS Bedrock`, `Google Vertex AI`, `Azure AI Foundry`, ou une URL de base personnalisée), dérivé de l'environnement réel du sous-processus pour que les superpositions d'env CLI soient reflétées. Toujours affiché — même sans `statusLine` configuré.
- **Boîte de réception des fils** — Avec `THREAD_INBOX_ENABLED=true`, le tableau de bord affiche une section 📬 persistante : après la fin de chaque session, Claude classe le message final (`waiting` / `done` / `ambiguous`) via un léger appel `claude -p` ; les fils en attente de votre réponse survivent aux redémarrages du bot et sont mis en avant jusqu'à ce que vous répondiez

#### 🔌 Entrée et Compétences
- **Support des pièces jointes** — Fichiers texte automatiquement ajoutés au prompt (jusqu'à 5 fichiers, 200 Ko chacun / 500 Ko au total ; les fichiers trop volumineux sont tronqués avec un avis plutôt qu'ignorés) ; images envoyées comme URLs CDN Discord via `--input-format stream-json` (jusqu'à 4 × 5 Mo) ; les longs messages collés que Discord convertit automatiquement en pièces jointes (sans `content_type`) sont gérés par détection basée sur l'extension
- **Livraison de fichiers à la demande** — Demandez à Claude de « m'envoyer » ou « joindre » un fichier et il écrit le chemin dans `.ccdb-attachments` ; le bot le lit et livre le fichier comme pièce jointe Discord à la fin de la session. Les instructions locales peuvent aussi exiger que les livrables écrits substantiels soient enregistrés en Markdown et joints.
- **Exécution de compétences** — Commande `/skill` avec autocomplétion, arguments optionnels, reprise dans le fil ; les compétences des plugins installés sont aussi découvertes automatiquement
- **Hot reload** — Les nouvelles compétences ajoutées à `~/.claude/skills/` sont détectées automatiquement (actualisation 60 s, sans redémarrage)

### Concurrence et Coordination
- **Instructions Worktree auto-injectées** — Chaque session est invitée à utiliser `git worktree` avant de toucher un fichier
- **Nettoyage automatique des worktrees** — Les worktrees de session (`wt-{thread_id}`) sont supprimés automatiquement à la fin de la session et au démarrage du bot ; les worktrees sales ne sont jamais supprimés automatiquement (invariant de sécurité)
- **Registre de sessions actives** — Registre en mémoire ; chaque session voit ce que font les autres
- **AI Lounge** — Canal « salon de repos » partagé ; contexte injecté comme instructions système/développeur spécifiques au backend (éphémères, ne s'accumulent jamais dans l'historique) pour que les longues sessions ne rencontrent jamais « Prompt is too long » ; les sessions publient leurs intentions, lisent le statut des autres et vérifient avant les opérations perturbatrices ; les humains le voient comme un fil d'activité en direct
- **Observabilité inter-sessions** — `GET /api/sessions` liste chaque session (active et stockée) avec son état, son répertoire de travail et sa dernière note de lounge ; `GET /api/threads/{thread_id}/messages` lit la conversation d'un autre fil. En lecture seule, pour qu'une session puisse regarder avant d'éditer — y compris les sessions qui n'ont jamais rien publié dans le lounge
- **Réclamations de ressources** — `POST /api/claims` réserve un dépôt, une issue ou un fichier avant le début du travail ; une deuxième session demandant la même ressource obtient un 409 avec le fil, la note et l'état en direct du détenteur. Consultatives et bornées par TTL (2 h par défaut, 24 h max), pour qu'une session morte ne puisse pas bloquer une ressource indéfiniment
- **Relais de session à session** — `POST /api/threads/{thread_id}/message` permet à une session de parler à une autre lorsqu'elles sont déjà entrées en collision ; `queue` attend le tour du destinataire, `interrupt` l'interrompt par SIGINT. Chaque relais est publié dans le fil (jamais un canal détourné), enveloppé dans un marqueur pour ne pas être confondu avec l'humain, et borné par des limites de sauts/récupération/débit pour que deux sessions ne puissent pas boucler
- **Détection automatique de collisions** — `CollisionWatchCog` compare les fichiers que les sessions actives ont réellement écrits (enregistrés depuis `Write`/`Edit`/`MultiEdit`/`NotebookEdit`) une fois par minute ; deux sessions écrivant le même fichier en moins de 15 minutes sont annoncées dans l'AI Lounge et dans les deux fils. Attrape les chevauchements que personne n'a annoncés ; une alerte par paire toutes les 30 minutes, et n'interrompt jamais un tour en cours
- **Canal de coordination** — La variable d'environnement `COORDINATION_CHANNEL_ID` est utilisée comme repli par défaut pour le canal AI Lounge (pas d'événements de cycle de vie séparés côté bot)

### Tâches Planifiées
- **SchedulerCog** — Exécuteur de tâches périodiques adossé à SQLite avec une boucle maître de 30 secondes
- **Auto-enregistrement** — Claude enregistre des tâches via `POST /api/tasks` pendant une session de chat
- **Sans changements de code** — Ajoutez, supprimez ou modifiez des tâches à l'exécution
- **Activer/désactiver** — Mettez en pause des tâches sans les supprimer (`PATCH /api/tasks/{id}`)

### Automatisation CI/CD
- **Déclencheurs Webhook** — Déclenchez des tâches Claude Code depuis GitHub Actions ou tout système CI/CD
- **Auto-mise à jour** — Mettez à jour le bot automatiquement lors de la publication de packages en amont
- **Redémarrage DrainAware** — Attend que les sessions actives se terminent avant de redémarrer
- **Marquage automatique de reprise** — Les sessions actives sont automatiquement marquées pour reprise lors de tout arrêt (redémarrage de mise à jour via `AutoUpgradeCog`, ou tout autre arrêt via `ClaudeChatCog.cog_unload()`) ; au redémarrage, Claude rapporte son état précédent et reconfirme avec l'utilisateur avant de reprendre tout travail d'implémentation
- **Approbation de redémarrage** — Barrière optionnelle pour confirmer les mises à jour ; approuvez via une réaction ✅ dans le fil de mise à jour ou via un bouton publié dans le canal parent ; le bouton se republie en bas à mesure que de nouveaux messages arrivent pour rester visible
- **Déclencheur manuel de mise à jour** — La commande slash `/upgrade` permet aux utilisateurs autorisés de déclencher le pipeline de mise à jour directement depuis Discord (opt-in via `slash_command_enabled=True`)

### Gestion de Session
- **Aide intégrée** — `/help` affiche toutes les commandes slash disponibles et l'usage de base (éphémère, visible uniquement par l'appelant)
- **Synchronisation de session** — Importez des sessions CLI comme fils Discord (`/sync-sessions`) ; `/sync-settings` pour voir ou modifier les préférences de synchronisation (style de fil, fenêtre temporelle, résultats minimum)
- **Liste de sessions** — `/sessions` avec filtrage par origine (Discord / CLI / toutes) et fenêtre temporelle
- **Reprise de session** — `/resume` affiche un menu de sélection des sessions récentes (jusqu'à 25) et reprend celle choisie dans un nouveau fil ; paramètre `query` optionnel pour la recherche par mot-clé (correspond au résumé et au répertoire de travail) ; `filter=orphaned` optionnel pour n'afficher que les sessions issues de fils supprimés ; fonctionne depuis n'importe quel canal ou fil — crée toujours un nouveau fil dans le canal principal configuré
- **Infos de reprise** — `/resume-info` affiche la commande CLI pour continuer la session actuelle dans un terminal (fil uniquement)
- **Effacer la session** — `/clear` réinitialise la session Claude Code du fil actuel, repartant de zéro sans créer de nouveau fil
- **Reprise au démarrage** — Les sessions interrompues redémarrent automatiquement après tout redémarrage du bot ; `AutoUpgradeCog` (redémarrages de mise à jour) et `ClaudeChatCog.cog_unload()` (tous les autres arrêts) les marquent automatiquement, ou utilisez `POST /api/mark-resume` manuellement
- **Spawn programmatique** — `POST /api/spawn` crée un nouveau fil Discord + session Claude depuis n'importe quel script ou sous-processus Claude ; renvoie immédiatement un 201 non bloquant après la création du fil
- **Injection de l'ID de fil** — La variable d'environnement `DISCORD_THREAD_ID` est passée à chaque sous-processus Claude, permettant aux sessions de créer des sessions enfants via `$CCDB_API_URL/api/spawn`
- **Affichage StatusLine** — Si votre `settings.json` de Claude Code a un `statusLine` configuré, sa sortie est affichée dans Discord après chaque réponse de session
- **Gestion des worktrees** — `/worktree-list` affiche tous les worktrees de session actifs avec statut propre/sale ; `/worktree-cleanup` supprime les worktrees propres orphelins (prend en charge un aperçu `dry_run`)
- **Changement de modèle à l'exécution** — `/model-show` affiche le modèle global actuel et le modèle de session par fil ; `/model-set` change le modèle pour toutes les nouvelles sessions sans redémarrage
- **Permissions d'outils à l'exécution** — `/tools-show` affiche les outils actuellement autorisés ; `/tools-set` ouvre un menu de sélection pour activer/désactiver des outils ; `/tools-reset` revient au défaut `.env` — le tout sans redémarrage
- **Utilisation du contexte** — `/context` affiche le pourcentage de la fenêtre de contexte avec une barre de progression visuelle ; avertissement ⚠️ à l'approche du seuil d'auto-compaction de 83,5 % ; éphémère (visible uniquement par l'appelant)
- **Utilisation des limites de débit** — `/usage` affiche l'utilisation des limites de débit de l'API Claude avec une barre en pourcentage et un compte à rebours jusqu'à la réinitialisation pour les fenêtres de 5 heures et 7 jours ; indicateur ⚠️ quand l'utilisation ≥ 80 %
- **Rembobinage de conversation** — `/rewind` affiche un menu de sélection des tours utilisateur passés et tronque le JSONL de session au point choisi, supprimant ce message et tout ce qui suit pour que la session reprenne à l'état exact d'avant ce tour ; conserve tous les fichiers de travail que Claude a créés ; utile lorsqu'une session a déraillé
- **Bifurcation de conversation** — `/fork` branche le fil actuel vers un nouveau fil qui continue depuis le même état de session via `--fork-session`, créant une copie de session véritablement indépendante ; vous permet d'explorer une autre direction sans affecter l'original

### Sécurité
- **Pas d'injection de shell** — `asyncio.create_subprocess_exec` uniquement, jamais `shell=True`
- **Validation de l'ID de session** — Regex stricte avant de passer à `--resume`
- **Prévention d'injection de flags** — Séparateur `--` avant tous les prompts
- **Isolation des secrets** — Token du bot supprimé de l'environnement du sous-processus
- **Autorisation des utilisateurs** — `allowed_user_ids` restreint qui peut invoquer Claude
- **Prévention d'injection dans les logs** — Les valeurs d'API fournies par l'utilisateur sont assainies (sauts de ligne supprimés) avant écriture dans les logs

---

## Démarrage Rapide — Claude ou Codex dans Discord en 5 Minutes

**Prérequis :**

- Python 3.12+
- Au moins l'un des deux :
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — installé et authentifié (`claude login`). Recommandé pour les abonnés Anthropic Pro/Max.
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` puis `codex login`. Utilise votre abonnement ChatGPT Plus/Pro/Business existant.
- Vous pouvez installer les deux. Basculez entre eux à l'exécution avec `/backend` (voir [Changement de Backend](#changement-de-backend--claude--codex-à-la-demande)).

**Support des plateformes :** Principalement développé et testé sur **Linux**. macOS et Windows sont pris en charge et passent la CI, mais reçoivent moins de tests réels — les rapports de bugs sont les bienvenus.

### Étape 1 — Créer un Bot Discord (une fois, ~2 minutes)

1. Allez sur [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. Naviguez vers **Bot** → activez **Message Content Intent** sous Privileged Gateway Intents
3. Copiez le **Token** du bot
4. Allez dans **OAuth2 → URL Generator** : Portées `bot` + `applications.commands`, Permissions : Send Messages, Create Public Threads, Send Messages in Threads, Add Reactions, Manage Messages, Read Message History
5. Ouvrez l'URL générée → invitez le bot sur votre serveur

### Étape 2 — Exécuter l'Assistant de Configuration

Pas besoin de cloner ni d'éditer `.env` — l'assistant le fait pour vous :

```bash
# With uvx (no install needed):
uvx --from "git+https://github.com/ebibibi/ebi-agent-chat-relay.git" ccdb setup

# Or after cloning:
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv run ccdb setup
```

L'assistant va :
1. Valider votre token de bot auprès de l'API Discord
2. **Lister automatiquement les canaux disponibles** — choisissez simplement un numéro (pas d'ID à copier)
3. Demander votre répertoire de travail et votre préférence de modèle
4. Écrire `.env` et proposer de démarrer le bot immédiatement

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

### Démarrer / Arrêter

```bash
ccdb start    # start the bot (reads .env in current dir)
ccdb start --env /path/to/.env   # custom .env location
```

Envoyez un message dans le canal configuré — Claude répondra dans un nouveau fil.

### Exécuter comme Service systemd (Production)

Pour les déploiements en production, exécutez le bot sous systemd afin qu'il démarre au boot et redémarre automatiquement en cas d'échec.

Le dépôt fournit un modèle prêt à adapter (`discord-bot.service`) et un script de pré-démarrage (`scripts/pre-start.sh`). Copiez-les et personnalisez-les :

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

**Ce que fait `scripts/pre-start.sh`** (exécuté comme `ExecStartPre` avant le processus du bot) :

1. **`git pull --ff-only`** — récupère le dernier code depuis `origin main`
2. **`uv sync`** — maintient les dépendances synchronisées avec `uv.lock`
3. **Validation des imports** — vérifie que `claude_discord.main` s'importe proprement
4. **Rollback automatique** — si l'import échoue, revient au commit précédent et réessaie ; publie une notification via webhook Discord en cas d'échec ou de succès
5. **Nettoyage des worktrees** — supprime les worktrees git périmés laissés par des sessions plantées

Le script détecte dynamiquement la racine du dépôt (via `readlink -f` sur `$0`), donc il fonctionne pour tout utilisateur quel que soit l'endroit où il a cloné le dépôt — aucune édition de chemin nécessaire dans le script lui-même. Il découvre aussi automatiquement le binaire `uv` depuis le `PATH` ; surchargez via la variable d'environnement `CCDB_UV_BIN` si besoin.

Le script nécessite la variable `DISCORD_WEBHOOK_URL` dans `.env` pour les notifications d'échec (optionnel — le script fonctionne sans).

#### PATH de la chaîne d'outils — définissez-le dans `.env`

systemd démarre une unité avec un `PATH` par défaut minimal (typiquement `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`) et ne source jamais `~/.bashrc` ni `~/.profile`. Le bot hérite de ce `PATH`, tout comme chaque session Claude/Codex qu'il crée — les sessions s'exécutent avec l'environnement du bot, moins les secrets retirés.

Le résultat est déroutant : un build qui fonctionne dans votre terminal échoue dans une session Discord, ou s'exécute silencieusement contre un binaire système plus ancien, parce que les outils installés sous `~/.local/bin` ou `~/.npm-global/bin` sont invisibles pour le service.

Comme le service charge `.env` via `EnvironmentFile=`, définir `PATH` là-bas corrige le bot et toutes les sessions d'un coup :

```bash
# .env — match your interactive shell's PATH
PATH=/home/you/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
```

Redémarrez le service (`sudo systemctl restart mybot.service`), puis confirmez depuis une session Discord en demandant à Claude d'exécuter `which node && node --version`.

### Cogs Personnalisés (Étendez Sans Forker)

Ajoutez vos propres fonctionnalités en déposant des fichiers Python dans un répertoire — sans fork, sans sous-classe, sans package :

```bash
ccdb start --cogs-dir ./my-cogs/
# Or: CUSTOM_COGS_DIR=./my-cogs ccdb start
```

Chaque fichier `.py` du répertoire doit exposer un `async def setup(bot, runner, components)` :

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

Les fichiers préfixés par `_` sont ignorés. Si un Cog échoue au chargement, les autres se chargent quand même normalement.

Voir [`examples/ebibot/`](examples/ebibot/) pour un exemple complet et réel avec rappels, watchdog Todoist, auto-mise à jour et synchronisation de docs.

**Exemples intégrés dans `examples/ebibot/cogs/` :**

| Cog | Objectif |
|-----|---------|
| `ReminderCog` | Planification de rappels basée sur Discord |
| `WatchdogCog` | Watchdog Todoist / service externe |
| `AutoUpgradeCog` | Mise à jour de package déclenchée par webhook |
| `DocsSyncCog` | Synchronisation automatique de la documentation au push |
| `AlertResponderCog` | Surveillance d'alertes générique — transfère les alertes des systèmes de monitoring vers Discord et déclenche une session d'investigation Claude Code |

---

### Bot Minimal (Installer comme Package)

Si vous avez déjà un bot discord.py, ajoutez plutôt ccdb comme package :

```bash
uv add git+https://github.com/ebibibi/ebi-agent-chat-relay.git
```

Créez un `bot.py` :

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

`setup_bridge()` connecte automatiquement tous les Cogs. Mettez à jour vers la dernière version :

```bash
uv lock --upgrade-package claude-code-discord-bridge && uv sync
```

#### Configuration Multi-Canal

Pour déployer le bot sur plusieurs canaux Discord, passez `claude_channel_ids` en plus de (ou à la place de) `claude_channel_id` :

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

Chaque canal est totalement indépendant — les messages dans l'un des canaux configurés créent un nouveau fil de session Claude, et les commandes `/skill` fonctionnent dans tous. `claude_channel_id` est conservé pour rétrocompatibilité et sert de cible de repli pour la création de fils lorsque la commande `/skill` est invoquée en dehors d'un canal configuré.

#### Canaux Sur Mention Uniquement

Pour que le bot ne réponde **que lorsqu'il est @mentionné** dans certains canaux (utile pour les canaux partagés où vous ne voulez pas qu'il réagisse à chaque message) :

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 222},
    mention_only_channel_ids={222},  # bot ignores messages in #222 unless @mentioned
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Ou via une variable d'environnement (IDs de canaux séparés par des virgules) :

```
MENTION_ONLY_CHANNEL_IDS=222,333
```

Les fils **héritent de la politique de leur canal parent**. Un fil qu'un humain crée dans un canal sur mention uniquement ne démarre pas de session Claude — sinon n'importe qui pourrait contourner le réglage juste en ouvrant un fil. Claude n'intervient dans un tel fil que lorsque :

- le bot est explicitement **@mentionné** dans le message, ou
- ccdb **possède déjà le fil** — un fil de session que le bot a créé, ou un fil créé via `/api/spawn`. Une fois qu'une session existe, chaque réponse est traitée normalement sans nécessiter de mention.

Les fils sous des canaux qui *ne* figurent *pas* dans `mention_only_channel_ids` ne sont pas affectés et sont toujours traités.

#### Canaux à Réponse en Ligne

Pour que le bot réponde **directement dans le canal** (sans créer de fil) pour certains canaux (utile pour les canaux de commandes personnels où les fils ajoutent un encombrement inutile) :

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 333},
    inline_reply_channel_ids={333},  # bot replies inline in #333, no thread created
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Ou via une variable d'environnement (IDs de canaux séparés par des virgules) :

```
INLINE_REPLY_CHANNEL_IDS=333,444
```

En mode réponse en ligne, la réponse de Claude est envoyée directement comme message dans le canal plutôt que de créer un nouveau fil. Les sessions sont tout de même suivies en interne, donc les messages de suivi dans le canal continuent la même session Claude.

#### Canaux en Chat Uniquement

Pour masquer l'UI technique (embeds d'outils, blocs de réflexion, avis de début/fin de session, listes de tâches) et n'afficher **que les réponses textuelles de Claude** dans certains canaux — utile pour les canaux publics où des utilisateurs non techniques observent :

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 444},
    chat_only_channel_ids={444},  # only text shown in #444; tool details hidden
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Ou via une variable d'environnement (IDs de canaux séparés par des virgules) :

```
CHAT_ONLY_CHANNEL_IDS=444,555
```

En mode chat uniquement, les demandes de permission et les invites `AskUserQuestion` sont **toujours affichées** quel que soit le réglage — elles nécessitent une saisie humaine et doivent être visibles.

---

## Configuration

| Variable | Description | Défaut |
|----------|-------------|--------|
| `DISCORD_BOT_TOKEN` | Votre token de bot Discord | (requis) |
| `DISCORD_CHANNEL_ID` | ID de canal pour le chat Claude | (requis) |
| `CCDB_BACKEND` | Backend CLI à utiliser : `claude` (Claude Code CLI) ou `codex` (OpenAI Codex CLI) | `claude` |
| `CCDB_COMMAND` | Chemin ou nom du binaire CLI (remplace `CLAUDE_COMMAND`). Utilisé par le runner initial choisi selon `CCDB_BACKEND` ; supplanté par les deux variables par backend ci-dessous lorsque `/backend` bascule à l'exécution. | _(auto : `claude` ou `codex`)_ |
| `CCDB_CLAUDE_COMMAND` | Chemin explicite vers le binaire CLI Claude. Utilisé par `BackendFactory` chaque fois que `/backend claude` est actif, quel que soit le `CCDB_BACKEND` initial. Se rabat sur `CLAUDE_COMMAND`, puis `claude` (PATH). | (optionnel) |
| `CCDB_CODEX_COMMAND` | Chemin explicite vers le binaire CLI OpenAI Codex. Requis lors de l'exécution du bot sous systemd (le PATH par défaut du service n'inclut pas `~/.npm-global/bin`). Se rabat sur `codex` (PATH). | (optionnel) |
| `PATH` | Chemin de recherche des binaires pour le bot **et chaque session CLI qu'il crée** — les sessions héritent de l'environnement du bot. Définissez-le dans `.env` lors de l'exécution sous systemd, qui démarre les unités avec un PATH minimal et ne lit jamais `~/.bashrc` / `~/.profile`. Voir [PATH de la chaîne d'outils](#path-de-la-chaîne-doutils--définissez-le-dans-env). | (hérité du processus parent) |
| `CCDB_MODEL` | Modèle à utiliser (remplace `CLAUDE_MODEL`) | `sonnet` |
| `CCDB_PERMISSION_MODE` | Mode de permission pour la CLI (remplace `CLAUDE_PERMISSION_MODE`) | `acceptEdits` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | Ignorer toutes les vérifications de permission — remplace `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | `false` |
| `CCDB_WORKING_DIR` | Répertoire de travail pour la CLI (remplace `CLAUDE_WORKING_DIR`) | répertoire courant |
| `CCDB_ALLOWED_TOOLS` | Liste d'outils autorisés séparés par des virgules (remplace `CLAUDE_ALLOWED_TOOLS`) | (optionnel) |
| `CCDB_CHANNEL_IDS` | IDs de canaux supplémentaires, séparés par des virgules (remplace `CLAUDE_CHANNEL_IDS`) | (optionnel) |
| `CLAUDE_COMMAND` | Chemin ou nom du binaire CLI Claude (nom hérité — préférez `CCDB_COMMAND`). À utiliser pour épingler une version spécifique (par ex. `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`) — utile pour éviter les régressions des versions plus récentes de la CLI. | `claude` |
| `CLAUDE_MODEL` | Modèle à utiliser (hérité — préférez `CCDB_MODEL`) | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | Mode de permission pour la CLI (hérité — préférez `CCDB_PERMISSION_MODE`) | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | Ignorer toutes les vérifications de permission (hérité — préférez `CCDB_DANGEROUSLY_SKIP_PERMISSIONS`) | `false` |
| `CLAUDE_WORKING_DIR` | Répertoire de travail pour Claude (hérité — préférez `CCDB_WORKING_DIR`) | répertoire courant |
| `MAX_CONCURRENT_SESSIONS` | Maximum de sessions CLI Claude parallèles sur tous les chemins de code (chat, skills, planificateur, webhooks) | `3` |
| `SESSION_TIMEOUT_SECONDS` | Timeout d'inactivité de session | `300` |
| `DISCORD_OWNER_ID` | ID utilisateur à @-mentionner quand Claude a besoin d'une saisie | (optionnel) |
| `COORDINATION_CHANNEL_ID` | ID de canal utilisé comme repli par défaut pour le canal AI Lounge | (optionnel) |
| `MENTION_ONLY_CHANNEL_IDS` | IDs de canaux (séparés par des virgules) où le bot ne répond que lorsqu'il est @mentionné (les fils sous ces canaux héritent de la politique) | (optionnel) |
| `INLINE_REPLY_CHANNEL_IDS` | IDs de canaux (séparés par des virgules) où le bot répond en ligne (sans créer de fil) | (optionnel) |
| `CHAT_ONLY_CHANNEL_IDS` | IDs de canaux (séparés par des virgules) en mode chat uniquement — seules les réponses textuelles de Claude sont affichées ; tous les embeds techniques (outils, réflexion, infos de session, tâches) sont masqués | (optionnel) |
| `WORKTREE_BASE_DIR` | Répertoire de base à scanner pour les worktrees de session (active le nettoyage automatique) | (optionnel) |
| `CLI_SESSIONS_PATH` | Chemin vers `~/.claude/projects` pour la découverte de sessions CLI (active `/sync-sessions`) | (optionnel) |
| `CUSTOM_COGS_DIR` | Répertoire contenant les fichiers Cog personnalisés à charger au démarrage (voir [Cogs Personnalisés](#cogs-personnalisés-étendez-sans-forker)) | (optionnel) |
| `CLAUDE_ALLOWED_TOOLS` | Liste d'outils autorisés séparés par des virgules pour la CLI Claude (hérité — préférez `CCDB_ALLOWED_TOOLS`) | (optionnel) |
| `CLAUDE_CHANNEL_IDS` | IDs de canaux supplémentaires (séparés par des virgules) pour une configuration multi-canal (hérité — préférez `CCDB_CHANNEL_IDS`) | (optionnel) |
| `THREAD_INBOX_ENABLED` | Activer la boîte de réception de fils persistante (classe les sessions en `waiting`/`done`/`ambiguous` via `claude -p` ; affichée dans le tableau de bord des fils) | `false` |
| `THREAD_AUTO_RENAME` | Renommer automatiquement les titres des nouveaux fils via l'IA Claude — génère un titre court et descriptif à partir du premier message utilisateur via un appel `claude -p` en arrière-plan (ne retarde jamais le démarrage de la session) | `false` |
| `CCDB_CLI_ENV_FILE` | Chemin vers un fichier `KEY=VALUE` dont les variables sont fusionnées dans l'environnement du sous-processus CLI à chaque invocation. Les changements prennent effet immédiatement sans redémarrer le bot. Utile pour un routage d'API temporaire (par ex. Azure Foundry) | (optionnel) |
| `CCDB_LOG_FILE` | Chemin vers un fichier de log. Quand défini, un gestionnaire de fichier rotatif (10 Mo × 5 sauvegardes) est ajouté en plus du gestionnaire stdout par défaut. Utile pour la surveillance et les alertes. | (optionnel) |
| `API_HOST` | Adresse de liaison de l'API REST | `127.0.0.1` |
| `API_PORT` | Port de l'API REST (active l'API REST quand défini) | (optionnel) |
| `CCDB_INGEST_TOKEN` | Jeton Bearer pour `POST /api/ingest` (indépendant de `api_secret`) ; non défini ⇒ l'endpoint répond `503` | (optionnel) |
| `CCDB_INGEST_REQUIRE_COMPLETE` | Mettre à `1` pour refuser une ingestion avec un `409` quand son `attachments_manifest` prouve que des pièces jointes ont été perdues, au lieu de démarrer une session sur des preuves partielles | `0` |

### Modes de Permission — Ce Qui Fonctionne en Mode `-p`

Claude Code CLI s'exécute en **mode `-p` (non interactif)** lorsqu'il est utilisé via ccdb. Dans ce mode, la CLI **ne peut pas demander de permission** — les outils nécessitant une approbation sont immédiatement rejetés. C'est une [contrainte de conception de la CLI](https://code.claude.com/docs/en/headless), pas une limitation de ccdb.

| Mode | Comportement en mode `-p` | Recommandation |
|------|----------------------|----------------|
| `default` | ❌ **Tous les outils rejetés** — inutilisable | Ne pas utiliser |
| `acceptEdits` | ⚠️ Edit/Write auto-approuvés, Bash rejeté (Claude se rabat sur Write pour les opérations de fichiers) | Option minimale viable |
| `bypassPermissions` | ✅ Tous les outils approuvés | Fonctionne, mais préférez le flag ci-dessous |
| **`auto`** | ✅ **Sécurité classée par IA** — opérations sûres auto-approuvées, opérations dangereuses bloquées | **Recommandé** — meilleur équilibre entre sécurité et facilité d'usage |
| `plan` | ✅ Classé par IA (biais lecture seule) — similaire à auto mais plus conservateur | Pour les flux à forte lecture |
| **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`** | ✅ **Tous les outils approuvés, aucune vérification de sécurité** | Mode « yolo » hérité — à utiliser quand le mode auto est trop restrictif |

**Notre recommandation :** Définissez `CLAUDE_PERMISSION_MODE=auto`. Le mode auto utilise un classificateur IA pour approuver automatiquement les opérations sûres (éditions de fichiers, tests locaux, git push vers la branche de travail) tout en bloquant les dangereuses (force push, déploiements en production, fuite d'identifiants). Cela donne à Claude une autonomie complète pour le travail de développement normal sans le risque du « tout est permis » du mode yolo.

**Repli vers le mode yolo :** Si le mode auto bloque des opérations dont vous avez besoin, définissez plutôt `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`. Comme ccdb contrôle qui peut interagir avec Claude via `allowed_user_ids`, les vérifications de permission au niveau de la CLI ajoutent des frictions sans bénéfice de sécurité significatif. Le « dangerously » dans le nom reflète l'avertissement général de la CLI ; dans le contexte ccdb où l'accès est déjà verrouillé, c'est un choix pratique.

> **Note :** Lorsque `CLAUDE_PERMISSION_MODE` est défini sur `auto` ou `plan`, `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` est automatiquement ignoré — ces modes ont leurs propres classificateurs de sécurité qui seraient outrepassés par le flag yolo.

**Pour un contrôle fin**, utilisez `CLAUDE_ALLOWED_TOOLS` pour autoriser des outils spécifiques sans contourner entièrement les permissions :

```env
# Example: allow file operations and code execution, but not web access
CLAUDE_ALLOWED_TOOLS=Bash,Read,Write,Edit,Glob,Grep

# Example: read-only mode — Claude can explore but not modify
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
```

Noms d'outils courants : `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`. Définissez `CLAUDE_PERMISSION_MODE=default` en utilisant ceci (d'autres modes peuvent l'outrepasser).

**Changements à l'exécution via Discord :** Utilisez `/tools-set` pour changer les outils autorisés à l'exécution sans redémarrer le bot. Le réglage est persisté et prend effet immédiatement pour toutes les nouvelles sessions. Utilisez `/tools-show` pour voir la configuration actuelle, ou `/tools-reset` pour revenir au défaut `.env`.

> **Boutons de permission dans Discord :** Lorsque `CLAUDE_PERMISSION_MODE=default`, Claude émet des événements `permission_request` et ccdb affiche des boutons Autoriser/Refuser dans le fil. stdin est toujours maintenu ouvert (mode d'entrée stream-json) pour que le bot puisse renvoyer des réponses à Claude. Si vous utilisez le mode `auto` ou `plan`, Claude gère les permissions automatiquement sans nécessiter d'interaction utilisateur. Lorsque `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true` (mode yolo), ccdb **auto-approuve** immédiatement tout événement `permission_request` — aucun bouton Autoriser/Refuser n'est affiché. C'est un contournement pour une régression de la CLI (v2.1.78+, en amont [#35895](https://github.com/anthropics/claude-code/issues/35895)) où `--dangerously-skip-permissions` échoue à contourner la vérification de chemin sensible au niveau des fichiers.

---

## Configuration du Bot Discord

1. Créez une nouvelle application sur le [Portail Développeur Discord](https://discord.com/developers/applications)
2. Créez un bot et copiez le token
3. Activez **Message Content Intent** sous Privileged Gateway Intents
4. Invitez le bot avec ces permissions :
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Manage Messages (pour le nettoyage des réactions)
   - Read Message History

---

## Automatisation GitHub + Claude Code

### Exemple : Synchronisation Automatisée de la Documentation

À chaque push sur `main`, Claude Code :
1. Récupère les derniers changements et analyse le diff
2. Met à jour la documentation anglaise
3. Traduit en japonais (ou toute langue cible)
4. Crée un PR avec un résumé bilingue
5. Active l'auto-merge — fusionne automatiquement quand la CI passe

**GitHub Actions :**

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

**Configuration du bot :**

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

**Sécurité :** Les prompts sont définis côté serveur. Les webhooks ne font que sélectionner quel déclencheur activer — pas d'injection de prompt arbitraire.

### Exemple : Auto-Approuver les PRs du Propriétaire

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

## Tâches Planifiées

Enregistrez des tâches Claude Code périodiques à l'exécution — sans changements de code, sans redéploiements.

Depuis une session Discord, Claude peut enregistrer une tâche :

```bash
# Claude calls this inside a session:
curl -X POST "$CCDB_API_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Check for outdated deps and open an issue if found", "interval_seconds": 604800}'
```

Ou enregistrez depuis vos propres scripts :

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Weekly security scan", "interval_seconds": 604800}'
```

La boucle maître de 30 secondes récupère les tâches dues et crée automatiquement des sessions Claude Code.

---

## Auto-Mise à Jour

Mettez automatiquement à jour le bot lorsqu'une nouvelle version est publiée :

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

#### Déclenchement Manuel via `/upgrade`

Lorsque `slash_command_enabled=True`, tout utilisateur autorisé peut exécuter `/upgrade` directement dans Discord pour déclencher le même pipeline de mise à jour — sans webhook. La commande fonctionne à la fois depuis les canaux texte et les fils (l'exécuter dans un fil crée le fil de mise à jour dans le canal parent). Elle respecte les barrières `upgrade_approval` et `restart_approval`, crée un fil de progression et gère élégamment les exécutions concurrentes (répond de façon éphémère si une mise à jour est déjà en cours).

Avant de redémarrer, `AutoUpgradeCog` :

1. **Capture les sessions actives** — Collecte tous les fils avec des sessions Claude en cours (duck-typing : tout Cog avec un dict `_active_runners` est découvert automatiquement).
2. **Draine** — Attend que les sessions actives se terminent naturellement.
3. **Marque pour reprise** — Enregistre les IDs des fils actifs dans la table des reprises en attente. Au prochain démarrage, ces sessions sont reprises avec un prompt priorisant la sécurité : Claude rapporte ce sur quoi il travaillait et demande à l'utilisateur de reconfirmer avant de reprendre tout travail d'implémentation (changements de code, commits, PRs). Cela empêche des actions non intentionnelles après qu'une compression de contexte a pu effacer l'état d'approbation de la tâche.
4. **Redémarre** — Exécute la commande de redémarrage configurée.

Tout Cog avec une propriété `active_count` est découvert automatiquement et drainé :

```python
class MyCog(commands.Cog):
    @property
    def active_count(self) -> int:
        return len(self._running_tasks)
```

Le marquage des sessions est entièrement opt-in — il ne s'active que lorsque `setup_bridge()` a initialisé la base de données des sessions (le défaut). Une fois activé, les sessions reprennent avec la continuité `--resume` pour que Claude Code puisse reprendre exactement la conversation là où elle s'est arrêtée.

> **Couverture :** `AutoUpgradeCog` couvre les redémarrages déclenchés par mise à jour. Pour *tous les autres* arrêts (`systemctl stop`, `bot.close()`, SIGTERM), `ClaudeChatCog.cog_unload()` fournit un second filet de sécurité automatique.

---

## API REST

API REST optionnelle pour les notifications et la gestion des tâches. Nécessite aiohttp :

```bash
uv add "claude-code-discord-bridge[api]"
```

### Endpoints

| Méthode | Chemin | Description |
|--------|------|-------------|
| GET | `/api/health` | Vérification de santé |
| POST | `/api/notify` | Envoyer une notification immédiate |
| POST | `/api/schedule` | Planifier une notification |
| GET | `/api/scheduled` | Lister les notifications en attente |
| DELETE | `/api/scheduled/{id}` | Annuler une notification |
| POST | `/api/tasks` | Enregistrer une tâche Claude Code planifiée |
| GET | `/api/tasks` | Lister les tâches enregistrées |
| DELETE | `/api/tasks/{id}` | Supprimer une tâche |
| PATCH | `/api/tasks/{id}` | Mettre à jour une tâche (activer/désactiver, changer le planning) |
| POST | `/api/spawn` | Créer un nouveau fil Discord et démarrer une session Claude Code (non bloquant) ; passez `auto_start: false` pour différer Claude jusqu'à la première réponse de l'utilisateur |
| POST | `/api/ingest` | Spawn externe authentifié (extension de navigateur / webhook) avec pièces jointes base64 ; renvoie un `result_id` quand la récupération de résultats est configurée |
| GET | `/api/ingest/{result_id}` | Interroger la réponse finale de la session créée (`status`/`result`/`error`/`thread_id`) |
| POST | `/api/mark-resume` | Marquer un fil pour reprise automatique au prochain démarrage du bot |
| GET | `/api/lounge` | Lire les messages récents de l'AI Lounge |
| POST | `/api/lounge` | Publier un message dans l'AI Lounge (avec `label` optionnel) |
| GET | `/api/sessions` | Lister chaque session — active et stockée — avec l'état, le répertoire de travail et la dernière note de lounge (`state=running`, `exclude_thread`, `limit`) |
| GET | `/api/threads/{thread_id}/messages` | Lire la conversation d'un autre fil, du plus ancien au plus récent (`limit`) |
| POST | `/api/claims` | Réclamer une ressource avant d'y travailler — 201 si acquise, 409 avec le détenteur si prise |
| GET | `/api/claims` | Lister les réclamations actives (filtre `resource` optionnel) |
| DELETE | `/api/claims` | Libérer une réclamation (`resource`, `thread_id`, `force=true` optionnel) |
| POST | `/api/threads/{thread_id}/message` | Relayer un message d'une session à une autre (`text`, `from_thread`, `mode`, `hop`) |

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

## Architecture

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
    ingest_manifest.py     # Rapproche attachments_manifest des fichiers livrés
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

### Philosophie de Conception

- **Spawn de CLI, pas d'API** — Invoque `claude -p --output-format stream-json`, offrant toutes les fonctionnalités de Claude Code (CLAUDE.md, skills, outils, mémoire) sans les réimplémenter. S'exécute sur votre abonnement Claude Pro/Max — pas de clé API, pas de facturation par token
- **Concurrence d'abord** — Plusieurs sessions simultanées sont le cas attendu, pas un cas limite ; chaque session reçoit les instructions de worktree, le registre et l'AI Lounge gèrent le reste
- **Discord comme colle** — Discord fournit l'UI, le threading, les réactions, les webhooks et les notifications persistantes ; aucun frontend personnalisé nécessaire
- **Framework, pas application** — Installez comme package, ajoutez des Cogs à votre bot existant, configurez par code
- **Extensibilité sans code** — Ajoutez des tâches planifiées et des déclencheurs webhook sans toucher au code source
- **Sécurité par simplicité** — ~8000 lignes de Python auditable ; uniquement subprocess exec, pas d'expansion de shell

---

## Tests

```bash
uv run pytest tests/ -v --cov=claude_discord
```

Plus de 1690 tests couvrant l'analyseur, le chunker, le référentiel, le runner, le streaming, les déclencheurs webhook, l'auto-mise à jour (y compris la commande slash `/upgrade`, l'invocation depuis un fil et le bouton d'approbation), l'API REST, l'UI AskUserQuestion, le tableau de bord des fils, les tâches planifiées, la synchronisation de session, l'AI Lounge, l'observabilité inter-sessions, les réclamations de ressources, le relais de session à session, la reprise au démarrage, le changement de modèle, la détection de compaction, les embeds de progression TodoWrite, le chargeur de Cogs personnalisés, l'analyse des événements de permission/elicitation/mode-plan, la classification de la boîte de réception des fils, le comportement de verrou par fil, le protocole SessionBackend, CodexRunner, la fabrique de backends et la propriété de session inter-backends.

---

## Comment ce Projet a été Construit

**Ce codebase est développé par [Claude Code](https://docs.anthropic.com/en/docs/claude-code)**, l'agent de codage IA d'Anthropic, sous la direction de [@ebibibi](https://github.com/ebibibi). L'auteur humain définit les exigences, révise les pull requests et approuve tous les changements — Claude Code fait l'implémentation.

Cela signifie :

- **L'implémentation est générée par IA** — architecture, code, tests, documentation
- **La revue humaine s'applique au niveau des PR** — chaque changement passe par des pull requests GitHub et la CI avant d'être fusionné
- **Les rapports de bugs et PRs sont les bienvenus** — Claude Code sera utilisé pour les traiter
- **C'est un exemple concret de logiciel open source dirigé par l'humain et implémenté par l'IA**

Le projet a commencé le 2026-02-18 et continue d'évoluer grâce à des conversations itératives avec Claude Code.

---

## Exemple Réel

**[`examples/ebibot/`](examples/ebibot/)** — Un bot Discord personnel construit sur ce framework, inclus directement dans ce dépôt. Démontre le chargeur de Cog personnalisé avec :

- **ReminderCog** — Commande slash `/remind HH:MM "message"` + boucle d'envoi de 30 secondes
- **WatchdogCog** — Moniteur de tâches Todoist en retard (vérification toutes les 30 minutes, déduplication quotidienne, alertes selon la sévérité)
- **AutoUpgradeCog** — Auto-mise à jour via webhook GitHub + redémarrage systemctl
- **DocsSyncCog** — Traduction automatique de la documentation au push via webhook
- **AlertResponderCog** — Cog de surveillance d'alertes générique ; surveille une source configurable et publie des notifications annotées par sévérité sur Discord

Exécutez-le avec : `ccdb start --cogs-dir examples/ebibot/cogs/`

> Les Cogs personnalisés d'EbiBot étaient auparavant maintenus dans un [dépôt séparé](https://github.com/ebibibi/discord-bot). Ils sont désormais co-localisés ici pour que Claude Code ait toujours le contexte complet du framework et des personnalisations — évitant une duplication accidentelle de fonctionnalités.

---

## Inspiré Par

- [OpenClaw](https://github.com/openclaw/openclaw) — Réactions emoji de statut, debouncing de messages, chunking conscient des fences
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — Approche CLI spawn + stream-json
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — Modèles de contrôle de permissions
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — Modèle de conversation par fil

---

## Licence

MIT
