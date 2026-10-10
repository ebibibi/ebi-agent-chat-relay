# Set up Microsoft Teams end to end

This guide takes a new deployment from an empty Microsoft tenant to a real
**Teams → agent → Teams** round trip. It uses the recommended split architecture: a small public
receiver accepts Microsoft Bot Framework traffic, while the machine that runs Claude Code, Codex,
local models, or AG-UI remains private and polls outbound.

## The pieces you are creating

```text
Teams user
   │
   ▼
Microsoft Teams / Bot Framework
   │ HTTPS: signed Activity
   ▼
public receiver                     Azure Container Apps is one option
   │ verify token, enqueue only     no bot client secret, cannot run an agent
   ▼
Azure Storage Queue
   ▲
   │ outbound poll
private session host                ccdb, CLI logins, repositories, bot secret
   │ outbound HTTPS
   ├──────────────▶ selected backend: Claude / Codex / Local / AG-UI
   └──────────────▶ Bot Connector ──▶ Teams user
```

The public receiver and private host use different queue credentials. The receiver needs only the
ability to add messages. The private host needs to read, update visibility, and delete them.

## Prerequisites

- Permission to create an Entra application and an Azure Bot resource.
- Permission to upload or publish a custom Teams app and grant its requested consent.
- An Azure subscription with a Storage account and an HTTPS container host, or equivalent services.
- A private machine that can run Python 3.12 or 3.13 and the desired agent backend.
- A public DNS host such as `relay.example.com` with a valid TLS certificate.

Microsoft portal labels change over time. The invariant values are the **application (client) ID**,
**directory (tenant) ID**, **client credential**, and **messaging endpoint**.

## 1. Create the Entra application

In Microsoft Entra admin center:

1. Open **App registrations → New registration**.
2. Choose a stable operator-facing name such as “Ebi Agent Chat Relay”.
3. Choose the supported account type that matches the deployment. A single-customer deployment
   normally uses “Accounts in this organizational directory only.” A SaaS deployment requires a
   separate multi-tenant design and customer consent process.
4. Record the **Application (client) ID** as `CCDB_TEAMS_APP_ID`.
5. Record the **Directory (tenant) ID** as `CCDB_TEAMS_TENANT_ID`.
6. Under **Certificates & secrets**, create a credential for the private session host. Store the
   secret *value* immediately in a secret manager; the portal will not show it again.

Do not put the secret in the Teams app package, container image, Git repository, or public receiver.
The current launcher authenticates with `CCDB_TEAMS_APP_PASSWORD`; rotate it before expiration.

## 2. Create the Azure Bot

Create an **Azure Bot** resource and associate it with the application ID from step 1. Select the
same tenant/account model as the Entra registration, then add the **Microsoft Teams** channel.

The messaging endpoint will be:

```text
https://relay.example.com/api/teams/messages
```

You can enter it now or after the receiver is deployed. The host and path must match
`CCDB_TEAMS_PUBLIC_HOST` and `CCDB_TEAMS_ENDPOINT_PATH` exactly.

## 3. Create the Azure Storage Queue

Create a Storage account and a queue dedicated to this deployment, for example `agent-activities`.
Do not share one queue across customers: the queue and session database are isolation boundaries.

Generate two short-lived, HTTPS-only SAS URLs:

| Consumer | Minimum queue permissions | Where it is stored |
|---|---|---|
| public receiver | add | receiver secret/environment |
| private session host | read, process/update, delete | private host secret/environment |

Both processes call their own credential `CCDB_TEAMS_QUEUE_URL`. A SAS URL contains a secret in its
query string; never paste the real value into logs, screenshots, an Issue, or a manifest. Prefer a
managed rotation process and narrow network access where practical.

## 4. Deploy the public receiver

Build the included minimal image:

```bash
docker build -f deploy/teams-relay/Dockerfile -t your-registry.example/agent-relay:v4 .
docker push your-registry.example/agent-relay:v4
```

