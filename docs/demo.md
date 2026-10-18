# A demo instance for screencasts

Recording the Ebi AI Console on your own deployment puts real work on screen: thread titles,
customer names, notes in the vault. A **demo instance** is a second deployment that holds only a
scripted scenario and goes back to it on every reset, so you can show the Console doing real work
without showing yours.

It is not a mode inside your instance. A "demo" switch in the same process would still share the
session database, the vault and, most importantly, the agents' home directory: an agent asked to
"just do it" in a demo would still reach your mail, your notes and your cloud logins. A demo
instance is a separate OS user, so the boundary is the operating system's, not the UI's.

## What you get

- The Console on its own port, with no Discord or Teams frontend (`CCDB_FRONTENDS=console`)
- An Ebi Workspace vault and Console items written by a **scenario**, with dates relative to the
  reset day: something "due today" is due today every time you record
- A reset that empties the data (keeping your passkey, so you do not re-enroll before each take),
  rebuilds the vault, follows a branch so a new release brings its new demo items, and runs your
  own clean-up hooks for anything the agents changed outside the instance

## Scenarios

A scenario is a folder (see [`examples/demo/ja-it-admin`](../examples/demo/ja-it-admin), a
one-person IT department in Japanese):

```
scenario.toml   places and Console items
vault/          the Ebi Workspace, as Markdown
```

`scenario.toml`:

```toml
name = "One-person IT"
timezone = "Asia/Tokyo"
places = ["Office", "Home"]

[[items]]
title = "Create accounts for the three new starters"
note = "The list is in M365/new-starters.md."
priority = 0             # 0..3, 0 first
due = "+0@17:00"         # +N days, optionally @HH:MM in the scenario's timezone
project = "M365"
place = "Office"         # optional
# state, snoozed_until are passed through as well
```

In vault files (and in item text) `{{today}}` and `{{date:+N}}` / `{{date:-N}}` become dates
relative to the reset day. File names may carry them too, which is how a daily note is named
after its day. Any other `{{...}}` is left alone.

Items are created through the Console API, so they pass the same validation as anything a person
types, and the scenario keeps working when the database schema changes.

### When you add a user-facing feature

Add something to a scenario that makes the feature visible in the first minute of a demo — an
item that exercises it, or vault content it reads. The scenario test (`tests/test_demo.py`) loads
every bundled scenario, so a broken one fails CI.

## Setting it up

The example uses an OS user `ccdb-demo`; replace it with yours.

1. Create the user and, as that user, install the agent CLI and log it in, clone this repository
   to `~/relay` and run `uv sync --extra console`. Install the Ebi Workspace plugin if you use it.
2. Write `~/demo.env` (mode 600):

   ```bash
   CCDB_FRONTENDS=console
   API_PORT=8121
   CCDB_CONSOLE_PORT=8120
   CCDB_CONSOLE_ORIGIN=https://your-host:8444   # where the browser reaches it
   CCDB_CONSOLE_TOKEN=<random>                  # the reset seeds items with it
   CCDB_DATA_ROOT=/home/ccdb-demo/data
   EBI_WORKSPACE_NOTES_FOLDER=/home/ccdb-demo/vault
   CLAUDE_WORKING_DIR=/home/ccdb-demo/vault
   ```

3. Install [`deploy/demo/ccdb-demo.service`](../deploy/demo/ccdb-demo.service) and
   [`deploy/demo/ccdb-demo-reset.service`](../deploy/demo/ccdb-demo-reset.service), and copy
   [`deploy/demo/reset.sh`](../deploy/demo/reset.sh) to `/usr/local/sbin/ccdb-demo-reset`
   owned by root, mode 0755.
4. `systemctl start ccdb-demo-reset.service`. The first run marks `~/vault` and `~/data` as demo
   folders, builds them and seeds the Console. The log shows the setup code for the first passkey.
5. Start the reset from your scheduler every night and after each deployment.

The reset runs as root because it stops and starts the service. **Never point it at the copy in
the demo user's checkout**: agents in a demo act as that user and can edit that file. Everything
the reset does inside the demo home runs as the demo user, detached from any terminal.

### Safety rails

- `reset` refuses to touch a vault or data folder that does not carry the marker file it wrote
  there (`.ccdb-demo-vault`, `.ccdb-demo-data`). Pointing it at your real vault by mistake fails
  instead of wiping it.
- The data wipe empties every table except `console_passkeys` and `console_sessions`.

## Connecting the demo to real services

A demo is more convincing when "create the new starters' accounts" really creates accounts. Give
the demo user **its own** credentials, scoped to what the scenario needs, and put the clean-up in
`~/reset.d/`: every executable there runs as the demo user after the wipe. Examples of a scope
that holds:

- **Microsoft 365**: an app whose write access comes only from roles scoped to an administrative
  unit (User / Groups / License Administrator), plus read-only Graph permissions. A dynamic
  membership rule (for example on `companyName`) lets the agent create users that land in the unit.
- **Azure**: a service principal with Contributor on one resource group, plus Azure Policy on that
  group (allowed locations, allowed VM sizes, denied expensive resource types) and a budget alert.
- **A lab host**: an SSH key that may only open a tunnel to the lab's control VM
  (`restrict,port-forwarding,permitopen="…"`), never a shell on the host that also runs production.

Keep these credentials, tenant IDs and hook scripts out of this repository: they belong to your
instance.
