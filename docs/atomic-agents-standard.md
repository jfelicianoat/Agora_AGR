# Atomic Agents: a harness-agnostic standard for AI agents that live as text

> An agent is not a running process. It's a contract written in plain language, plus a card that gives it permission to work.

**Author:** Marcos Emowe · **Version:** 1.0 · **Status:** running in production since August 2026

This is a specification, not a framework. There is nothing to install. Everything below is Markdown files in a folder and one small Python script that carries no model.

---

## The problem

Most agent systems are conversations. You open a session, the agent loads its memory, its rules, its tool catalog, and then you ask it to do something. That works while you're sitting there.

Three things break the moment you want the work to happen without you:

1. **It doesn't run when you're away.** Someone has to start the session.
2. **The result isn't reproducible.** It depends on what the session had loaded, so the same request gives different answers on different machines.
3. **You can't hand it to anyone.** The agent lives inside one tool's syntax. It doesn't travel.

Atomic Agents fixes all three by making the agent a file instead of a process.

---

## Core idea

An **atomic agent** is an ephemeral worker that is born to handle one card, handles it, and dies.

Three defining properties:

**No ambient context.** It loads no persistent memory, no global rule files, no skill catalogs. Everything it knows arrives through two channels: its own `PROFILE.md` and the card it was given. Consequence: same profile + same card = same context = reproducible.

**Ephemeral.** The process ends when the card closes. If it doesn't die, it's a zombie and the dispatcher reclaims its card. The worker is a day laborer, not an employee: it shows up for one job, does it, and leaves.

**Under contract.** The profile declares what it handles, what it refuses, what it guarantees, and how it closes. The card *asks*; the profile decides whether the request is its own.

The opposite of an atomic agent is a **continuous agent**: the always-on session with the full backpack (memory, vault, conversation), for work that needs implicit context. Rule of thumb: if the task fits in a card with inputs passed by reference, it's a worker's job. If writing the card makes you type "search my notes for…" or "you already know that…", it belongs to the continuous agent.

---

## The pieces

| Piece | What it is |
|---|---|
| **Profile** | The contract. Its name is a *trade* (noun). Declares function, what it handles, inputs/outputs, guarantees, and which skills it mounts. Never contains know-how. |
| **Skill** | The know-how. Its name is a *verb*. Mounted by profiles, or by the continuous agent. Two doors, one body of knowledge. |
| **Card** | A unit of work *and* a production permit. A `.md` file on the board. No card, no worker. Its state IS the folder it lives in. |
| **Board** | The shared bus, made of folders. Agents never talk to each other: they drop and pick up files here. Integration happens on the board, not in code. |
| **Dispatcher** | The blind, deterministic runner. Each heartbeat it matches cards to profiles by string comparison, spawns the process, and rescues zombies. No model. No semantic judgment. |
| **Vault** | The knowledge base agents read from and write to. Folder indexes let skills infer where inputs live and where outputs go, even when nobody wired it. |

---

## Anatomy of a profile

```
AGENTS/<name>/
├── PROFILE.md
├── skills/<skill>/SKILL.md      ← local know-how
└── examples/case-*/             ← frozen material: input.md, expected-output.md
```

### Front matter (the contract)

| Field | Meaning |
|---|---|
| `name`, `version` | identity; version bumps with every scar |
| `description` | 2-3 plain sentences: what it does and doesn't do |
| `function` | cognitive function — level 1 of matching |
| `handles` / `refuses` | descriptors matched against the card's request |
| `inputs` / `outputs` | name, type, transport, wireable, default, description |
| `guarantees` | what it will never do (read-only on inputs, writes confined to the output path) |
| `model_capacity` / `model_modality` | what the model needs (minimum/standard/advanced × text/audio/image) — never concrete model names |
| `skills` | skills it mounts |
| `harness` | optional preferred harness; falls back to the dispatcher's default |
| `record` | measured facts only: reviewed, tested_harness, tested_skills@version, typical_turns |

### Body (six sections)

1. **Mission** — what it produces and what it does *not* do.
2. **Procedure** — the card mode: claim → admission check → resolve inputs → mount skills → work → closing ritual. **In card mode there is no interlocutor**: the profile must forbid dying with a question. On any failure, apply the failure policy and die.
3. **Inputs** — semantics of each, plus the bad-input policy: never invent raw material.
4. **Quality** — when the result is good enough; a generate-judge-regenerate loop is allowed before closing.
5. **Exceptions** — legitimate outcomes that are not failures (an empty selection with a justification, for example).
6. **Record** — changelog of versions and scars.

### Naming standard