Run it on Azure Container Apps or another HTTPS container service with:

```dotenv
CCDB_TEAMS_APP_ID=00000000-0000-0000-0000-000000000000
CCDB_TEAMS_TENANT_ID=00000000-0000-0000-0000-000000000000
CCDB_TEAMS_PUBLIC_HOST=relay.example.com
CCDB_TEAMS_QUEUE_URL=https://account.queue.core.windows.net/agent-activities?RECEIVER_SAS
```

The container command is:

```bash
python -m claude_teams relay --host 0.0.0.0 --port 8080
```

Expose port 8080 through the platform's TLS ingress and map the custom DNS name. Keep at least one
warm replica: a cold start can exceed the short response budget for Teams card invokes. Configure
health checks against `/healthz`.

The receiver must **not** have `CCDB_TEAMS_APP_PASSWORD`, repository access, or agent credentials.
It verifies the inbound Bot Framework token and writes a bounded envelope to the queue. It cannot
reply as the bot or start a shell session.

Before continuing, verify:

```bash
curl --fail https://relay.example.com/healthz
```

Then set the Azure Bot messaging endpoint to
`https://relay.example.com/api/teams/messages`.

## 5. Configure the private session host

On a checkout of the repository, install the Teams extra and at least one backend:

```bash
uv sync --extra teams
claude --version   # or: codex --version
```

Add these values to the same protected environment file that starts the normal EbiBot process:

```dotenv
# Keep Discord active and add Teams in the same process.
CCDB_FRONTENDS=discord,teams

CCDB_TEAMS_APP_ID=00000000-0000-0000-0000-000000000000
CCDB_TEAMS_TENANT_ID=00000000-0000-0000-0000-000000000000
CCDB_TEAMS_APP_PASSWORD=replace-with-the-client-secret
CCDB_TEAMS_PUBLIC_HOST=relay.example.com
CCDB_TEAMS_QUEUE_URL=https://account.queue.core.windows.net/agent-activities?PRIVATE_HOST_SAS

# Existing Discord configuration remains unchanged.
DISCORD_BOT_TOKEN=replace-with-the-discord-token
```

Start the normal launcher:

```bash
uv run ccdb start
```

At startup, one process keeps the Discord gateway connection open and starts the private
`ActivityPuller`. The puller performs outbound queue requests; no Teams listener is opened on this
machine. `CCDB_FRONTENDS` defaults to `discord`, so omitting the setting preserves a Discord-only
deployment.

