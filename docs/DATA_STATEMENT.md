# What Orbita does with your data

Last updated: 2026-07-25 · Status: **draft, not yet in force**

This document is written to be read before you upload anything. It describes what
actually happens, including the parts that are inconvenient to admit. If any of it is
unacceptable to you, do not upload — that is a reasonable response and not a failure.

---

## Current status: not open

Orbita is not accepting archives from anyone but its operator.

Per-tenant encryption is specified but not yet implemented or reviewed, and until it is,
uploading someone else's chat history would mean storing an unusually complete record of
their private life with weaker protection than that record deserves. Nobody is being
invited to upload until that is fixed. The service refuses non-operator uploads at
runtime, so this is a behaviour rather than an intention.

The rest of this document describes the state the service is being built toward. It is
written in the present tense so it can be checked against the code, but **it is not yet
true**, and every unimplemented item is marked.

---

## 1. What you are uploading

A ChatGPT export is not a document. It is close to a complete record of everything you
have thought out loud into a machine, often over years. In practice that includes:

- Health, including things you have not told a doctor
- Money, employment, and legal exposure
- Relationships, and conversations about people who never consented to be discussed
- Credentials and keys, if you have ever pasted one into a chat
- Work product belonging to your employer or your clients

Treat the decision to upload as proportionate to that, not to "it's just some chat logs."

**Before you upload, consider deleting conversations you would not want stored.** The
export is generated from your account at the time you request it; anything you remove
first is never transmitted.

---

## 2. What happens to it

**Ingestion.** The archive is uploaded through a single-use URL bound to one case and one
size limit, and expires. Zip members are extracted and parsed. `conversations.json` is
read into one row per message.

**What gets read.** Only the branch of each conversation that was actually displayed to
you. ChatGPT stores regenerations as siblings in a tree; discarded drafts are counted but
never treated as things you said.

**Indexing.** Messages are indexed for search within your own tenant. The index stores
message text and the identifiers needed to cite it: conversation, node, timestamp.

**What is never done.** Your archive is not used to train any model. It is not sent to any
third-party model provider — there is no LLM in this pipeline at all. It is not read for
advertising, profiling, or scoring. It is not shared with other users, and no aggregate
built from it is shared either.

---

## 3. Who can read it

**Other users: no.** Each tenant has a separate database and a separate workspace. Another
user's case identifier does not exist in your database, so the isolation does not depend
on a filter being applied correctly. This is verified against each deployed build before
any second identity is admitted.

**The operator (Derek Earnhart): yes, technically.** This is the part that matters and it
would be easy to obscure. The service must decrypt your archive to search it, so the
running system holds plaintext transiently, and the operator controls the running system.

**This is not end-to-end encryption and will never be described as such here.** If you
need a system whose operator provably cannot read your data, this is not that system, and
you should not upload.

**Railway (the hosting provider): yes, in principle**, to the extent that anyone with
infrastructure access can reach a running process. Encryption at the application layer
raises the bar against a leaked volume or a stale backup. It does not make a live process
opaque to whoever runs the machine.

---

## 4. Encryption *(specified, not yet implemented)*

- Each tenant's raw archive, extracted copies, search index, backups, and temporary
  staging files are encrypted at the application layer with a key unique to that tenant.
- Tenant keys are wrapped by a master key held in **external managed key infrastructure**,
  deliberately outside the platform holding the ciphertext. A key stored beside the data
  it protects is not key separation.
- Every unwrap of a tenant key is authenticated and logged: which tenant, which request,
  when.
- Plaintext exists only in memory, only while serving a request you authorized.

**Until this ships, only the operator's own archive is stored, and the volume has only
infrastructure-level encryption** — which protects against a stolen physical disk and
essentially nothing else.

---

## 5. Deletion

`orbita_delete_case` removes the case record, the uploaded files, their extracted copies,
and the search index entries. It verifies the directory is gone and the rows are gone
before reporting success, and returns a manifest of what was destroyed including each
file's sha256. This is implemented and tested against the volume rather than against the
API's own reply.

*(Not yet implemented)* Deletion will also destroy the tenant's data key, so that any
missed copy or stale backup becomes unrecoverable rather than merely unreferenced.

**One honest limit.** Deleting a *research* case does not delete claims derived from it.
Claims are hash-chained evidence records that other work can reference, and silently
truncating that ledger under an erasure request would corrupt exactly the provenance this
system exists to preserve. Claims that survive are listed in the deletion response.
A chat archive produces no claims, so deleting an archive case erases it completely.

---

## 6. What the results mean

Search returns messages containing your terms, each with the conversation, node, and date
it came from. **Presence means a message was written. It does not mean it was true then,
or that it is true now.** Nothing is summarized, inferred, or scored.

Change-of-position candidates pair a later message in which you marked a change of mind
with an earlier message sharing subject matter. **These are candidates, not
contradictions.** The system matched a marker and some shared words; it did not read
either statement. Word overlap is not topic identity, and changing your mind is not an
error. Only you can decide whether two things you wrote actually conflict.

---

## 7. Retention and access

- Your archive is retained until you delete it. There is no automatic expiry.
- Access is authenticated through GitHub OAuth. Sign-in is by explicit invitation; there
  is no open registration.
- Being able to sign in never implies access to a tenant. An identity with no explicit
  binding is refused rather than given a default.

---

## 8. Who to ask

Derek Earnhart · derekearnhart1@gmail.com

If something in this document turns out to be false, that is a defect and I want to know.
