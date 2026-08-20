# Everything between Orbita today and "anyone can use it"

Written 2026-07-25. This is a complete inventory, not a plan. Some of these are hours,
some are weeks, and a few are not engineering problems at all.

**The vision being measured against:** a live service anyone can sign up for, upload any
substantial personal or research archive to, and have it become durable memory that any
AI model can connect to over MCP — with Orbita's falsification discipline applied to it.

Legend: **[me]** I can do it · **[you]** needs a decision, account, or money ·
**[nobody yet]** needs a person who is not currently on this project

---

## 0. What is actually done

Isolation per tenant, proven against each deployed build. Verified migration. Verified
deletion. Large-file upload by single-use ticket. Zip and ChatGPT-export parsing. Search
with citations. Change-of-position candidates. Operator-only refusal policy. Draft data
statement. Unscorable-candidate guard. 222 tests.

That is a working invited beta for one person. Everything below is the distance to
"anyone."

---

## 1. Capacity — the hard stop

**1.1 The volume is 4.6 GB total, for all users combined. [you]**
One ChatGPT export can be a gigabyte. Three users and it is full. When it fills, the
service does not merely refuse uploads — it fails for everyone, including you, mid-demo.
Options: pay for a bigger volume, move archives to object storage and keep only the
parsed index on disk, or cap per-user storage hard and say so. This single-handedly
prevents "anyone."

**1.2 No per-tenant storage quota. [me]**
One user can consume the entire volume. There is a global brake (uploads refused under
500 MB free) but nothing stopping a single tenant taking all of it.

**1.3 No rate limiting anywhere. [me]**
Upload tickets can be minted in a loop. Search can be hammered. No cost ceiling per user.

**1.4 No archive size cap per tenant, only per upload. [me]**
Ten 400 MB uploads are permitted where one 4 GB upload is not.

---

## 2. Security and privacy

**2.1 Encryption at rest is specified, not built. [me, after 2.2]**
Your own gate. Blocks all external ingestion.

**2.2 No external key infrastructure chosen. [you]**
Railway has no managed KMS. A master key in Railway env vars sits in the same trust
boundary as the ciphertext, so it is not key separation. Needs an AWS/GCP/Vault account
and an IAM decision. Blocks 2.1. Introduces a new hard runtime dependency: KMS down
means archives unreadable.

**2.3 Key destruction on deletion. [me, after 2.1]**

**2.4 Key-access logging. [me, after 2.1]**

**2.5 No general access audit log. [me]**
Nothing records who read what, when. For a service holding personal archives this is
expected, and its absence is hard to explain after an incident.

**2.6 No Terms of Service or Privacy Policy. [nobody yet]**
The data statement is honest prose, not a legal document. Accepting strangers' personal
data without one is a real exposure. This needs a lawyer, not an engineer.

**2.7 `ORBITA_AGENT_API_TOKEN` still exists. [you]**
Dead since the OAuth cutover, was stored in plaintext in `.claude.json`. Rotate or delete.

**2.8 No incident plan. [you]**
If an archive leaks, what happens, who is told, how fast.

---

## 3. Access and onboarding

**3.1 No self-serve signup. [me, after 2.1]**
Access is a GitHub allowlist plus a manual tenant binding per person. That is correct
today and is the opposite of "anyone."

**3.2 Plan v3 A1 forbids opening the allowlist. [you]**
You approved this today. Opening signup should be a deliberate plan v4, not a switch
flipped one evening.

**3.3 MCP-only interface. [me / you]**
Using Orbita requires Claude Code, ChatGPT connectors, or similar. Non-technical users
have no way in. A web UI is weeks.

**3.4 No landing page and no way to discover it exists. [you]**

**3.5 No user-facing documentation. [me]**
Nothing explains what to do after connecting.

**3.6 No account recovery. [me]**
If someone loses their GitHub, their archive is unreachable.

---

## 4. The product gap against the vision

**4.1 No model anywhere in the research pipeline. [you — costs money]**
This is why case goals must be blank. It blocks 4.2, 4.3, and 4.5.

**4.2 No claim extraction from conversation. [me, after 4.1]**
"Learns everything" is not happening. Uploads become searchable text, not understanding.

**4.3 No semantic linking. [me, after 4.1]**
Keyword matching only. The same idea in different words will not connect.

**4.4 No thread assembly. [me]**
Cannot show the arc of one idea across scattered conversations. Deterministic, buildable
without a model.

**4.5 Archive and belief graph are separate worlds. [me, after 4.2]**
Nothing links the conversation where you worked an idea out to the case where you tested
it. This is the most distinctive thing available and it does not exist.

**4.6 One archive format. [me]**
ChatGPT exports only. "Any data from research or accounts apps" is currently one reader.

**4.7 Falsification does not apply to archive content. [by design]**
Deliberate — see the unscorable guard. Worth stating so it is not mistaken for a bug.

---

## 5. Operations

**5.1 No backups of tenant data. [me / you]**
The volume survives redeploys. It does not survive deletion, corruption, or a Railway
incident. Holding people's irreplaceable archives with no backup is the quietest serious
risk here.

**5.2 No monitoring or alerting. [you]**
Nothing tells you the service is down except a user, and there is no on-call but you.

**5.3 `GIT_COMMIT_SHA` unset. [you]**
The running container cannot report which commit it is. This already left a gap in the
G21 evidence record.

**5.4 Single instance, single region. [you]**
Any deploy is a brief outage. Any Railway SFO incident is a full outage.

**5.5 `orbita-worker` in staging is crashed. [me / you]**
Known, unaddressed for weeks.

**5.6 `knowledge.sqlite` is a Git LFS pointer. [me]**
One test has failed on main for the entire project. Small, but it means the suite is
never actually green, which erodes the signal.

**5.7 Production MCP depends on staging Guided UI. [you]**
All Genome data lives in the staging database. A staging change can break production.

**5.8 Repository hygiene. [me]**
`orbita-guided-ui` is not a git repo; `orbita-research-mvp` sits on the wrong branch name.

---

## 6. Commercial

**6.1 No billing. [you]** Nobody can pay you.
**6.2 No cost model. [you]** Storage and compute per user are unknown, so pricing is unknown.
**6.3 No usage metering. [me]** Nothing measures what a user consumes.

---

## 7. The trip, which is five days away

**7.1** 20 consecutive clean demo runs — not started **[you]**
**7.2** Five unfamiliar-viewer comprehension tests — not started **[you]**
**7.3** Offline fallback complete — partial **[you]**
**7.4** Your own export ingested — not done **[you]**
**7.5** Data statement read and approved — not done **[you]**
**7.6** SSH key and 2FA recovery backed up — not done **[you]**

---

## The critical path, if the goal is genuinely "anyone"

1. **Storage decision (1.1)** — nothing else matters if the disk fills at four users
2. **Quotas and rate limits (1.2–1.4)** — cheap, mine, prerequisite for any second user
3. **Backups (5.1)** — before holding data you cannot replace
4. **KMS choice (2.2) → encryption (2.1, 2.3, 2.4)** — your own gate
5. **Terms of Service (2.6)** — before strangers, not after
6. **Self-serve signup (3.1) under a deliberate plan v4 (3.2)**
7. Everything in section 4, which is the actual product vision rather than the platform

Realistically: **1–3 are days. 4 is a week. 5 is not on your calendar. 6 is days once 4
and 5 are done. 7 is months.**

"Invited beta for people you have met" is roughly two weeks of the above.
"Anyone can sign up" is not a five-day target, and pretending otherwise is how the disk
fills during a demo.