If Teams is the only frontend, `CCDB_FRONTENDS=teams` runs without logging in to Discord and
without Discord credentials. Scheduled tasks that open a *new* conversation need a frontend that
can create one; add the Relay Console (`CCDB_FRONTENDS=console,teams`, see
[console.md](console.md#run-without-discord)) for that. Running Discord and Teams together is the
most exercised production path.

## 6. Generate the Teams app package

Use the same IDs and public host, but no secret is required:

```bash
export CCDB_TEAMS_APP_ID=00000000-0000-0000-0000-000000000000
export CCDB_TEAMS_TENANT_ID=00000000-0000-0000-0000-000000000000
export CCDB_TEAMS_PUBLIC_HOST=relay.example.com

python -m claude_teams manifest \
  --out dist/teams-app.zip \
  --color-icon assets/color.png \
  --outline-icon assets/outline.png
```

`CCDB_TEAMS_PUBLIC_HOST` is a bare DNS host: no `https://`, port, or path. The command prints the
messaging endpoint; compare it character-for-character with the Azure Bot setting.

The generated zip includes the manifest and required icons. It declares:

- the Entra application ID as the bot identity;
- `ChannelMessage.Read.Group` resource-specific consent, granted by a team owner on installation;
- `webApplicationInfo`, so future SSO work does not unexpectedly change the consent shape; and
- the exact public host in `validDomains`.

## 7. Upload and consent in Teams

Upload `dist/teams-app.zip` through **Manage your apps → Upload an app** or publish it through the
organization's Teams admin process. Install it first in a personal chat, then in a test team.

In channels, mention the bot on the first message. The resource-specific consent in the manifest
allows the installed app to receive subsequent channel messages according to the tenant's policy.

The generated package includes the command-list scaffold used by the standalone Teams surface.
The normal private queue integration in v4 does not yet dispatch those text commands: slash-looking
text is passed to the selected agent like any other prompt. Configure `CCDB_BACKEND` on the private
host (or use the Discord global administration command when both frontends run together).

## 8. Validate in three stages

1. **Public edge:** `/healthz` returns success and the Azure Bot endpoint matches the generated URL.
2. **Transport:** receiver logs show an accepted activity and private-host logs show the
   `ActivityPuller` consuming it. No secret or SAS URL should appear in either log.
3. **Agent round trip:** send a harmless prompt such as “Reply with exactly `teams-v4-ok`.” Confirm
   the answer appears in the same Teams conversation, then send a Discord prompt and confirm both
   frontends remain live together.

After that, restart with the backend you intend to use and repeat the harmless prompt. AG-UI
requires `CCDB_BACKEND=agui`, `CCDB_AGUI_URL`, and the optional `CCDB_AGUI_TOKEN`; see
[Choose an agent backend](backends.md).

## Security and tenancy checklist

- The public receiver holds no bot client secret and no agent/repository credentials.
- Inbound JWT signature, issuer, audience, expiry, and signed `serviceurl` are verified before enqueue.
- The receiver and private host use separate least-privilege queue credentials.
- Secrets live in a secret manager or protected environment file and are rotated.
- Each customer has a separate bot identity, queue, data root/database, and process or deployment.
- A customer-owned Entra registration alone does **not** keep data inside that tenant. The full data
  path includes Bot Framework, receiver, queue, private host, and selected backend.
- For a literal customer-boundary promise, deploy the entire stack and backend inside the approved
  customer environment.

Read [Running Teams without putting the session host on the internet](teams-relay.md) for the
detailed threat model, delivery semantics, operational trade-offs, and measured latency/cost.

## Troubleshooting

| Symptom | Check |
|---|---|
| The app installs but every message is silent | Azure Bot has the Teams channel; messaging endpoint, DNS, TLS, and generated host/path match exactly |
| Manifest generation rejects the host | Use `relay.example.com`, not a URL, port, or path |
| Receiver answers 401 | Entra app ID/tenant/account type mismatch, wrong Azure Bot association, or invalid Bot Framework token |
| Receiver answers 503 | Queue URL or receiver SAS lacks add permission, is expired, or cannot reach Storage |
| Queue grows but no reply appears | `CCDB_FRONTENDS` includes `teams`; private process has the private SAS, app secret, and network access; inspect ActivityPuller logs |
| Private startup says a Teams variable is required | Set all five identity/secret/queue variables shown in step 5 |
| Reply fails after an activity was consumed | Verify the Entra client secret and tenant ID; rotate expired credentials; confirm the activity's `serviceUrl` was retained |
| Channel messages only work with @mention | Confirm the app is installed in that team and the owner granted `ChannelMessage.Read.Group` consent |
| A card press appears successful after the prompt expired | Expected relay trade-off: the public receiver acknowledges the invoke before the private prompt registry sees it; the private host still rejects the stale action |
| A generated command-menu entry becomes an agent prompt | Expected v4 limitation of the normal queue path; configure `CCDB_BACKEND` on the private host |
| Agent-created files are not delivered through the relay | Expected v4 limitation: the Teams surface has personal-chat file consent, but the private queue path does not yet bridge its invoke; channel delivery is also unsupported |
| Discord works but Teams does not start | The default is Discord-only; set `CCDB_FRONTENDS=discord,teams` and restart through the deployment's safe drain procedure |

For surface limits, prompts, commands, cards, and file behavior, continue with
[The Microsoft Teams frontend](teams.md).