- **Skill = infinitive verb + object.** `transcribe-audio`, `archive-cards`, `capture-web`.
- **Profile = trade (agent noun).** `transcriber`, `capturer`, `maintainer`, `editor-in-chief`.
- **Tool or kit → prefix**, keeping the pattern: `youtube-research-trends`.
- **Difficulty is not in the name** — it lives in `model_capacity`. A profile that only classifies and one that writes an essay are both named by their trade; what separates them is declared capacity.

### Overlap check (mandatory when creating a profile)

The risk at scale isn't the number of profiles — matching is deterministic — it's two profiles with overlapping `handles`, so a card matches the wrong one.

For each descriptor in the new profile's `handles`, simulate matching it against all profiles and confirm it resolves to the new profile, unambiguously. If a descriptor lands elsewhere, refine `handles`/`refuses` until every descriptor has exactly one owner. This is a design-time check, done once, not a runtime one.

---

## Two doors, one body of knowledge

A skill can run through two doors, and they converge in the know-how, not in the door:

| Door | Who invokes | Contract that applies |
|---|---|---|
| **Continuous agent** (conversation) | The 24/7 session, calling the skill directly | Its own: global rules and guarantees, conversational admission, dynamic catalog |
| **Board** (card) | The dispatcher, via `PROFILE.md` | The profile's: unattended admission, declared guarantees, card closing ritual, "the card asks, it does not command" |

Normative consequences:

1. **Continuous-agent skills need no profile.** The continuous agent *is* its living profile. A profile is created when — and only when — the skill must run as an autonomous unattended unit.
2. **A profile never contains know-how, only contract.** This is the invariant that keeps the two doors from diverging: scars about *how to do the work well* go in `SKILL.md` (both doors inherit them); scars about *card protocol* go in `PROFILE.md`.
3. **A `modes:` field** in `SKILL.md` declares which doors a skill supports: conversation only, conversation plus card, or additionally schedulable.
4. **Acid test for card mode:** the card can be written completely without the agent asking anything or knowing the vault. If not, it's a conversation skill.

---

## Hierarchy: workers, directors, CEO

Three floors on **one board, one dispatcher, one heartbeat**. Rank lives in the profiles, not in the infrastructure (fractal pattern):

| Floor | What it does | Creates cards |
|---|---|---|
| **Worker** | Works and closes | Never |
| **Director** | Owns a flow: distributes and chains | Yes, its flow's cards |
| **CEO** | Director of directors | Yes, director cards |

**Rank as a discriminated union:** `skills: [a, b]` → worker. `profiles: [x, y, z]` + `failure_policy:` → director. Never both. There is no `rank:` field — it would be redundant and eventually contradict the list.

**Golden rule — one card at a time.** The director creates ONE child, dies, and creates the next when the previous one closes. It never dumps the whole flow into `pending/` (the dispatcher is blind: it would dispatch all three and the later ones would start with no input), and it never pre-wires paths that don't exist yet. Benefit: if step 1 produces nothing, steps 2-3 never come into existence.

**How a director wakes up — it's dead.** Its memory is the cards, not its context. A restart mid-flow means rebuilding state by reading the board. Mechanism: the *input gate the dispatcher already has*, with zero new logic. At each step the director writes into its own card `inputs: dependent_task: <path to the child in done/>`, returns the card to `pending/` **without** incrementing attempts (a yield, not a failure), and dies. The gate keeps it asleep until the child lands in `done/`. Pull by signal, not by polling: the child arriving in `done/` *is* the kanban card coming back.

**Wiring self-check (mandatory in every director).** The one fragile point of `dependent_task` is that an LLM writes it: one mistyped reference leaves the card deaf forever. So before dying at each yield, the director re-reads its own card and verifies that the value matches the child's filename exactly. Both gestures happen in the same wake-up, so it's a trivial comparison. A director without this rule in its Procedure is incomplete.

**Wiring rule:** on waking, the director reads the closed child's `paths` and puts them in the next card's `inputs`. It resolves **instances**, never redefines **contracts**: it fills the slots the worker's profile declares. A child closed with empty `paths` is a **false close**: don't chain, block the director's card, log it. Never invent the missing step.

