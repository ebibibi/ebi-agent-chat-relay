> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../../README.md) takes precedence.
> **참고:** 이 문서는 원본 영어 문서의 자동 번역본입니다.
> 내용이 다를 경우 [영어 버전](../../README.md)이 우선합니다.

# Claude & Codex Discord Bridge

*패키지명: `claude-code-discord-bridge` (케밥 케이스)*

[![CI](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**스마트폰에서 Claude Code _또는_ OpenAI Codex를 사용하세요. 멀티 스레드, 동시 진행, 실전 개발까지 모두 가능합니다.**

스마트폰 Discord 앱에서 Claude Code 또는 OpenAI Codex를 실행하고, 여러 스레드를 열어 개발 세션을 병렬로 진행하세요 — 키보드를 만지지 않고도 가능합니다. 각 Discord 스레드는 완전히 격리된 AI 세션이 됩니다. 한 스레드에서 기능 개발, 다른 스레드에서 PR 리뷰, 세 번째 스레드에서 백그라운드 작업 — 동시에, 심지어 스레드마다 다른 백엔드를 섞어서 사용할 수도 있습니다. 브리지가 모든 조율을 처리하여 세션들이 서로를 덮어쓰는 일이 없습니다.

**기존 구독을 그대로 활용하세요. API 키 씨름은 불필요합니다.** ccdb는 공식 CLI 위에서 실행됩니다 — Claude Code([Claude Pro/Max 구독](https://claude.ai/pricing)에 포함)와 OpenAI Codex([ChatGPT Plus/Pro/Business](https://chatgpt.com)에 포함). `/backend`로 백엔드를 전환하거나 스레드별 오버라이드를 설정하세요 — 팀 전체가 예측 가능한 비용으로 Discord를 통해 두 AI를 모두 사용할 수 있습니다.

**[English](../../README.md)** | **[日本語](../ja/README.md)** | **[简体中文](../zh-CN/README.md)** | **[Español](../es/README.md)** | **[Português](../pt-BR/README.md)** | **[Français](../fr/README.md)**

> **면책 조항:** 이 프로젝트는 Anthropic 또는 OpenAI와 제휴, 보증 또는 공식 관계가 없습니다. "Claude"와 "Claude Code"는 Anthropic, PBC의 상표이며, "OpenAI", "Codex", "ChatGPT"는 OpenAI의 상표입니다. 이것은 Claude Code CLI 및 OpenAI Codex CLI와 인터페이스하는 독립적인 오픈소스 도구입니다.

> **Claude Code로 완전히 구축.** 전체 코드베이스 — 아키텍처, 구현, 테스트, 문서 — 는 Claude Code 자체가 작성했습니다. 인간 저자는 자연어로 요구사항과 방향을 제공했습니다. [이 프로젝트의 구축 방법](#이-프로젝트의-구축-방법)을 참조하세요.

---

## 핵심 아이디어: 충돌 없는 병렬 세션

Claude Code 또는 OpenAI Codex에 별도의 Discord 스레드로 작업을 보내면, 어떤 백엔드를 선택했든 브리지는 자동으로 네 가지를 수행합니다:

1. **동시성 알림 주입** — 모든 세션의 시스템 프롬프트에 필수 지침이 포함됩니다: git worktree 생성, 그 안에서만 작업, 메인 작업 디렉토리 직접 수정 금지.

2. **활성 세션 레지스트리** — 실행 중인 각 세션은 다른 세션들의 존재를 알고 있습니다. 두 세션이 같은 저장소를 건드리려 할 경우, 충돌 대신 조율할 수 있습니다.

3. **AI Lounge** — 모든 프롬프트에 주입되는 세션 간 "휴게실". 시작 전에 각 세션은 최근 라운지 메시지를 읽어 다른 세션들이 무엇을 하고 있는지 파악하고, 자신이 건드리려는 저장소, 이슈 또는 파일을 클레임합니다([리소스 클레임](#리소스-클레임) 참조). 그러면 두 번째 세션은 작업이 중복되기 전에 되돌려 보내집니다. 파괴적인 작업(강제 푸시, 봇 재시작, DB 삭제) 전에 세션은 라운지를 먼저 확인하여 서로의 작업을 짓밟지 않습니다.

4. **백엔드 무관 표면** — 동일한 Discord UI, 슬래시 명령, 스케줄러, API, Lounge가 스레드가 Claude를 실행하든 Codex를 실행하든 동일하게 작동합니다. 원한다면 스레드마다 백엔드를 섞으세요 — 예를 들어 리팩토링에는 Claude, 코드 리뷰에는 Codex — `/backend`를 스레드별로 사용하면 됩니다.

```
Thread A (feature)    ──→  Claude Code  (worktree-A)  ─┐
Thread B (PR review)  ──→  OpenAI Codex (worktree-B)   ├─→  #ai-lounge
Thread C (docs)       ──→  Claude Code  (worktree-C)  ─┘    "A: auth refactor in progress"
                                                             "B: PR #42 review done (codex)"
                                                             "C: updating README"
```

경쟁 조건 없음. 작업 손실 없음. 머지 충돌 없음. 백엔드 종속 없음.

---

## 할 수 있는 것들

### 인터랙티브 채팅 (모바일 / 데스크탑)

Discord가 실행되는 모든 곳에서 Claude Code _또는_ OpenAI Codex 사용 — 스마트폰, 태블릿, 데스크탑. 각 메시지는 스레드를 생성하거나 계속하며, 지속적인 AI 세션과 1:1로 매핑됩니다. `/backend claude` 또는 `/backend codex`로 언제든지 백엔드를 전환하세요 — 스레드별로 또는 새 기본값으로 서버 전역에.

### 병렬 개발

여러 스레드를 동시에 엽니다. 각 스레드는 독자적인 컨텍스트, 작업 디렉토리, git worktree를 가진 독립적인 AI 세션(Claude Code 또는 Codex)입니다. 유용한 패턴:

- **기능 + 리뷰 병렬 진행**: 한 스레드에서 Claude로 기능 개발을 시작하면서 다른 스레드에서 Codex가 PR을 리뷰.
- **여러 기여자**: 팀원 각자가 자신의 스레드(그리고 선호하는 백엔드)를 가지며, AI Lounge를 통해 세션들이 서로의 동향을 파악.
- **안전한 실험**: 스레드 A에서 접근법을 시도하면서 스레드 B는 안정적인 코드를 유지.
- **두 AI에 같은 프롬프트로 A/B 테스트**: 같은 작업으로 두 스레드를 스폰하여 하나는 `/backend claude`, 하나는 `/backend codex`로 설정한 뒤 diff를 나란히 비교.

### 예약 작업 (SchedulerCog)

코드 변경, 재배포 없이 Discord 대화 또는 REST API로 주기적인 Claude Code 작업을 등록. 작업은 SQLite에 저장되고 설정 가능한 일정에 따라 실행됩니다. Claude는 세션 중에 `POST /api/tasks`를 사용하여 스스로 작업을 등록할 수 있습니다.

```
/skill name:goodmorning         → runs immediately
Claude calls POST /api/tasks    → registers a periodic task
SchedulerCog (30s master loop)  → fires due tasks automatically
```

### CI/CD 자동화

Discord webhook을 통해 GitHub Actions에서 Claude Code 작업을 트리거. Claude가 자율적으로 실행 — 코드 읽기, 문서 업데이트, PR 생성, 자동 머지 활성화.

```
GitHub Actions → Discord Webhook → Bridge → Claude Code CLI
                                                  ↓
GitHub PR ←── git push ←── Claude Code ──────────┘
```

**실제 예시:** `main`에 푸시할 때마다 Claude가 diff를 분석하고, 영문 + 일문 문서를 업데이트하고, 이중 언어 PR을 생성하고, 자동 머지를 활성화합니다. 사람 개입 전혀 없음.

### 세션 동기화

이미 Claude Code CLI를 직접 사용 중이신가요? `/sync-sessions`으로 기존 터미널 세션을 Discord 스레드로 동기화하세요. 최근 대화 메시지를 백필하여 컨텍스트 손실 없이 스마트폰에서 CLI 세션을 계속할 수 있습니다.

### AI Lounge

모든 동시 세션이 스스로를 알리고, 서로의 업데이트를 읽고, 파괴적인 작업 전에 조율하는 공유 "휴게실" 채널.

각 세션은 라운지 컨텍스트를 대화 기록의 일부가 아니라 에페메랄 시스템/개발자 지침(Claude의 경우 `--append-system-prompt`, Codex의 경우 `developer_instructions`)으로 자동으로 받습니다. 이렇게 하면 컨텍스트가 턴마다 누적되어 장기 세션에서 "Prompt is too long" 오류를 일으키는 것을 방지합니다. 주입되는 컨텍스트에는 다른 세션의 최근 메시지와, 파괴적인 작업을 하기 전에 먼저 확인하라는 규칙이 포함됩니다.

```bash
# Sessions post their intentions before starting:
curl -X POST "$CCDB_API_URL/api/lounge" \
  -H "Content-Type: application/json" \
  -d '{"message": "Starting auth refactor on feature/oauth — worktree-A", "label": "feature dev"}'

# Read recent lounge messages (also injected into each session automatically):
curl "$CCDB_API_URL/api/lounge"
```

라운지 채널은 사람이 볼 수 있는 활동 피드 역할도 합니다 — Discord에서 열면 모든 활성 Claude 세션이 현재 무엇을 하고 있는지 한눈에 볼 수 있습니다.

### 세션 간 관찰 가능성

라운지 메모는 세션에게 다른 스레드가 존재한다는 *사실*을 알려줍니다. 이 두 개의 읽기 전용 엔드포인트는 세션이 직접 가서 살펴볼 수 있게 해줍니다 — 그래서 같은 작업을 시작한 두 세션이 둘 다 앞으로 돌진하는 대신 겹침을 발견할 수 있습니다.

```bash
# Who else is alive, where are they working, what did they last announce?
curl "$CCDB_API_URL/api/sessions?exclude_thread=$DISCORD_THREAD_ID"

# Read that thread's actual conversation
curl "$CCDB_API_URL/api/threads/1529338965000192110/messages?limit=30"
```

`/api/sessions`는 세 가지 소스를 병합합니다: `sessions` 테이블(created_at, 작업 디렉토리, 백엔드), 인메모리 레지스트리(각 라이브 세션이 *지금 당장* 무엇을 하고 있는지), 그리고 각 스레드의 최신 라운지 메모. 세션은 턴이 진행 중인 동안 `"state": "running"`으로 나타납니다 — 라운지에 전혀 게시하지 않은 세션도 포함되며, 바로 그런 경우가 이것이 중요한 순간입니다. 실행 중인 턴이 없는 저장된 대화는 `"state": "history"`로 나타납니다. 이는 재개 가능한 기록이라는 뜻이며 에이전트가 작업이나 사용자 입력을 기다린다는 뜻이 아닙니다. 세션은 자체 Discord 토큰이 없으므로 봇이 읽기를 수행하고, 엔드포인트는 localhost 제어 평면에 머무릅니다.

### 리소스 클레임

관찰 가능성은 세션에게 충돌이 *일어났다*는 것을 알려줍니다. 클레임은 그것을 예방합니다 — 읽기도, 협상도, LLM 왕복도 없습니다. 세션은 자신이 작업하려는 것을 클레임하고, 같은 것을 요청하는 다음 세션은 어떤 작업도 하기 전에 거부됩니다.

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

클레임은 **권고적(advisory)** 입니다 — git이나 파일시스템 수준에서 강제하는 것은 아무것도 없습니다 — 그리고 모든 클레임은 TTL(기본 2시간, 최대 24시간)을 가지므로 죽은 세션이 리소스를 영원히 붙잡아둘 수 없습니다. 409 본문은 보유자가 아직 실행 중인지 보고하며, 호출자는 이를 통해 기다릴지, 다른 것을 작업할지, 아니면 `force=true`로 인계받을지 결정합니다. 리소스 이름은 자유 형식이며 정규화됩니다(대소문자 및 공백), 따라서 `repo:ccdb`와 `Repo: CCDB`는 동일한 클레임입니다.

라운지 프롬프트는 모든 세션에게 시작 전에 클레임하고 완료 시 해제하라고 지시합니다.

### 세션 간 릴레이

관찰 가능성은 세션이 동료를 볼 수 있게 하고, 클레임은 그들을 떼어 놓습니다. 두 세션이 이미 충돌한 경우, 그들은 실제로 대화해야 하며 — 둘 중 하나는 멈춰야 합니다.

```bash
curl -X POST "$CCDB_API_URL/api/threads/<their_thread_id>/message" \
  -H "Content-Type: application/json" \
  -d '{"text": "I started this at 13:02 on branch fix/parser and already pushed 3 commits.",
       "from_thread": "'$DISCORD_THREAD_ID'", "mode": "queue", "hop": 0}'
```

`on_message`는 봇이 작성한 것은 무엇이든 무시합니다 — 그 가드가 봇이 스스로에게 말하는 것을 막습니다 — 따라서 릴레이는 대신 이 엔드포인트를 거치며, `/api/spawn`과 동일한 방식입니다.

- **`mode: "queue"`** (기본값)는 수신자의 현재 턴이 끝날 때까지 기다립니다.
- **`mode: "interrupt"`** 는 진행 중인 턴에 SIGINT를 보내므로 "지금 멈춰"가 몇 초 안에 도달합니다. 수신자의 커밋되지 않은 작업이 손실될 수 있으므로 실제 충돌에만 사용하도록 예약되어 있습니다.
- 릴레이된 텍스트는 Claude에 도달하기 전에 **스레드에 게시**되므로, 지켜보는 사람들이 AI 간 교환 전체를 볼 수 있습니다. 릴레이는 결코 은밀한 뒷채널이 아닙니다.
- 모든 메시지는 발신 스레드를 명시하고 인간이 보낸 것이 아님을 밝히는 **마커로 감싸집니다** — 마커 없는 지침은 소유자가 직접 작성한 것처럼 그대로 따라질 것이기 때문입니다.

루프가 진짜 위험입니다(서로에게 답하는 두 세션은 토큰을 태우고 서로를 무한히 방해함). 그래서 가드가 모든 체인을 제한합니다: **최대 2홉**, 스레드 쌍당 60초 쿨다운, 발신자당 10분에 5회 릴레이, 그리고 자기 자신에게 보내기 금지. 거부는 이유와 함께 429로 돌아옵니다.

라운지 프롬프트는 또한 세션에게 대화가 상호 예의로 끝나지 않고 수렴하도록 타이브레이크 규칙을 제공합니다: 커밋이나 PR이 있는 쪽이 아직 조사 중인 쪽을 이깁니다. 그렇지 않으면 더 먼저 시작한 세션이 계속합니다. 동점이면 더 낮은 스레드 ID가 이깁니다. 물러나는 쪽은 먼저 브랜치를 푸시하고 배운 것을 인계합니다.

### 자동 충돌 감지

라운지와 클레임은 모두 세션이 무언가를 *말하는* 것에 의존합니다. 이것은 아무도 알리지 않은 겹침을, 세션이 실제로 한 일로부터 잡아냅니다: 두 라이브 세션이 15분 이내에 같은 파일에 쓰면, 어느 쪽이든 언급했든 안 했든 같은 것을 작업하고 있는 것입니다.

`EventProcessor`는 모든 쓰기 유형 도구 호출(`Write`, `Edit`, `MultiEdit`, `NotebookEdit`)의 경로를 기록합니다. `CollisionWatchCog`는 라이브 세션 전반에 걸쳐 그 집합을 1분에 한 번 비교합니다.

> 작업 디렉토리가 아니라 파일 경로를 쓰는 이유: 단일 사용자 호스트에서는 모든 세션이 같은 홈 디렉토리에서 시작하는 경향이 있으므로, `working_dir` 동일성은 모든 쌍을 플래그하며 아무 의미가 없습니다. 공유된 *편집 파일*은 거의 우연이 아닙니다. 읽기는 의도적으로 무시됩니다 — 두 세션이 같은 파일을 읽는 것은 정상이며 신호를 묻어버릴 것입니다.

겹침이 발견되면 워처는 다음을 게시합니다:

- **AI Lounge**의 한 줄. 이는 아무것도 방해하지 않고 토큰 비용 없이 모든 세션의 다음 턴에 주입되며,
- **충돌하는 각 스레드**의 메시지. 동료, 공유된 파일, 그리고 이를 해결하는 엔드포인트를 명시합니다.

그것은 결코 실행 중인 세션에 릴레이하지 않습니다 — 단순한 의심으로 턴을 선점하는 것은 충돌보다 더 큰 비용이 들 것입니다. 에스컬레이션은 위의 릴레이 엔드포인트를 사용하는 세션들의 결정입니다. 각 쌍은 최대 30분에 한 번만 알려지는데, 매분 반복되는 경고는 모두가 무시하게 되는 경고이기 때문입니다.

자동으로 활성화됩니다. 두 세션이 실제로 겹칠 때까지는 휴면 상태로 유지됩니다.

### 프로그래밍 방식 세션 생성

스크립트, GitHub Actions, 또는 다른 Claude 세션에서 Discord 메시지 상호작용 없이 새로운 Claude Code 세션을 스폰합니다.

```bash
# From another Claude session or a CI script:
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Run security scan on the repo", "thread_name": "Security Scan"}'
# Returns immediately with the thread ID; Claude runs in the background
```

**지연 시작 (`auto_start=false`)** — 스레드를 생성하고 시드 메시지를 게시하되 Claude를 즉시 시작하지 않습니다. Claude는 사용자가 답장할 때만 시작하며, 시드 메시지를 컨텍스트로 자동으로 받습니다.

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

이것은 정보를 먼저 표시하고 사용자가 Claude에 참여할지 결정하게 하려는 알림 스타일 워크플로우(예: 일일 브리핑, CI 경보)에 유용합니다.

Claude 서브프로세스는 `DISCORD_THREAD_ID`를 환경 변수로 받으므로, 실행 중인 세션은 자식 세션을 스폰하여 작업을 병렬화할 수 있습니다.

### 결과 조회가 가능한 인증된 외부 인제스트 (`/api/ingest`)

`POST /api/ingest`는 신뢰할 수 없는 외부 클라이언트(브라우저 확장 프로그램, 모바일 단축키, webhook)를 위한 **인증된, 첨부 파일 지원 스폰**입니다. `/api/spawn`(신뢰됨, localhost)과 달리, 전용 `ingest_token`(`CCDB_INGEST_TOKEN`으로 설정; `api_secret`과 독립)이 필요하며, base64 파일 첨부를 디스크에 기록하여 스폰된 세션이 읽을 수 있습니다. 실제 Discord 스레드를 생성하므로 전체 상호작용을 관찰할 수 있습니다.

세션은 **인터랙티브**합니다(계속 답장할 수 있는 실제 Discord 스레드) — 하지만 최종 답변을 프로그래밍 방식으로 되받을 수도 있습니다. 결과 조회가 구성된 경우(`setup_bridge()`를 통해 자동 연결), 응답에 `result_id`가 포함되며 `GET /api/ingest/{result_id}`로 세션의 최종 답변을 폴링합니다. 동일한 최종 답변은 Discord 스레드에 `ccdb-answer.md`로도 첨부되므로, 통합 시스템은 그 첨부 파일을 정본 답변 페이로드로 취급할 수 있습니다. 이것이 왕복 패턴입니다: 스레드 + 첨부 파일 게시 → 대기 → 답변 파일 읽기 또는 결과 폴링 → 자체 시스템(예: Teams 스레드)에 다시 기록, 그동안 Discord가 기록을 보관합니다.

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

이 엔드포인트는 옵트인 방식입니다: `ingest_token`이 구성되지 않으면 `POST`는 `503`을 반환합니다. 결과 조회를 사용할 수 없으면 `POST`는 단순히 `result_id`를 생략하고 `GET /api/ingest/{id}`는 `503`을 반환합니다 — 스폰 동작은 그 외에는 변경되지 않습니다. 요청 본문과 첨부 파일은 결과 저장소에 **저장되지 않습니다**(상태, 최종 텍스트, 스레드 ID만); 결과는 최대 200개 행으로 제한됩니다.

#### 검증된 첨부 파일 전달 (`attachments_manifest`)

클라이언트 쪽에서 첨부 파일이 사라져도 예전에는 아무도 알아챌 수 없었습니다. ccdb는 건네받은 것을 저장하고 아무도 대조할 수 없는 개수를 보고했기 때문에, 세션은 받은 적 없는 파일을 가진 것처럼 답변했습니다. **매니페스트**를 보내면 ccdb는 전달을 가정하는 대신 **검증**합니다 — 원본에서 찾은 첨부 파일마다 한 항목씩 나열하고, 그 바이트가 이 요청에 포함되어 있는지를 `status`로 알려주세요:

```bash
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "답장 초안을 작성해 줘",
       "attachments": [{"filename": "bundle.zip", "data": "<base64>"}],
       "attachments_manifest": [
         {"name": "shot.png",  "status": "embedded", "sha256": "…", "message": "返信 11"},
         {"name": "debug.log", "status": "linked", "url": "https://…", "message": "返信 12",
          "reason": "SharePoint download returned 403"}
       ]}'
# → {"status": "spawned", …, "attachments": {"verified": true, "complete": false,
#      "missing": [], "not_delivered": [{"name": "debug.log", "message": "返信 12", …}]}}
```

| `status` | 의미 |
|---|---|
| `embedded`(기본값) | 바이트가 이 요청에 포함됨 — ccdb는 대응하는 파일을 찾을 것으로 기대함 |
| `linked` | URL만 얻음(호스트 권한 없음, 인증 벽 등) |
| `skipped` | 의도적으로 보내지 않음(크기 제한, 필터링됨) |
| `failed` | 캡처 또는 다운로드 실패 |

ccdb는 각 `embedded` 항목을 실제 전달된 파일과 **sha256 우선**, 그다음 파일명 완전 일치, 그다음 번들러가 이름 충돌 시 붙이는 `4_image.png` 인덱스 접두사, 마지막으로 크기 순으로 대조합니다. 각 파일은 최대 한 번만 소비되므로 `image.png`라는 이름의 첨부 두 개가 도착한 파일 하나를 동시에 자기 것이라 주장할 수 없습니다. 설명되지 않은 것은 네 가지 경로로 드러납니다: **세션 프롬프트 최상단**의 ⚠️ 블록(누락된 파일을 나열하고 그 내용을 지어내지 말라고 세션에 지시하며, 최신 메시지에서 손실이 발생한 경우 별도 안내가 추가됨), 파일 옆에 기록되는 `ATTACHMENTS-REPORT.md` 원장, 위 응답의 `attachments` 판정, 그리고 로그의 `WARNING`입니다. `message`를 함께 보내면 프롬프트의 경로 목록이 원본 메시지 단위로 묶이고, 가장 최신 그룹이 먼저 읽어야 할 것으로 표시됩니다.

`CCDB_INGEST_REQUIRE_COMPLETE=1`(또는 `ingest_require_complete=True`)을 설정하면 손실이 있는 인제스트를 불완전한 근거로 세션을 시작하는 대신 `409`로 **거부**합니다 — 첨부 파일 자체가 요청의 본체인 경우에 적합합니다. 매니페스트를 아예 생략하면 아무것도 달라지지 않습니다: 해당 인제스트는 `verified: false`로 보고되며, 검증 완료로 보고되는 일은 결코 없습니다.

### 시작 재개

봇이 세션 도중 재시작되면, 중단된 Claude 세션이 봇이 다시 온라인이 될 때 자동으로 재개됩니다. 세션은 세 가지 방법으로 재개 대상으로 표시됩니다:

- **자동(업그레이드 재시작)** — `AutoUpgradeCog`가 패키지 업그레이드 재시작 직전에 모든 활성 세션을 스냅샷하고 자동으로 표시합니다.
- **자동(모든 종료)** — `ClaudeChatCog.cog_unload()`가 봇이 어떤 방식(`systemctl stop`, `bot.close()`, SIGTERM 등)으로든 종료될 때마다 실행 중인 모든 세션을 표시합니다.
- **수동** — 모든 세션이 `POST /api/mark-resume`를 직접 호출할 수 있습니다.

### 백엔드 전환 — 온디맨드 Claude / Codex

ccdb 3.0은 봇 재시작 없이 다음 세션을 처리할 AI를 변경하는 세 가지 슬래시 명령을 도입합니다:

- `/backend [name] [scope]` — 백엔드 표시 또는 전환. `name`은 `claude` 또는 `codex`. `scope`는 `thread`(이 스레드만) 또는 `global`(서버 전역 기본값). `scope`를 생략하면 명령이 자동 해석합니다: 스레드 안에서는 그 스레드로 범위가 지정되고, 그렇지 않으면 전역 기본값을 설정합니다.
- `/model [name] [scope]` — **현재** 백엔드가 사용하는 모델 표시 또는 전환. 각 백엔드는 자신의 모델 선호를 기억하므로, 백엔드를 앞뒤로 전환해도 선호하는 모델이 그대로 유지됩니다. 백엔드의 모델을 설정하지 않은 채로 두면 해당 CLI 자체의 기본값을 따릅니다(예: Codex는 `~/.codex/config.toml`의 `model`을 사용하므로, ccdb는 버전을 고정하는 대신 콘솔 기본값을 추적합니다).
- `/effort [level] [scope]` — 현재 백엔드가 사용하는 **추론 노력(reasoning effort)** 표시 또는 전환. 유효한 레벨은 백엔드별로 다릅니다: Claude는 `low/medium/high/max`를 허용하고, Codex는 `low/medium/high/xhigh/max/ultra`를 허용합니다(CLI의 `model_reasoning_effort`에 매핑됨). 설정하지 않은 채로 두면 CLI 기본값을 따릅니다.

세 명령 모두 `SettingsRepository`를 통해 SQLite에 저장되므로, 선택은 봇 재시작에도 유지됩니다. 인수 없이 호출하면 현재 전역 기본값과 스레드 오버라이드(있는 경우)를 출력합니다.

**이미 세션이 있는 스레드는 어떻게 되나요?** 세션 ID는 두 CLI 간에 상호 운용되지 않습니다 — Codex 롤아웃 ID를 `claude --resume`에 넘기거나(또는 Claude UUID를 `codex exec resume`에) 넘기면 CLI 수준에서 실패합니다. ccdb는 각 세션 ID를 어느 백엔드가 만들었는지 기록하므로, 전환이 스레드를 좌초시키는 일이 없습니다:

- **스레드 범위 전환** — 저장된 세션 ID는 삭제되어 다음 메시지가 새 백엔드에서 새로 시작합니다. *단*, 그 레코드가 전환 **대상** 백엔드에 속한다고 알려진 경우는 예외입니다. 따라서 다시 전환하는 것은 스레드의 이전 대화를 되찾는 유효한 방법입니다.
- **전역 전환** — 스레드별 레코드는 의도적으로 건드리지 않습니다. 스레드가 여전히 다른 백엔드의 세션 ID를 보유하고 있으면, 다음 메시지는 새 세션을 시작하고 재개하는 대신 이유를 설명하는 한 줄 알림을 게시합니다.

ccdb가 백엔드 소유권을 추적하기 전에 작성된 레코드에는 저장된 백엔드가 없습니다. 전역 전환은 예전과 똑같이 그것들을 재개합니다. 스레드 범위 전환은 깨진 재개의 위험을 무릅쓰는 대신 그것들을 지웁니다.

지금 누구와 대화하는지 절대 잊지 않도록 하는 시각적 신호:

- **Claude 세션**은 "🤖 Claude Code session started"라는 제목의 블러플(blurple) embed로 시작합니다.
- **Codex 세션**은 "🌀 OpenAI Codex session started"라는 제목의 OpenAI 청록색 embed로 시작합니다.
- 완료 embed는 평소의 소요 시간 / 비용 / 토큰 / 컨텍스트 지표와 함께 `🧠 Claude · sonnet` / `🧠 Codex · gpt-5.6-sol` 칩을 앞에 붙입니다. (백엔드의 모델이 CLI 기본값으로 남아 있으면 칩은 백엔드 이름만 표시합니다.)

구체적인 예시:

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

내부 동작:

- `BackendFactory` — 부팅 시 정적 구성(백엔드별 명령 경로, 권한 모드, 작업 디렉토리, 허용 도구, 타임아웃, append-system-prompt, effort, api_port, api_secret)을 캡처하고 필요에 따라 새로운 `ClaudeRunner` 또는 `CodexRunner`를 빌드합니다. `api_port`는 REST API 서버가 시작된 후 `setup_bridge`에 의해 자동으로 연결되므로, 팩토리로 빌드된 러너는 항상 서브프로세스 환경에 `CCDB_API_URL`이 주입되어 있습니다.
- `BackendSettings` — `SettingsRepository`에 대한 얇은 래퍼로, **thread > global > env** 우선순위로 활성 백엔드를 해석하고 슬래시 명령의 쓰기를 저장합니다.
- `SessionBackend` 프로토콜 — 두 러너가 모두 만족하는 추상 인터페이스. 내부 배선(cog, embed, view, 스케줄러, webhook 트리거)은 하나의 구체적인 러너 클래스가 아니라 `SessionBackend`를 받습니다.

**각 백엔드는 어디에서 인증하나요?** Claude Code는 `claude` CLI의 `claude login`을 통해 기존 Claude Pro/Max 구독을 사용합니다. Codex는 `codex` CLI의 `codex login`을 통해 기존 ChatGPT Plus/Pro/Business 구독을 사용합니다. ccdb는 원시 API 키를 절대 보지 않습니다 — 선택된 CLI가 무엇이든 그것으로 셸 아웃할 뿐입니다.

---

## 기능 목록

### 인터랙티브 채팅

#### 🔗 세션 기본
- **채팅 전용 모드** — `CHAT_ONLY_CHANNEL_IDS`에 채널이 포함되면, Claude의 텍스트 응답만 표시되고 도구 embed, 사고 블록, 세션 시작/완료 embed, 할 일 목록은 숨겨집니다. 권한 요청과 `AskUserQuestion`은 항상 표시됩니다. 비기술 사용자가 지켜보는 공개 채널에 이상적입니다.
- **스레드 = 세션** — Discord 스레드와 Claude Code 세션 간 1:1 매핑
- **목표 추적** — `/goal <condition>`이 완료 조건을 설정하면, Claude는 조건이 충족될 때까지 계속 작업합니다. 조건을 생략하면 상태를 확인하고, `clear`를 전달하면 취소합니다
- **세션 지속성** — `--resume`을 통해 메시지 간 대화 재개
- **자동 Codex 재개 복구** — 재개된 Codex 세션이 출력을 내기 전에 WebSocket을 반복적으로 잃으면, ccdb는 이전 대화의 제한된 텍스트 전용 전사본으로 대체 세션을 시작합니다. 이미지와 도구 페이로드는 제외됩니다
- **동시 세션** — 설정 가능한 제한으로 여러 병렬 세션
- **지우지 않고 중지** — `/stop`이 세션을 재개용으로 보존하면서 중지합니다
- **세션 중단** — 활성 스레드에 새 메시지를 보내면 실행 중인 세션에 SIGINT를 보내고 새 지침으로 새로 시작합니다. 수동 `/stop`이 필요 없습니다
- **스레드 자동 이름 변경** — `THREAD_AUTO_RENAME=true`이면 각 새 스레드가 첫 메시지에서 파생된 Claude 생성 제목으로 자동 이름 변경됩니다(백그라운드 작업, 세션 시작을 절대 지연시키지 않음)

#### 📡 실시간 피드백
- **실시간 상태** — 이모지 반응: 🧠 사고 중, 🛠️ 파일 읽기, 💻 편집 중, 🌐 웹 검색
- **스트리밍 텍스트** — Claude가 작업하는 동안 중간 어시스턴트 텍스트가 나타남
- **도구 결과 embed** — 라이브 도구 호출 결과가 경과 시간과 함께 즉시 표시(0초)되고 5초마다 증가; 단일 라인 출력은 인라인으로, 여러 라인 출력은 펼치기 버튼 뒤에 접혀 표시
- **확장 사고** — 추론을 스포일러 태그 embed로 표시 (클릭하여 펼치기)
- **스레드 대시보드** — 어떤 스레드가 활성/대기 중인지 표시하는 라이브 고정 embed; 입력이 필요할 때 소유자를 @멘션

#### 🤝 Human-in-the-Loop
- **인터랙티브 질문** — `AskUserQuestion`이 Discord 버튼 또는 선택 메뉴로 렌더링됨; 세션이 답변으로 재개; 봇 재시작 후에도 버튼 유지; 입력이 필요할 때 요청자를 @멘션
- **계획 모드** — Claude가 `ExitPlanMode`를 호출하면 Discord embed에 전체 계획과 승인/취소 버튼이 표시됨; Claude는 승인 후에만 진행; 프롬프트 시 요청자를 @멘션; 5분 타임아웃 시 자동 취소
- **도구 권한 요청** — Claude가 도구 실행 권한이 필요하면 Discord에 도구 이름과 입력이 있는 허용/거부 버튼이 표시됨; 요청자를 @멘션; 2분 후 자동 거부
- **MCP Elicitation** — MCP 서버가 Discord를 통해 사용자 입력을 요청할 수 있음(form 모드: JSON 스키마로부터 최대 5개의 Modal 필드; url 모드: URL 버튼 + Done 확인); 요청자를 @멘션; 5분 타임아웃
- **TodoWrite 실시간 진행** — Claude가 `TodoWrite`를 호출하면 단일 Discord embed가 게시되고 각 업데이트마다 인플레이스로 편집됨; ✅ 완료, 🔄 진행 중(`activeForm` 레이블 포함), ⬜ 대기 항목 표시

#### 📊 관찰 가능성
- **토큰 사용량** — 세션 완료 embed에 캐시 히트율과 토큰 수 표시
- **컨텍스트 사용량** — 컨텍스트 창 백분율(입력 + 캐시 토큰, 출력 제외)과 자동 압축까지 남은 용량을 세션 완료 embed에 표시; 83.5% 초과 시 ⚠️ 경고
- **압축 감지** — 컨텍스트 압축이 발생하면 스레드 내에서 알림(트리거 유형 + 압축 전 토큰 수)
- **하드 스톨 알림** — 활동이 없을 때(확장 사고 또는 컨텍스트 압축) 스레드 메시지; Claude가 재개하면 자동으로 리셋. 임계값은 모델 인식형입니다: 표준 모델은 30초, Opus는 120초(더 긴 사고 멈춤이 있음)
- **타임아웃 알림** — 타임아웃 시 경과 시간과 재개 안내가 포함된 embed
- **StatusLine 표시** — Claude가 (`/statusline-setup`을 통해) `statusLine`을 구성하면, 현재 상태가 각 세션 후 Discord에 간결하고 항상 보이는 표시기로 나타남
- **API 제공자 표시** — 각 세션 후, `🔗 API: <provider>` 라인이 CLI가 실제로 사용 중인 엔드포인트(`Anthropic API (direct)`, `AWS Bedrock`, `Google Vertex AI`, `Azure AI Foundry`, 또는 커스텀 base URL)를 표시. 실제 서브프로세스 환경에서 파생되므로 CLI env 오버레이가 반영됨. `statusLine` 구성 여부와 무관하게 항상 표시됨
- **스레드 수신함** — `THREAD_INBOX_ENABLED=true`이면 대시보드에 지속적인 📬 수신함 섹션이 표시됨: 각 세션 종료 후, Claude가 가벼운 `claude -p` 호출로 최종 메시지를 분류(`waiting` / `done` / `ambiguous`)함; 답장을 기다리는 스레드는 봇 재시작 후에도 유지되며 응답할 때까지 표시됨

#### 🔌 입력 및 스킬
- **첨부 파일 지원** — 텍스트 파일이 프롬프트에 자동 추가됨(최대 5개 파일, 각 200 KB / 총 500 KB; 초과 파일은 건너뛰지 않고 알림과 함께 잘림); 이미지는 `--input-format stream-json`을 통해 Discord CDN URL로 전송(최대 4 × 5 MB); Discord가 (`content_type` 없이) 파일 첨부로 자동 변환하는 길게 붙여넣은 메시지는 확장자 기반 감지로 처리됨
- **주문형 파일 전달** — Claude에게 파일을 "보내줘" 또는 "첨부해줘"라고 요청하면 경로를 `.ccdb-attachments`에 기록함; 봇이 이를 읽어 세션 완료 시 파일을 Discord 첨부 파일로 전달함. 로컬 지침은 상당한 분량의 서면 산출물을 Markdown으로 저장하고 첨부하도록 요구할 수도 있음
- **스킬 실행** — 자동완성이 있는 `/skill` 명령, 선택적 인수, 스레드 내 재개; 설치된 플러그인의 스킬도 자동 검색됨
- **핫 리로드** — `~/.claude/skills/`에 추가된 새 스킬이 자동으로 감지됨(60초 갱신, 재시작 없음)

### 동시성 및 조율
- **Worktree 지침 자동 주입** — 모든 세션에 파일을 건드리기 전에 `git worktree`를 사용하도록 촉구
- **자동 worktree 정리** — 세션 worktree(`wt-{thread_id}`)가 세션 종료 및 봇 시작 시 자동으로 제거됨; 더티 worktree는 절대 자동 제거되지 않음(안전 불변식)
- **활성 세션 레지스트리** — 인메모리 레지스트리; 각 세션이 다른 세션의 동향을 파악함
- **AI Lounge** — 공유 "휴게실" 채널; 컨텍스트가 백엔드별 시스템/개발자 지침으로 주입됨(에페메랄, 기록에 절대 누적 안 됨)므로 장기 세션이 "Prompt is too long"에 절대 걸리지 않음; 세션이 의도를 게시하고, 서로의 상태를 읽고, 파괴적인 작업 전에 확인함; 사람은 이를 라이브 활동 피드로 봄
- **세션 간 관찰 가능성** — `GET /api/sessions`가 모든 세션(라이브 및 저장됨)을 상태, 작업 디렉토리, 최신 라운지 메모와 함께 나열함; `GET /api/threads/{thread_id}/messages`가 다른 스레드의 대화를 읽음. 읽기 전용이므로 세션이 편집하기 전에 살펴볼 수 있음 — 라운지에 전혀 게시하지 않은 세션도 포함
- **리소스 클레임** — `POST /api/claims`가 작업 시작 전에 저장소, 이슈 또는 파일을 예약함; 같은 리소스를 요청하는 두 번째 세션은 보유자의 스레드, 메모, 라이브 상태와 함께 409를 받음. 권고적이며 TTL 제한(기본 2시간, 최대 24시간)이므로 죽은 세션이 리소스를 영원히 붙잡을 수 없음
- **세션 간 릴레이** — `POST /api/threads/{thread_id}/message`가 이미 충돌한 세션끼리 대화하게 함; `queue`는 수신자의 턴을 기다리고, `interrupt`는 SIGINT를 보냄. 모든 릴레이는 스레드에 게시되고(뒷채널 아님), 인간으로 오인되지 않도록 마커로 감싸지며, 두 세션이 루프에 빠지지 않도록 홉/쿨다운/속도 제한으로 제한됨
- **자동 충돌 감지** — `CollisionWatchCog`가 라이브 세션이 실제로 쓴 파일(`Write`/`Edit`/`MultiEdit`/`NotebookEdit`에서 기록됨)을 1분에 한 번 비교함; 15분 이내에 같은 파일에 쓰는 두 세션은 AI Lounge와 두 스레드 모두에 알려짐. 아무도 알리지 않은 겹침을 잡아냄; 쌍당 30분에 한 번 경고하며, 실행 중인 턴을 절대 방해하지 않음
- **조율 채널** — `COORDINATION_CHANNEL_ID` env 변수가 AI Lounge 채널의 기본 폴백으로 사용됨(별도의 봇 측 라이프사이클 이벤트 없음)

### 예약 작업
- **SchedulerCog** — 30초 마스터 루프를 가진 SQLite 기반 주기적 작업 실행기
- **자기 등록** — Claude가 채팅 세션 중 `POST /api/tasks`로 작업 등록
- **코드 변경 불필요** — 런타임에 작업 추가, 제거, 수정
- **활성화/비활성화** — 삭제 없이 작업 일시 중지 (`PATCH /api/tasks/{id}`)

### CI/CD 자동화
- **Webhook 트리거** — GitHub Actions 또는 모든 CI/CD 시스템에서 Claude Code 작업 트리거
- **자동 업그레이드** — 업스트림 패키지 릴리스 시 봇 자동 업데이트
- **DrainAware 재시작** — 재시작 전 활성 세션 완료 대기
- **자동 재개 마킹** — 모든 종료 시 활성 세션이 재개용으로 자동 마킹됨(`AutoUpgradeCog`를 통한 업그레이드 재시작, 또는 `ClaudeChatCog.cog_unload()`를 통한 그 외 모든 종료); 재시작 시 Claude는 이전 상태를 보고하고 구현 작업을 재개하기 전에 사용자와 재확인함
- **재시작 승인** — 업그레이드를 확인하는 선택적 게이트; 업그레이드 스레드에서 ✅ 반응으로, 또는 부모 채널에 게시된 버튼으로 승인; 새 메시지가 도착하면 버튼이 하단에 다시 게시되어 계속 보이도록 유지됨
- **수동 업그레이드 트리거** — `/upgrade` 슬래시 명령으로 인가된 사용자가 Discord에서 직접 업그레이드 파이프라인을 트리거할 수 있음(`slash_command_enabled=True`로 옵트인)

### 세션 관리
- **내장 도움말** — `/help`가 사용 가능한 모든 슬래시 명령과 기본 사용법을 표시함(에페메랄, 호출자만 볼 수 있음)
- **세션 동기화** — CLI 세션을 Discord 스레드로 가져오기(`/sync-sessions`); `/sync-settings`로 동기화 환경설정(스레드 스타일, 시간 창, 최소 결과 수) 보기 또는 변경
- **세션 목록** — 출처(Discord / CLI / 전체)와 시간 창으로 필터링하는 `/sessions`
- **세션 재개** — `/resume`이 최근 세션(최대 25개)의 선택 메뉴를 표시하고 선택한 세션을 새 스레드에서 재개함; 키워드 검색을 위한 선택적 `query` 매개변수(요약과 작업 디렉토리 매칭); 삭제된 스레드의 세션만 표시하는 선택적 `filter=orphaned`; 모든 채널이나 스레드에서 작동 — 항상 설정된 메인 채널에 새 스레드를 생성
- **재개 정보** — `/resume-info`가 터미널에서 현재 세션을 계속하기 위한 CLI 명령을 표시함(스레드 전용)
- **세션 지우기** — `/clear`가 현재 스레드의 Claude Code 세션을 리셋하여 새 스레드를 생성하지 않고 새로 시작함
- **시작 재개** — 봇 재부팅 후 중단된 세션이 자동으로 재시작됨; `AutoUpgradeCog`(업그레이드 재시작)와 `ClaudeChatCog.cog_unload()`(그 외 모든 종료)가 자동으로 마킹하거나, `POST /api/mark-resume`를 수동으로 사용
- **프로그래밍 방식 스폰** — `POST /api/spawn`이 모든 스크립트나 Claude 서브프로세스에서 새 Discord 스레드 + Claude 세션을 생성함; 스레드 생성 직후 논블로킹 201을 즉시 반환
- **스레드 ID 주입** — `DISCORD_THREAD_ID` env 변수가 모든 Claude 서브프로세스에 전달되어, 세션이 `$CCDB_API_URL/api/spawn`을 통해 자식 세션을 스폰할 수 있게 함
- **StatusLine 표시** — Claude Code `settings.json`에 `statusLine`이 구성되어 있으면, 각 세션 응답 후 그 출력이 Discord에 표시됨
- **Worktree 관리** — `/worktree-list`가 clean/dirty 상태와 함께 모든 활성 세션 worktree를 표시함; `/worktree-cleanup`이 고아가 된 clean worktree를 제거함(`dry_run` 미리보기 지원)
- **런타임 모델 전환** — `/model-show`가 현재 전역 모델과 스레드별 세션 모델을 표시함; `/model-set`이 재시작 없이 모든 새 세션의 모델을 변경함
- **런타임 도구 권한** — `/tools-show`가 현재 허용된 도구를 표시함; `/tools-set`이 도구를 켜고 끄는 선택 메뉴를 엶; `/tools-reset`이 `.env` 기본값으로 되돌림 — 모두 재시작 없이
- **컨텍스트 사용량** — `/context`가 시각적 진행 막대와 함께 컨텍스트 창 백분율을 표시함; 83.5% 자동 압축 임계값에 가까워지면 ⚠️ 경고; 에페메랄(호출자만 볼 수 있음)
- **속도 제한 사용량** — `/usage`가 5시간 및 7일 창에 대한 백분율 막대와 리셋까지 남은 시간 카운트다운과 함께 Claude API 속도 제한 활용도를 표시함; 활용도 ≥ 80%일 때 ⚠️ 플래그
- **대화 되감기** — `/rewind`가 과거 사용자 턴의 선택 메뉴를 표시하고 선택한 지점에서 세션 JSONL을 잘라, 그 메시지와 그 이후의 모든 것을 제거하여 세션이 그 턴 직전의 정확한 상태에서 재개되게 함; Claude가 만든 모든 작업 파일은 유지됨; 세션이 궤도를 벗어났을 때 유용
- **대화 포크** — `/fork`가 `--fork-session`을 통해 동일한 세션 상태에서 계속되는 새 스레드로 현재 스레드를 분기하여, 진정으로 독립적인 세션 사본을 생성함; 원본에 영향을 주지 않고 다른 방향을 탐색할 수 있게 함

### 보안
- **쉘 주입 없음** — `asyncio.create_subprocess_exec`만 사용, `shell=True` 절대 없음
- **세션 ID 유효성 검사** — `--resume`에 전달하기 전 엄격한 정규식 검사
- **플래그 주입 방지** — 모든 프롬프트 앞에 `--` 구분자
- **시크릿 격리** — 봇 토큰을 서브프로세스 환경에서 제거
- **사용자 인증** — `allowed_user_ids`로 Claude를 호출할 수 있는 사용자를 제한
- **로그 주입 방지** — 로그에 기록하기 전에 사용자가 제공한 API 값을 정화함(개행 제거)

---

## 빠른 시작 — 5분 안에 Discord에서 Claude 또는 Codex 실행

**전제 조건:**

- Python 3.12+
- 다음 중 최소 하나:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — 설치 및 인증됨(`claude login`). Anthropic Pro/Max 구독자에게 권장.
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` 후 `codex login`. 기존 ChatGPT Plus/Pro/Business 구독을 사용.
- 둘 다 설치할 수 있습니다. `/backend`로 런타임에 둘 사이를 전환하세요([백엔드 전환](#백엔드-전환--온디맨드-claude--codex) 참조).

**플랫폼 지원:** 주로 **Linux**에서 개발 및 테스트됩니다. macOS와 Windows는 지원되고 CI를 통과하지만, 실제 테스트는 적습니다 — 버그 보고 환영.

### 1단계 — Discord 봇 생성 (일회성, 약 2분)

1. [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**으로 이동
2. **Bot**으로 이동 → Privileged Gateway Intents에서 **Message Content Intent** 활성화
3. 봇 **Token** 복사
4. **OAuth2 → URL Generator**로 이동: 범위 `bot` + `applications.commands`, 권한: Send Messages, Create Public Threads, Send Messages in Threads, Add Reactions, Manage Messages, Read Message History
5. 생성된 URL 열기 → 봇을 서버에 초대

### 2단계 — 설정 마법사 실행

클론이나 `.env` 편집 불필요 — 마법사가 모두 처리합니다:

```bash
# With uvx (no install needed):
uvx --from "git+https://github.com/ebibibi/ebi-agent-chat-relay.git" ccdb setup

# Or after cloning:
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv run ccdb setup
```

마법사는 다음을 수행합니다:
1. Discord API로 봇 토큰을 검증
2. **사용 가능한 채널을 자동으로 나열** — 번호만 고르면 됨(ID 복사 불필요)
3. 작업 디렉토리와 모델 선호를 질문
4. `.env`를 작성하고 봇을 즉시 시작할지 제안

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

### 시작 / 중지

```bash
ccdb start    # start the bot (reads .env in current dir)
ccdb start --env /path/to/.env   # custom .env location
```

설정된 채널에 메시지를 보내세요 — Claude가 새 스레드에서 응답합니다.

### systemd 서비스로 실행 (프로덕션)

프로덕션 배포의 경우, 부팅 시 시작되고 실패 시 자동 재시작되도록 봇을 systemd 아래에서 실행하세요.

저장소는 바로 적용 가능한 템플릿(`discord-bot.service`)과 사전 시작 스크립트(`scripts/pre-start.sh`)를 제공합니다. 이를 복사하여 커스터마이즈하세요:

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

**`scripts/pre-start.sh`가 하는 일**(봇 프로세스 전에 `ExecStartPre`로 실행됨):

1. **`git pull --ff-only`** — `origin main`에서 최신 코드를 가져옴
2. **`uv sync`** — 의존성을 `uv.lock`과 동기화 상태로 유지
3. **Import 검증** — `claude_discord.main`이 깔끔하게 import되는지 확인
4. **자동 롤백** — import가 실패하면 이전 커밋으로 되돌리고 재시도; 실패 또는 성공 시 Discord webhook 알림을 게시
5. **Worktree 정리** — 충돌한 세션이 남긴 오래된 git worktree를 제거

스크립트는 저장소 루트를 동적으로 감지하므로(`$0`에 대한 `readlink -f`를 통해), 사용자가 저장소를 어디에 클론했든 상관없이 모든 사용자에게 작동합니다 — 스크립트 자체에 경로 편집이 필요 없습니다. 또한 `PATH`에서 `uv` 바이너리를 자동으로 발견합니다; 필요하면 `CCDB_UV_BIN` env 변수로 재정의하세요.

스크립트는 실패 알림을 위해 `.env`에 `DISCORD_WEBHOOK_URL` 변수를 요구합니다(선택 사항 — 없어도 스크립트는 작동함).

#### 툴체인 PATH — `.env`에서 설정

systemd는 최소한의 기본 `PATH`(일반적으로 `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`)로 유닛을 시작하며, `~/.bashrc`나 `~/.profile`을 절대 소싱하지 않습니다. 봇은 그 `PATH`를 상속하고, 봇이 스폰하는 모든 Claude/Codex 세션도 마찬가지입니다 — 세션은 봇의 환경에서 제거된 시크릿을 뺀 상태로 실행됩니다.

그 결과는 혼란스럽습니다: 터미널에서 작동하는 빌드가 Discord 세션 안에서는 실패하거나, `~/.local/bin`이나 `~/.npm-global/bin` 아래에 설치된 도구가 서비스에 보이지 않기 때문에 조용히 더 오래된 시스템 전역 바이너리에 대해 실행됩니다.

서비스는 `EnvironmentFile=`을 통해 `.env`를 로드하므로, 거기에 `PATH`를 설정하면 봇과 모든 세션이 한 번에 고쳐집니다:

```bash
# .env — match your interactive shell's PATH
PATH=/home/you/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
```

서비스를 재시작(`sudo systemctl restart mybot.service`)한 다음, Discord 세션에서 Claude에게 `which node && node --version`을 실행하도록 요청하여 확인하세요.

### 커스텀 Cog (포크 없이 확장)

Python 파일을 디렉토리에 넣기만 하면 자신만의 기능을 추가할 수 있습니다 — 포크, 서브클래스, 패키지 불필요:

```bash
ccdb start --cogs-dir ./my-cogs/
# Or: CUSTOM_COGS_DIR=./my-cogs ccdb start
```

디렉토리의 각 `.py` 파일은 `async def setup(bot, runner, components)`를 노출해야 합니다:

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

`_`로 시작하는 파일은 건너뜁니다. 한 Cog가 로드에 실패해도 다른 Cog는 정상적으로 로드됩니다.

리마인더, Todoist 워치독, 자동 업그레이드, 문서 동기화가 포함된 완전한 실제 예시는 [`examples/ebibot/`](examples/ebibot/)를 참조하세요.

**`examples/ebibot/cogs/`의 내장 예시:**

| Cog | 목적 |
|-----|---------|
| `ReminderCog` | Discord 기반 리마인더 예약 |
| `WatchdogCog` | Todoist / 외부 서비스 워치독 |
| `AutoUpgradeCog` | Webhook 트리거 패키지 업그레이드 |
| `DocsSyncCog` | 푸시 시 자동 문서 동기화 |
| `AlertResponderCog` | 범용 알림 모니터링 — 모니터링 시스템의 알림을 Discord로 전달하고 Claude Code 조사 세션을 트리거 |

---

### 최소 봇 (패키지로 설치)

이미 discord.py 봇이 있다면, 대신 ccdb를 패키지로 추가하세요:

```bash
uv add git+https://github.com/ebibibi/ebi-agent-chat-relay.git
```

`bot.py` 생성:

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

`setup_bridge()`가 모든 Cog를 자동으로 연결합니다. 최신 버전으로 업데이트:

```bash
uv lock --upgrade-package claude-code-discord-bridge && uv sync
```

#### 멀티 채널 설정

봇을 여러 Discord 채널에 배포하려면, `claude_channel_id`에 더해(또는 대신) `claude_channel_ids`를 전달하세요:

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

각 채널은 완전히 독립적입니다 — 설정된 채널 중 어느 곳에서든 메시지를 보내면 새 Claude 세션 스레드가 스폰되며, `/skill` 명령은 모든 채널에서 작동합니다. `claude_channel_id`는 이전 버전과의 호환성을 위해 유지되며, `/skill` 명령이 설정된 채널 밖에서 호출될 때 폴백 스레드 생성 대상으로 사용됩니다.

#### 멘션 전용 채널

특정 채널에서 봇이 **@멘션될 때만** 응답하도록 만들려면(봇이 모든 메시지에 반응하는 것을 원치 않는 공유 채널에 유용):

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 222},
    mention_only_channel_ids={222},  # bot ignores messages in #222 unless @mentioned
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

또는 환경 변수를 통해(쉼표로 구분된 채널 ID):

```
MENTION_ONLY_CHANNEL_IDS=222,333
```

스레드는 **부모 채널의 정책을 상속합니다**. 사람이 멘션 전용 채널에 만든 스레드는 Claude 세션을 시작하지 않습니다 — 그렇지 않으면 누구나 스레드를 여는 것만으로 설정을 우회할 수 있기 때문입니다. Claude는 다음의 경우에만 그런 스레드에 참여합니다:

- 봇이 메시지에서 명시적으로 **@멘션**되거나,
- ccdb가 **이미 그 스레드를 소유**하는 경우 — 봇이 생성한 세션 스레드, 또는 `/api/spawn`을 통해 생성된 스레드. 세션이 존재하면 멘션 없이도 모든 답장이 정상적으로 처리됩니다.

`mention_only_channel_ids`에 나열되지 *않은* 채널 아래의 스레드는 영향을 받지 않으며 항상 처리됩니다.

#### 인라인 응답 채널

특정 채널에서 봇이 (스레드를 생성하지 않고) **채널에 직접** 응답하도록 만들려면(스레드가 불필요한 잡동사니를 더하는 개인 명령 채널에 유용):

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 333},
    inline_reply_channel_ids={333},  # bot replies inline in #333, no thread created
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

또는 환경 변수를 통해(쉼표로 구분된 채널 ID):

```
INLINE_REPLY_CHANNEL_IDS=333,444
```

인라인 응답 모드에서는 Claude의 응답이 새 스레드를 스폰하는 대신 채널에 메시지로 직접 전송됩니다. 세션은 여전히 내부적으로 추적되므로, 채널의 후속 메시지는 동일한 Claude 세션을 계속합니다.

#### 채팅 전용 채널

특정 채널에서 기술적 UI(도구 embed, 사고 블록, 세션 시작/완료 알림, 할 일 목록)를 숨기고 **Claude의 텍스트 응답만** 표시하려면 — 비기술 사용자가 지켜보는 공개용 채널에 유용:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 444},
    chat_only_channel_ids={444},  # only text shown in #444; tool details hidden
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

또는 환경 변수를 통해(쉼표로 구분된 채널 ID):

```
CHAT_ONLY_CHANNEL_IDS=444,555
```

채팅 전용 모드에서는 권한 요청과 `AskUserQuestion` 프롬프트가 설정과 무관하게 **항상 표시됩니다** — 이들은 사람의 입력이 필요하며 반드시 보여야 합니다.

---

## 설정

| 변수 | 설명 | 기본값 |
|----------|-------------|---------|
| `DISCORD_BOT_TOKEN` | Discord 봇 토큰 | (필수) |
| `DISCORD_CHANNEL_ID` | Claude 채팅 채널 ID | (필수) |
| `CCDB_BACKEND` | 사용할 CLI 백엔드: `claude`(Claude Code CLI) 또는 `codex`(OpenAI Codex CLI) | `claude` |
| `CCDB_COMMAND` | CLI 바이너리의 경로 또는 이름(`CLAUDE_COMMAND` 재정의). `CCDB_BACKEND`에서 선택된 초기 러너가 사용; 런타임에 `/backend`가 전환하면 아래 두 백엔드별 변수로 대체됨. | _(자동: `claude` 또는 `codex`)_ |
| `CCDB_CLAUDE_COMMAND` | Claude CLI 바이너리에 대한 명시적 경로. 초기 `CCDB_BACKEND`와 무관하게 `/backend claude`가 활성일 때마다 `BackendFactory`가 사용. `CLAUDE_COMMAND`, 그다음 `claude`(PATH)로 폴백. | (선택) |
| `CCDB_CODEX_COMMAND` | OpenAI Codex CLI 바이너리에 대한 명시적 경로. 봇을 systemd 아래에서 실행할 때 필요(기본 서비스 PATH에는 `~/.npm-global/bin`이 포함되지 않음). `codex`(PATH)로 폴백. | (선택) |
| `PATH` | 봇 **및 봇이 스폰하는 모든 CLI 세션**을 위한 바이너리 검색 경로 — 세션은 봇의 환경을 상속함. 최소 PATH로 유닛을 시작하고 `~/.bashrc` / `~/.profile`을 절대 읽지 않는 systemd 아래에서 실행할 때 `.env`에 설정. [툴체인 PATH](#툴체인-path--env에서-설정) 참조. | (부모 프로세스에서 상속됨) |
| `CCDB_MODEL` | 사용할 모델(`CLAUDE_MODEL` 재정의) | `sonnet` |
| `CCDB_PERMISSION_MODE` | CLI 권한 모드(`CLAUDE_PERMISSION_MODE` 재정의) | `acceptEdits` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | 모든 권한 검사 건너뛰기 — `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` 재정의 | `false` |
| `CCDB_WORKING_DIR` | CLI 작업 디렉토리(`CLAUDE_WORKING_DIR` 재정의) | 현재 디렉토리 |
| `CCDB_ALLOWED_TOOLS` | 허용 도구의 쉼표 구분 목록(`CLAUDE_ALLOWED_TOOLS` 재정의) | (선택) |
| `CCDB_CHANNEL_IDS` | 추가 채널 ID, 쉼표 구분(`CLAUDE_CHANNEL_IDS` 재정의) | (선택) |
| `CLAUDE_COMMAND` | Claude CLI 바이너리의 경로 또는 이름(구버전 이름 — `CCDB_COMMAND` 권장). 특정 버전을 고정하는 데 사용(예: `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`) — 새 CLI 릴리스의 회귀를 피하는 데 유용. | `claude` |
| `CLAUDE_MODEL` | 사용할 모델(구버전 — `CCDB_MODEL` 권장) | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | CLI 권한 모드(구버전 — `CCDB_PERMISSION_MODE` 권장) | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | 모든 권한 검사 건너뛰기(구버전 — `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` 권장) | `false` |
| `CLAUDE_WORKING_DIR` | Claude 작업 디렉토리(구버전 — `CCDB_WORKING_DIR` 권장) | 현재 디렉토리 |
| `MAX_CONCURRENT_SESSIONS` | 모든 코드 경로(채팅, 스킬, 스케줄러, webhook)에 걸친 최대 병렬 Claude CLI 세션 수 | `3` |
| `SESSION_TIMEOUT_SECONDS` | 세션 비활성 타임아웃 | `300` |
| `DISCORD_OWNER_ID` | Claude가 입력이 필요할 때 @멘션할 사용자 ID | (선택) |
| `COORDINATION_CHANNEL_ID` | AI Lounge 채널의 기본 폴백으로 사용되는 채널 ID | (선택) |
| `MENTION_ONLY_CHANNEL_IDS` | 봇이 @멘션될 때만 응답하는 채널 ID(쉼표 구분, 그 아래 스레드가 정책 상속) | (선택) |
| `INLINE_REPLY_CHANNEL_IDS` | 봇이 인라인으로 응답하는 채널 ID(쉼표 구분, 스레드 미생성) | (선택) |
| `CHAT_ONLY_CHANNEL_IDS` | 채팅 전용 모드 채널 ID(쉼표 구분) — Claude의 텍스트 응답만 표시되고 모든 기술 embed(도구, 사고, 세션 정보, 할 일)는 숨겨짐 | (선택) |
| `WORKTREE_BASE_DIR` | 세션 worktree를 스캔할 기본 디렉토리(자동 정리 활성화) | (선택) |
| `CLI_SESSIONS_PATH` | CLI 세션 검색을 위한 `~/.claude/projects` 경로(`/sync-sessions` 활성화) | (선택) |
| `CUSTOM_COGS_DIR` | 시작 시 로드할 커스텀 Cog 파일이 포함된 디렉토리([커스텀 Cog](#커스텀-cog-포크-없이-확장) 참조) | (선택) |
| `CLAUDE_ALLOWED_TOOLS` | Claude CLI를 위한 허용 도구의 쉼표 구분 목록(구버전 — `CCDB_ALLOWED_TOOLS` 권장) | (선택) |
| `CLAUDE_CHANNEL_IDS` | 멀티 채널 설정을 위한 추가 채널 ID(쉼표 구분)(구버전 — `CCDB_CHANNEL_IDS` 권장) | (선택) |
| `THREAD_INBOX_ENABLED` | 지속적인 스레드 수신함 활성화(`claude -p`를 통해 세션을 `waiting`/`done`/`ambiguous`로 분류; 스레드 대시보드에 표시) | `false` |
| `THREAD_AUTO_RENAME` | Claude AI로 새 스레드 제목 자동 이름 변경 — 백그라운드 `claude -p` 호출을 통해 첫 사용자 메시지에서 짧고 설명적인 제목을 생성(세션 시작을 절대 지연시키지 않음) | `false` |
| `CCDB_CLI_ENV_FILE` | 매 호출마다 변수를 CLI 서브프로세스 환경에 병합하는 `KEY=VALUE` 파일 경로. 봇을 재시작하지 않고 즉시 적용됨. 임시 API 라우팅(예: Azure Foundry)에 유용 | (선택) |
| `CCDB_LOG_FILE` | 로그 파일 경로. 설정 시 기본 stdout 핸들러에 더해 순환 파일 핸들러(10 MB × 5 백업)가 추가됨. 모니터링 및 알림에 유용. | (선택) |
| `API_HOST` | REST API 바인드 주소 | `127.0.0.1` |
| `API_PORT` | REST API 포트(설정 시 REST API 활성화) | (선택) |
| `CCDB_INGEST_TOKEN` | `POST /api/ingest`용 Bearer 토큰(`api_secret`과 독립); 미설정 시 이 엔드포인트는 `503`을 반환 | (선택) |
| `CCDB_INGEST_REQUIRE_COMPLETE` | `1`로 설정하면 `attachments_manifest`가 첨부 파일 누락을 입증할 때, 불완전한 근거로 세션을 시작하는 대신 `409`로 인제스트를 거부 | `0` |

### 권한 모드 — `-p` 모드에서 작동하는 것

Claude Code CLI는 ccdb를 통해 사용될 때 **`-p`(비대화형) 모드**로 실행됩니다. 이 모드에서 CLI는 **권한을 요청할 수 없습니다** — 승인이 필요한 도구는 즉시 거부됩니다. 이는 ccdb의 제한이 아니라 [CLI 설계 제약](https://code.claude.com/docs/en/headless)입니다.

| 모드 | `-p` 모드에서의 동작 | 권장 사항 |
|------|----------------------|----------------|
| `default` | ❌ **모든 도구 거부됨** — 사용 불가 | 사용하지 마세요 |
| `acceptEdits` | ⚠️ Edit/Write 자동 승인, Bash 거부됨(Claude가 파일 작업에 Write로 폴백) | 최소한의 실행 가능 옵션 |
| `bypassPermissions` | ✅ 모든 도구 승인됨 | 작동하지만 아래 플래그를 권장 |
| **`auto`** | ✅ **AI 분류 안전성** — 안전한 작업은 자동 승인, 위험한 작업은 차단 | **권장** — 안전성과 사용성의 최적 균형 |
| `plan` | ✅ AI 분류(읽기 전용 편향) — auto와 유사하지만 더 보수적 | 읽기 중심 워크플로우용 |
| **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`** | ✅ **모든 도구 승인됨, 안전성 검사 없음** | 레거시 "yolo" 모드 — auto 모드가 너무 제한적일 때 사용 |

**우리의 권장 사항:** `CLAUDE_PERMISSION_MODE=auto`를 설정하세요. Auto 모드는 AI 분류기를 사용하여 안전한 작업(파일 편집, 로컬 테스트, 작업 브랜치로 git push)은 자동으로 승인하면서 위험한 작업(강제 푸시, 프로덕션 배포, 자격 증명 유출)은 차단합니다. 이는 yolo 모드의 "무엇이든 허용" 위험 없이 정상적인 개발 작업에 대해 Claude에게 완전한 자율성을 부여합니다.

**yolo 모드로 폴백:** auto 모드가 필요한 작업을 차단하면, 대신 `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`를 설정하세요. ccdb가 `allowed_user_ids`를 통해 누가 Claude와 상호작용할 수 있는지 제어하므로, CLI 수준의 권한 검사는 의미 있는 보안 이점 없이 마찰만 더합니다. 이름의 "dangerously"는 CLI의 범용 경고를 반영합니다; 접근이 이미 게이트된 ccdb 컨텍스트에서는 실용적인 선택입니다.

> **참고:** `CLAUDE_PERMISSION_MODE`가 `auto` 또는 `plan`으로 설정되면, `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS`는 자동으로 무시됩니다 — 이 모드들은 yolo 플래그로 재정의될 자체 안전성 분류기를 가지고 있습니다.

**세밀한 제어를 위해서는**, `CLAUDE_ALLOWED_TOOLS`를 사용하여 권한을 완전히 우회하지 않고 특정 도구를 허용하세요:

```env
# Example: allow file operations and code execution, but not web access
CLAUDE_ALLOWED_TOOLS=Bash,Read,Write,Edit,Glob,Grep

# Example: read-only mode — Claude can explore but not modify
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
```

일반적인 도구 이름: `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`. 이것을 사용할 때는 `CLAUDE_PERMISSION_MODE=default`를 설정하세요(다른 모드가 재정의할 수 있음).

**Discord를 통한 런타임 변경:** `/tools-set`을 사용하여 봇을 재시작하지 않고 런타임에 허용 도구를 변경하세요. 설정은 저장되며 모든 새 세션에 즉시 적용됩니다. 현재 구성을 보려면 `/tools-show`를, `.env` 기본값으로 되돌리려면 `/tools-reset`을 사용하세요.

> **Discord의 권한 버튼:** `CLAUDE_PERMISSION_MODE=default`이면 Claude가 `permission_request` 이벤트를 발생시키고 ccdb가 스레드에 허용/거부 버튼을 표시합니다. stdin은 항상 열려 있으므로(stream-json 입력 모드) 봇이 Claude에 응답을 되보낼 수 있습니다. `auto` 또는 `plan` 모드를 사용 중이라면, Claude가 사용자 상호작용 없이 권한을 자동으로 처리합니다. `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`(yolo 모드)이면 ccdb가 모든 `permission_request` 이벤트를 즉시 **자동 승인**합니다 — 허용/거부 버튼이 표시되지 않습니다. 이는 `--dangerously-skip-permissions`가 파일 수준 민감 경로 검사를 우회하지 못하는 CLI 회귀(v2.1.78+, 업스트림 [#35895](https://github.com/anthropics/claude-code/issues/35895))에 대한 우회책입니다.

---

## Discord 봇 설정

1. [Discord Developer Portal](https://discord.com/developers/applications)에서 새 애플리케이션 생성
2. 봇을 생성하고 토큰 복사
3. Privileged Gateway Intents에서 **Message Content Intent** 활성화
4. 다음 권한으로 봇 초대:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Manage Messages (반응 정리용)
   - Read Message History

---

## GitHub + Claude Code 자동화

### 예시: 자동 문서 동기화

`main`에 푸시할 때마다 Claude Code는:
1. 최신 변경사항을 가져오고 diff를 분석
2. 영문 문서를 업데이트
3. 일본어(또는 임의의 대상 언어)로 번역
4. 이중 언어 요약이 있는 PR 생성
5. 자동 머지 활성화 — CI가 통과하면 자동으로 머지

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

**봇 구성:**

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

**보안:** 프롬프트는 서버 측에서 정의됩니다. Webhook은 어떤 트리거를 발동할지 선택만 합니다 — 임의의 프롬프트 주입 없음.

### 예시: 소유자 PR 자동 승인

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

## 예약 작업

코드 변경, 재배포 없이 런타임에 주기적인 Claude Code 작업을 등록하세요.

Discord 세션 안에서 Claude가 작업을 등록할 수 있습니다:

```bash
# Claude calls this inside a session:
curl -X POST "$CCDB_API_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Check for outdated deps and open an issue if found", "interval_seconds": 604800}'
```

또는 자체 스크립트에서 등록:

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Weekly security scan", "interval_seconds": 604800}'
```

30초 마스터 루프가 기한이 된 작업을 집어 들어 Claude Code 세션을 자동으로 스폰합니다.

---

## 자동 업그레이드

새 릴리스가 게시되면 봇을 자동으로 업그레이드합니다:

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

#### `/upgrade`를 통한 수동 트리거

`slash_command_enabled=True`이면, 인가된 사용자는 누구나 Discord에서 직접 `/upgrade`를 실행하여 동일한 업그레이드 파이프라인을 트리거할 수 있습니다 — webhook이 필요 없습니다. 이 명령은 텍스트 채널과 스레드 모두에서 작동합니다(스레드 안에서 실행하면 부모 채널에 업그레이드 스레드를 생성함). `upgrade_approval`과 `restart_approval` 게이트를 준수하고, 진행 스레드를 생성하며, 동시 실행을 우아하게 처리합니다(이미 업그레이드가 진행 중이면 에페메랄로 응답).

재시작 전에 `AutoUpgradeCog`는:

1. **활성 세션 스냅샷** — 실행 중인 Claude 세션이 있는 모든 스레드를 수집(덕 타이핑: `_active_runners` 딕셔너리를 가진 모든 Cog가 자동으로 발견됨).
2. **드레인(Drain)** — 활성 세션이 자연스럽게 끝날 때까지 대기.
3. **재개용 마킹** — 활성 스레드 ID를 pending-resumes 테이블에 저장. 다음 시작 시, 그 세션들은 안전 우선 프롬프트로 재개됩니다: Claude는 작업 중이던 내용을 보고하고, 구현 작업(코드 변경, 커밋, PR)을 재개하기 전에 사용자에게 재확인을 요청합니다. 이는 컨텍스트 압축이 작업 승인 상태를 지웠을 수 있는 후 의도치 않은 동작을 방지합니다.
4. **재시작** — 구성된 재시작 명령을 실행.

`active_count` 속성을 가진 모든 Cog가 자동으로 발견되어 드레인됩니다:

```python
class MyCog(commands.Cog):
    @property
    def active_count(self) -> int:
        return len(self._running_tasks)
```

세션 마킹은 완전히 옵트인입니다 — `setup_bridge()`가 세션 데이터베이스를 초기화한 경우(기본값)에만 활성화됩니다. 활성화되면, 세션은 `--resume` 연속성으로 재개되므로 Claude Code가 중단된 지점에서 정확히 대화를 이어갈 수 있습니다.

> **커버리지:** `AutoUpgradeCog`는 업그레이드로 트리거된 재시작을 커버합니다. *그 외 모든* 종료(`systemctl stop`, `bot.close()`, SIGTERM)의 경우, `ClaudeChatCog.cog_unload()`가 두 번째 자동 안전망을 제공합니다.

---

## REST API

알림 및 작업 관리를 위한 선택적 REST API. aiohttp가 필요합니다:

```bash
uv add "claude-code-discord-bridge[api]"
```

### 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|-------------|
| GET | `/api/health` | 헬스 체크 |
| POST | `/api/notify` | 즉시 알림 전송 |
| POST | `/api/schedule` | 알림 예약 |
| GET | `/api/scheduled` | 대기 중인 알림 목록 |
| DELETE | `/api/scheduled/{id}` | 알림 취소 |
| POST | `/api/tasks` | 예약된 Claude Code 작업 등록 |
| GET | `/api/tasks` | 등록된 작업 목록 |
| DELETE | `/api/tasks/{id}` | 작업 삭제 |
| PATCH | `/api/tasks/{id}` | 작업 업데이트(활성화/비활성화, 일정 변경) |
| POST | `/api/spawn` | 새 Discord 스레드 생성 및 Claude Code 세션 시작(논블로킹); `auto_start: false`를 전달하면 첫 사용자 답장까지 Claude를 지연 |
| POST | `/api/ingest` | base64 첨부 파일이 있는 인증된 외부 스폰(브라우저 확장 / webhook); 결과 조회가 구성된 경우 `result_id` 반환 |
| GET | `/api/ingest/{result_id}` | 스폰된 세션의 최종 답변 폴링(`status`/`result`/`error`/`thread_id`) |
| POST | `/api/mark-resume` | 다음 봇 시작 시 자동 재개를 위해 스레드 마킹 |
| GET | `/api/lounge` | 최근 AI Lounge 메시지 읽기 |
| POST | `/api/lounge` | AI Lounge에 메시지 게시(선택적 `label` 포함) |
| GET | `/api/sessions` | 모든 세션(라이브 및 저장됨)을 상태, 작업 디렉토리, 최신 라운지 메모와 함께 나열(`state=running`, `exclude_thread`, `limit`) |
| GET | `/api/threads/{thread_id}/messages` | 다른 스레드의 대화를 오래된 것부터 읽기(`limit`) |
| POST | `/api/claims` | 작업 전에 리소스 클레임 — 획득 시 201, 이미 점유된 경우 보유자와 함께 409 |
| GET | `/api/claims` | 라이브 클레임 목록(선택적 `resource` 필터) |
| DELETE | `/api/claims` | 클레임 해제(`resource`, `thread_id`, 선택적 `force=true`) |
| POST | `/api/threads/{thread_id}/message` | 한 세션에서 다른 세션으로 메시지 릴레이(`text`, `from_thread`, `mode`, `hop`) |

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

## 아키텍처

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
    ingest_manifest.py     # attachments_manifest와 실제 전달된 파일의 대조
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

### 설계 철학

- **CLI 스폰, API 아님** — `claude -p --output-format stream-json`을 호출하여, 재구현 없이 완전한 Claude Code 기능(CLAUDE.md, 스킬, 도구, 메모리)을 제공. Claude Pro/Max 구독으로 실행 — API 키 없음, 토큰당 요금 없음
- **동시성 우선** — 여러 동시 세션이 엣지 케이스가 아니라 예상 사례; 모든 세션이 worktree 지침을 받고, 레지스트리와 AI Lounge가 나머지를 처리
- **Discord를 접착제로** — Discord가 UI, 스레딩, 반응, webhook, 지속적인 알림을 제공; 커스텀 프론트엔드 불필요
- **프레임워크, 애플리케이션 아님** — 패키지로 설치, 기존 봇에 Cog 추가, 코드로 구성
- **코드 없는 확장성** — 소스를 건드리지 않고 예약 작업 및 webhook 트리거 추가
- **단순함으로 보안** — 약 8000줄의 감사 가능한 Python; subprocess exec만, 쉘 확장 없음

---

## 테스트

```bash
uv run pytest tests/ -v --cov=claude_discord
```

파서, 청커, 저장소, 러너, 스트리밍, webhook 트리거, 자동 업그레이드(`/upgrade` 슬래시 명령, 스레드 호출, 승인 버튼 포함), REST API, AskUserQuestion UI, 스레드 대시보드, 예약 작업, 세션 동기화, AI Lounge, 세션 간 관찰 가능성, 리소스 클레임, 세션 간 릴레이, 시작 재개, 모델 전환, 압축 감지, TodoWrite 진행 embed, 커스텀 Cog 로더, 권한/elicitation/계획 모드 이벤트 파싱, 스레드 수신함 분류, 스레드별 락 동작, SessionBackend 프로토콜, CodexRunner, 백엔드 팩토리, 백엔드 간 세션 소유권을 커버하는 1690+ 테스트.

---

## 이 프로젝트의 구축 방법

**이 코드베이스는 Anthropic의 AI 코딩 에이전트인 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)가** [@ebibibi](https://github.com/ebibibi)의 지도 하에 개발합니다. 인간 저자는 요구사항을 정의하고, 풀 리퀘스트를 리뷰하고, 모든 변경사항을 승인합니다 — Claude Code가 구현을 담당합니다.

이것이 의미하는 바는:

- **구현이 AI 생성됨** — 아키텍처, 코드, 테스트, 문서
- **인간 리뷰가 PR 수준에서 적용됨** — 모든 변경은 머지 전에 GitHub 풀 리퀘스트와 CI를 거침
- **버그 보고와 PR 환영** — Claude Code가 이를 처리하는 데 사용됨
- **이것은 인간이 지시하고 AI가 구현하는 오픈소스 소프트웨어의 실제 예시**

프로젝트는 2026-02-18에 시작되었으며, Claude Code와의 반복적인 대화를 통해 계속 발전하고 있습니다.

---

## 실제 예시

**[`examples/ebibot/`](examples/ebibot/)** — 이 프레임워크 위에 구축되어 바로 이 저장소에 포함된 개인 Discord 봇. 커스텀 Cog 로더를 다음과 함께 시연합니다:

- **ReminderCog** — `/remind HH:MM "message"` 슬래시 명령 + 30초 전송 루프
- **WatchdogCog** — Todoist 기한 초과 작업 모니터(30분 확인, 일일 중복 제거, 심각도 기반 경보)
- **AutoUpgradeCog** — GitHub webhook + systemctl restart를 통한 자가 업데이트
- **DocsSyncCog** — 푸시 시 webhook을 통한 자동 문서 번역
- **AlertResponderCog** — 범용 알림 모니터링 Cog; 구성 가능한 소스를 감시하고 심각도가 주석된 알림을 Discord에 게시

실행: `ccdb start --cogs-dir examples/ebibot/cogs/`

> EbiBot 커스텀 Cog는 이전에 [별도 저장소](https://github.com/ebibibi/discord-bot)에서 관리되었습니다. 이제 Claude Code가 프레임워크와 커스터마이징 양쪽의 전체 컨텍스트를 항상 가지도록 여기에 함께 배치되어 있습니다 — 실수로 인한 기능 중복을 방지합니다.

---

## 영감을 받은 프로젝트

- [OpenClaw](https://github.com/openclaw/openclaw) — 이모지 상태 반응, 메시지 디바운싱, fence 인식 청킹
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — CLI 스폰 + stream-json 접근 방식
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — 권한 제어 패턴
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — 스레드별 대화 모델

---

## 라이선스

MIT
