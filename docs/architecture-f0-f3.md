# Agora architecture baseline for F0-F3

Status: inspected against the local worktrees on 2026-09-01.

## Authority and boundaries

- The current project brief supplied with Agora is authoritative when older material
  differs.
- The BOARD lives on the primary PC and has one direct filesystem owner: Agora.
- Remote runners use pull through the future Agora API. They never mount or synchronize
  `KANBAN/`.
- Athena decides what work to delegate and remains usable without Agora.
- Agora selects a compatible profile/worker and decides when durable work may run.
- AI_Broker remains the exclusive model/provider router.

## Evidence from Atomic Agents

`docs/atomic-agents-standard.md` defines an ephemeral worker, PROFILE as contract, SKILL as
know-how, CARD as work and production permit, folder state, deterministic matching, atomic
claim, visible errors, zombie recovery, and a model-free dispatcher. Agora preserves those
invariants while adding an API boundary around the single filesystem owner in F2.

## Evidence from Athena

- `Athena/docs/adr/001-athena-owns-agent-runtime.md`: Athena owns `AgentLoop` and runtime
  policy.
- `Athena/docs/adr/002-ai-broker-is-a-model-provider.md`: AI_Broker is an optional adapter
  behind `ModelProvider`; the Athena core has no broker dependency.
- `Athena/docs/adr/014-extensions-restrict-but-never-grant.md` and
  `Athena/src/athena/skills.py`: `SkillManifest` is knowledge, required toolsets are
  preconditions, and a skill never grants capability.
- `Athena/docs/adr/023-the-executor-joins-what-already-existed.md`: graph execution reuses
  the one `AgentLoop`; Athena must not gain a second model router.
- `Athena/docs/adr/028-a-profile-declares-what-counts-as-done.md`: `AthenaProfile` describes
  a whole run and is not an Atomic `PROFILE.md`.
- `Athena/src/athena/adapters/ai_broker.py`: the current adapter submits
  `POST /api/v1/tasks`, polls `GET /api/v1/tasks/{id}`, cancels with `DELETE`, and sends
  `X-Admin-Token`.

F4 must therefore add an optional external-work adapter beside Athena core and a local
Athena harness runner. It must not alter `AgentLoop`, replace `ModelProvider`, or merge
`AthenaProfile` with Atomic PROFILE.

## Evidence from AI_Broker

- `AI_Broker/app/main.py` and `AI_Broker/app/schemas.py` expose contract 2.9: durable async
  tasks, idempotency, cancellation, file ingestion, capabilities, invocation telemetry,
  effective generation parameters, execution fingerprints, and actual cost/model data.
- Attachments are uploaded through `POST /api/v1/files`; a task is rejected until each
  referenced file is `ready`.
- Atomic jobs can set `prompt_compression: off`; the current deployment has global
  aggressive compression.
- The current deployment has `task_timeout_seconds: 3000` and three internal broker
  attempts. Agora CARD attempts are a separate counter, and the Agora zombie threshold
  must exceed the maximum legitimate broker lifetime plus a safety margin.

## X-Admin-Token lifecycle

`AI_Broker/scripts/run_broker.py` reads the configured environment variable. If absent, it
generates `secrets.token_urlsafe(24)`, stores it in the broker process environment, and
prints it. `AI_Broker/app/admin_auth.py` resolves the environment first and otherwise the
Windows keyring. There is no IPC, temporary credential file, or rotating shared keyring
entry through which an independently started runner can recover that generated value.

Printing the complete token is incompatible with the target requirement that it never
appears in captured logs. F3 must not silently scrape logs.

Preferred design requiring no AI_Broker core change: an Agora AI-host supervisor on the AI
PC generates a new credential in memory for each broker child process, injects it into the
broker environment, retains it only in the local runner/gateway process, and redacts child
output. A separately started broker cannot be auto-discovered under the current contract.
Changing AI_Broker to publish a rotating credential through Windows Credential Manager is
the alternative and requires an explicit compatibility decision in F3.

## Contradictions resolved

1. The older integration PDF locates the BOARD on the AI PC; the current brief requires
   the primary PC. The current brief wins.
2. The older PDF includes a broker harness in F0; the current brief requires F0 without
   network or AI_Broker. The current brief wins.
3. The Atomic standard says a folder move is sufficient when workers share one filesystem.
   In the two-PC deployment, only the Agora server performs that move; remote claims use a
   server-side compare-and-swap endpoint in F2.
4. The standard says same PROFILE plus CARD is reproducible, while routed inference is not
   bit-for-bit reproducible. Agora will state this honestly through explicit `strict` and
   `routed` policies and record the effective model/configuration.

## Initial modules

```text
src/agora/
  cards.py       CARD parsing, validation, atomic serialization, Record
  board.py       six states, state transitions, atomic claim, zombies
  profiles.py    direct PROFILE census and validation
  skills.py      SKILL validation and shared-format seam
  matching.py    deterministic funnel and overlap analysis
  scheduler.py   idempotent scheduled-card materialization
  dispatcher.py  model-free heartbeat and injectable launcher
  harnesses.py   F0 deterministic fake harness
  cli.py         init, dry-run dispatch, overlap check
```

F1 adds `agora.desktop` as a client of these services. F2 adds a versioned API and pull
runner. F3 adds the broker harness, local credential acquisition, attachment readiness,
poll/cancel/recovery, and invocation telemetry without putting routing in Agora.