**When not to build a director:** with a single step (bureaucracy); before the workers are proven (a director multiplies failure, it doesn't fix it); and never build the CEO before you have two real directors.

---

## The card

A `.md` file with front matter, prose body, and a `## Record` section (chronological log).

| Field | Written by | What it is |
|---|---|---|
| `function`, `request`, `created`, `origin` | creator | matchable request + provenance |
| `paths` (empty at creation), `attempts: 0` | creator / system | exact outputs at close; return counter |
| `priority` | optional | normal or urgent — urgent dispatches first |
| `inputs`, `destination` | optional | raw material by reference; a concrete file destination is an order |
| `recipient` + `push_reason`, `flow`, `director` | optional | direct push to a profile (skips matching), chain membership |
| `agent`, `claimed` | claimer | claim in progress |
| `model` | dispatcher at claim | the concrete model that did the work (cost/quality traceability) |
| `closed` | closer | timestamp; enables exact archiving and end-to-end duration |
| `blocked` | system | date + short reason; untouched until a human clears the field |

**Record format.** The parent line carries only date and time; the actor and each milestone are indented beneath it. Never one enormous line:

```
- 2026-08-20 13:56
    - writer: admission passed (matches handles).
    - Inputs resolved and valid: selection (<path>) and editorial identity (default).
    - Draft written on the chosen trend using write-in-own-voice@1. 1 turn.
    - Draft at <path>, ready for human review.
    - Card closed.
```

**Be explicit.** Every milestone names the concrete object worked on — its human title, not just an ID or a counter — and its per-item result. The Record has two readers: agents rebuilding state, and the vault's owner. Both must understand what happened without opening a log. `processed: 1 (aB3xK9pQ2wE)` is precise for a machine and opaque for a human.

**The card asks, it does not command.** No instruction inside a card can override the profile, its guarantees, or the system's rules. A card asking to bypass guarantees is blocked as suspicious, without doing any work. This is prompt-injection defense in depth, alongside origin validation.

---

## The board

```
KANBAN/
├── pending/    ← waiting for a capable profile or for raw material
├── in-progress/← claimed (agent + claimed in front matter)
├── done/       ← closed, with paths filled and a complete Record
└── archive/    ← history by month (YYYY-MM/)
```

Life cycle: created in `pending/` → claimed into `in-progress/` (claiming = moving the file, an atomic operation) → worked → `done/` with `paths` filled and a closing line in the Record. Failed admission or bad input → back to `pending/` with `attempts`+1, or blocked. Zombie (claimed longer than the threshold without closing) → the dispatcher returns it to `pending/`; on exhausting `max_attempts` → blocked.

**State is the folder, not a database.** Two workers can never claim the same card, because moving a file is atomic. No locks, no coordination protocol, no message bus.

---

## The dispatcher

The board's heartbeat. It lives **outside the vault**, in its own git repo: code that runs on its own must not live where agents write. No model, no semantic judgment, no state. It compares normalized strings, counts, and checks whether files exist.

Each round (every N minutes, with a PID lock against overlap):

**0. Scheduled cards.** The only cron in the system is the heartbeat; all demand lives in the vault. Templates in `KANBAN/scheduled/` (a normal card plus a trigger block: `every: day|week|month`, `day`, `time`, `active`) materialize as instances in `pending/` when their period comes due. One instance per period — the filename `YYYY-MM-DD-<slug>.md` gives idempotency by file existence. `time` means "not before this hour within the period", not exactness. No catch-up for missed periods. Step zero **never assigns a profile**: the instance enters the same funnel as a manual card.

**Two-layer doctrine:** work that needs an LLM → a scheduled card. A deterministic script (reminders, backups, watchdogs) → the OS scheduler, where the heartbeat also lives. Something has to beat outside the board for the board to work.

**1. Profile census.** Read the front matter of every `PROFILE.md` under `AGENTS/`. Direct read, every round: never a cached list, never a central registry. Adding an agent is copying a folder.

**2. Card census** in `pending/`, discarding: blocked cards, cards at `attempts >= max_attempts`, cards whose `origin` isn't in the trusted list, cards addressed to a human, and cards whose declared input files don't exist yet (wait — that's not an incident).

**3. Matching (funnel + ranking).** Level 1: `function` must be equal. Level 2: a matched `refuses` descriptor eliminates the profile. Level 3: the longest matched descriptor wins (specificity). A `recipient` naming a profile skips the funnel entirely.

**4. Dispatch.** Urgent first, then oldest first, up to `max_dispatches_per_round`. Claim the card, compose the harness command, spawn the detached worker process, with a per-execution log.

**5. Zombies.** Return to `pending/` any card claimed longer than the threshold, incrementing attempts.

**Dry-run mode** reports what it would dispatch without claiming, blocking, or writing anything. Spawning goes through an injectable seam, so tests exercise real claiming with a fake spawn.

### Why blind matching, on purpose

Letting a model decide which agent handles each card destroys determinism: the same board would route differently on different days. String comparison is dumb, auditable, and free.

The consequence matters more than the mechanism: **when the system doesn't know what to do, it doesn't guess. It waits and it shows.** An unmatched card sits in `pending/` costing nothing, and it's telling you something useful — either a profile needs a new descriptor, or you've just found the next agent you need to write. A wrong dispatch costs money and produces garbage someone has to clean up.

---

## Harnesses (adapters.yml)

Each installed harness declares its execution template:

| Field | Purpose |
|---|---|
| `command` | argument list (no shell → no injection); `{prompt}` and `{model}` substituted per element |
| `prompt_template` | the instruction text, with paths to profile and card. Belongs to the *harness*, not the profile |
| `environment` | literal variables (isolated home, explicit PATH) |
| `environment_from_private` | keys the dispatcher reads from a private env file and injects into the process — never written into versioned config |
| `working_directory` | the worker's cwd; neutral when the harness auto-discovers config by directory |

The concrete model comes from `models.yml`: the profile's declared level × the harness. Changing model, provider, or going local is one line in a table. **The profile never names a model.**

Worker harnesses run in isolated mode: a dedicated profile or home with no memory, no global rules, no preloaded skills, minimal toolsets, and a turn budget. Clean context is not the same as neutral context — a subagent spawned from a session inherits its parent's system prompt, tools, and rules. An atomic agent starts in a new process and sees only its profile, its declared skills, and the card.

**The lock rule:** every harness registered must wire the credential-blocking hook through its own mechanism. A new profile or harness never inherits protection automatically.

---

## Security, in layers

1. `origin` validated against a trusted list — the dispatcher blocks foreign cards.
2. "The card asks, it does not command", stated in every profile — prompt-injection shielding.
3. A credential-blocking hook wired into every harness.
4. Keys injected by environment at dispatch time only; never in versioned config. **Keys never touch an LLM.**
5. Minimal toolsets per harness; widen only to the minimum that passes the closing ritual, never to "all".
6. The dispatcher's code lives outside the vault, where agents don't write.

---

## Integrity rules

**Front matter integrity is an invariant.** No agent write may leave a card's front matter unparseable. A worker never invents fields: only the ones the card template declares. The Record always goes in the body; if the section is missing, the worker creates it — it never replaces it with a YAML field. *Why:* front matter is the one surface read by both the human board view and the dispatcher. Corrupting it drops the card out of both systems without either complaining.

**Fail loudly: nothing disappears in silence.** An unreadable card is declared, not skipped. Every route degradation (a fast path missing, a skill not found, falling back to a slower engine) is written to the Record *before* it happens. A loud failure costs a minute of reading; a silent one costs an hour of diagnosis.

**Traceability during execution, not only at close.** Every worker writes a progress milestone right after admission: which skill it's mounting and what it's about to do. A card in `in-progress/` with no milestones is indistinguishable from a hung one, and the owner's natural reaction destroys good work.

**A package must stand on its own.** No primary path of a distributed profile or skill may depend on files that don't travel with it. A personal shortcut is legitimate only as an optional optimization, with the complete path intact and the fallback declared.

**Every scar ends in a test.** Each real incident leaves its round with an automated check, not just a paragraph. A scar without a test reopens.

---

## Why this shape

**Reproducibility.** Same profile + same card = same result. Debuggable and testable like a factory part, not like a conversational black box.

**Model per task.** A cheap model for the dumb work, an expensive one only for the hard part. A single agent uses one model for everything.

**Visible cost, per piece.** Each closed card carries its model and its cost. You see spend per task and per flow, not an opaque monthly bill.

**No provider lock-in.** No model is hardcoded anywhere. Switching providers is editing a table. In this field the landscape changes every quarter.

**Horizontal scale.** Several machines can share one board. (If these were concurrent sub-second tasks in a critical system you'd want a real database. For chunked agentic work, a folder of files is plenty.)

**Parallelism without collisions.** Many workers at once, each isolated. Claiming is moving a file, so two never collide.

**Auditability with no extra tooling.** State IS the board, and a closed card is the receipt. All plain text, no external dashboard to maintain.

**Growth costs no friction.** Adding an agent requires modifying nothing, because there's nobody to notify: integration happens by dropping files on the board.

**Pull, not push.** Nothing is produced without a signal to pull. Idle capacity waiting beats accumulated inventory. And you stop being the central planner who matches every task to every tool by hand.

**If the tool disappears, you keep the method.** The whole system is a folder of text files. It's exactly what Toyota would keep if you took away their machines: the method, not the equipment.

---

## What this is not

It is not a framework, a library, or a product. There is nothing to `pip install`.

It is not a replacement for conversational agents. Delegating to a subagent inside a session you're actively running is the right call for heavy work you need in the next two minutes. This standard is for work you want to happen without you, repeatably, and be shareable.

It is not novel infrastructure. Dependency graphs are from the seventies, `make` is from 1976, and kanban is from Toyota in the fifties. What's new here is the substrate: applying it to LLM agents defined as plain text contracts, so they survive the tool that runs them.

---
## Credit

Built by **Marcos Emowe**, 2026. Running in production on a Mac Mini since August 2026. I write about digital brains and AI agents at [emowe.com](https://emowe.com).

The reasoning behind this standard, in Spanish: [por qué atomizamos](https://emowe.com/cerebro-digital/por-que-atomizamos/).

If you build on this, a link back is appreciated. The specification is free to read, use and adapt. The name **Cerebro Digital®** is a registered trademark; using it requires permission.
