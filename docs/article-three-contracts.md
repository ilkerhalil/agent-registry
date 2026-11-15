# The three contracts

**An agent registry is only worth building if the runtime cannot ignore it.**

Every team that ships agents ends up with the same conversation six months later. Someone asks which
version is in production. The answer is a guess. Someone asks whether that version can read customer
records. The answer is a wiki page, last edited in March. Someone asks who owns it. The answer is a
name of a person who left.

Nothing was broken that day. The agent was running fine. What was broken is that the answer lived in
a document, and the document had no power over the process.

That is the distinction worth designing around. Agent sprawl is not a documentation problem. It is a
runtime problem: the wrong version keeps running because nothing stops it.

I built a small service around three contracts. Each one exists to close a specific hole, and each one
is enforced somewhere the application cannot argue with.

## Contract one: the manifest is immutable

An agent is described by one file: what it does, which tools it may call, which data scopes it may
reach, which team owns it, how it is evaluated, where a human must intervene.

The important part is not the file. It is that the registry never updates it. A change is a new row
with a new `manifest_hash`, computed over canonical JSON. The old version keeps existing, unchanged,
with its own hash.

This kills a whole class of incident. There is no "someone edited the agent config at 2am". There is
only "a new version exists, and here is its hash". Rollback is not an edit either: it is promoting an
older version, which produces an audit event like everything else.

Databases do not enforce this by accident, so there is a trigger: `agent_version_manifest_immutable`
rejects any `UPDATE` that touches `manifest_json`. The rule is in the schema, not in a code review
checklist.

## Contract two: the runtime verifies, it does not trust

When an agent starts, it asks the registry for its version and receives a manifest plus a signature.

Here is the subtle part, and the one I got wrong first.

The signature covers `manifest_hash`. So a naive client verifies the signature against the hash in the
response and calls it a day. An attacker who changes `data_scopes` after signing and leaves the hash
untouched passes that check. The signature is valid; the content is not what was signed.

The fix is boring and essential: the client recomputes the hash from the manifest it received and
compares. Signature verification alone is not integrity. Hash the content locally, then verify the
signature over that hash.

```python
agent = runtime.resolve("claim-triage", env="prod")
agent.manifest_obj.data_scopes  # only reachable if the local hash matched
```

The same contract covers the failure modes people forget to design for:

- **Registry unreachable.** The last verified copy is reused from disk — for as long as its
  `expires_at` allows. After that the agent refuses to start. Availability is not allowed to become
  an excuse for running unverified code.
- **Expired copy.** Refused, with the reason stated plainly.
- **Manifest from another tenant or environment.** Refused, by comparing the response against what
  was requested.

The SDK has a test suite for exactly these three. Not because they are hard to write, but because
they are easy to skip.

## Contract three: the audit trail cannot be edited

Every register, version, validate, promote, deprecate, retire and rejection writes an `audit_event`
row. Postgres triggers reject `UPDATE` and `DELETE` on that table.

An audit log you can edit is a log that will eventually be edited — not always maliciously, often just
by an operator fixing a mistake. The point of the trigger is that the fix has to be a new event.

## Between the contracts: the gate

The gate is what stops an agent from reaching production. It runs twice: in your CI, and again
server-side at promotion. The second run exists because the first one happened on a machine you do not
control. A client saying "the gate passed" is not evidence.

Eight checks, each aimed at a real failure:

| Check | What it stops |
|---|---|
| Manifest schema | malformed manifests reaching production |
| Eval evidence | a success rate typed by hand |
| Eval thresholds and regression | "it feels better" promotions |
| Data scopes against the team's allow-list | a version quietly widening its access |
| Every tool in the catalogue | agents calling tools nobody owns |
| Every model in the catalogue | shadow models |
| Owning team and on-call | orphaned agents with no one to page |
| Scope conflict with the active version | two versions fighting over the same scope |

Two of these deserve a note.

**The scope check is the one that earns its keep.** Adding a line to a YAML file is how permissions
grow in practice. Nobody writes a design document to grant themselves `salaries:write`; they add a
line and move on. The gate compares the requested scopes against the owning team's allow-list, and
refuses. In the examples repository there is a manifest that differs from a passing one by a single
scope line, and the gate output is one line: `not allowed: ['salaries:write']`.

**Eval evidence started as a mistake of mine.** The first version accepted a JSON with a
`success_rate` and a suite name. That is a number anyone can write. It is a grade with no exam paper
attached.

So the gate now demands the surrounding facts: how many cases, which dataset hash, which harness
version, when it ran. Timestamps must carry a timezone and move forward. Per-suite case counts must
add up to the total. And when the deployment requires it, the whole payload must be signed — the
signature is over the canonical payload with the attestation removed, so changing a digit afterwards
breaks it. Promotion re-verifies what was stored, which means editing the row in Postgres does not
help you.

What this does not do: the registry still does not run evaluations. It guarantees the evidence is
complete, consistent, bound to the manifest's suite, immutable, and — if signing is enforced —
producible only by someone holding the key. Making the gate the evaluator is a different product.

## What this is not

Not a model registry. MLflow tracks models, runs and metrics. It does not know what an agent is
allowed to touch, who owns it, or what retirement means.

Not an observability tool. Tracing tells you what happened. A registry decides what may run.

Not a configuration store. Kubernetes ConfigMaps distribute settings; they do not gate, sign, or
audit them. Anyone with write access ships anything.

The registry is the narrow thing between those: identity, permission, approval, proof.

## Trying it

```bash
docker compose up -d db
uv run python examples/demo.py       # register → version → eval → promote → signed resolve → audit
bash examples/gate_all.sh            # 3 pass, 4 fail — both are the point
uv run python examples/runtime_offline.py   # no server: cache fallback, expiry, tamper rejection
```

The third one is the argument in executable form. It runs with no database and no network, and it
shows the SDK serving a cached copy when the registry is down, refusing an expired one, and rejecting
a manifest whose scopes were changed after signing.

126 tests, 93% line coverage, CI on Postgres 16 with cosign installed so the signature tests actually
run instead of skipping.

## What is still missing

No load test. The S3 artifact store is exercised against a fake client, not a real bucket. Signing
defaults to HMAC; Cosign works and is tested, but it is not the default because a shared secret is
simpler in a single trust domain.

I would rather list those than pretend the edges are finished.

## The one-sentence version

A manifest that cannot be edited, a runtime that verifies instead of trusting, and an audit trail that
cannot be rewritten — everything else in the registry exists to make those three true.
