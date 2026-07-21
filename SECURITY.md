# Security

graph-mem stores a knowledge graph on your machine and exposes it to two very
different kinds of caller: a language model over MCP, and a browser over HTTP.
Both are treated as untrusted. This document describes what is defended, how,
and what is explicitly out of scope.

## Reporting a vulnerability

Open a [security advisory](https://github.com/Sathvik-1007/GraphMem-MCP/security/advisories/new)
rather than a public issue. Include a reproduction if you have one — the
defences below were all written against reproductions, and a report with one
gets fixed considerably faster.

---

## Threat model

| Actor | Can do | Cannot do |
|-------|--------|-----------|
| The language model calling MCP tools | Anything the tool surface allows: read, write, delete graph data | Reach files outside `.graphmem/`, run SQL, bind a network listener |
| A website the user visits while the UI is running | Issue HTTP requests to loopback | Read responses, forge the session token, or reach the API |
| Another local process | Connect to the UI port | Authenticate without the session token |
| Someone with the UI URL | Everything the UI can do | — the URL contains the token; treat it as a password |

The model is the interesting one. Prompt injection is not hypothetical: any
document the agent reads can contain instructions, and the agent will call
tools based on them. So tool arguments are validated as hostile input, not as
programmer input.

---

## MCP tool boundary

**Graph names never become paths directly.** `create_graph`, `switch_graph`,
and `delete_graph` all route through one resolver that enforces a strict name
grammar (letters, digits, hyphens, underscores; 64 characters), rejects the
reserved stem `graph`, and re-checks after resolution that the result is still
inside `.graphmem/`. Two independent layers, so a mistake in either alone is
not exploitable.

This closed a real hole. Previously `delete_graph` took the name unvalidated:

```
delete_graph("../outside")  ->  {"status": "deleted"}   # file outside .graphmem/ unlinked
delete_graph("graph")       ->  unlinked the ACTIVE default database
```

**The dashboard binds loopback and is not configurable by the model.**
`open_dashboard` has no `host` parameter. It previously did, so a
prompt-injected agent could call `open_dashboard(host="0.0.0.0")` and publish
the entire knowledge graph to the local network. A human who wants that runs
`graph-mem ui --host ...` themselves and gets a warning.

**Every response is bounded.** Read tools clamp their limits and report
truncation rather than returning a silent subset. A negative limit is rejected
before it reaches SQL, where SQLite would have interpreted `LIMIT -1` as
unbounded and scanned everything into memory.

**SQL is always parameterised.** Where an identifier must be interpolated it
comes from a hard-coded allow-list, never from caller input. The FTS5 query
sanitiser tokenises on `[\w']+`, which cannot emit a quote character, so
FTS5 operators in a query string are neutralised rather than executed.

**Query text is not echoed back.** A failing statement is logged with its SQL;
the error returned to the model carries only the message. Returning query text
would disclose schema and query structure for no diagnostic benefit.

---

## Web UI

Binding to `127.0.0.1` is **not** a security boundary. Every web page the user
visits can issue requests to loopback, and every other process on the machine
can too. The UI has write endpoints, so it is defended in three independent
layers.

### 1. Host allow-list

`Host` must name the interface the server bound. A DNS-rebinding attack
resolves an attacker's domain to `127.0.0.1` and thereby reaches loopback with
the browser's same-origin privileges — but the request still carries
`Host: evil.example`, which is rejected.

### 2. Origin allow-list

A cross-origin `POST` with `Content-Type: text/plain` is a CORS *simple
request*: the browser sends it with no preflight and merely hides the response.
The write has already happened. This was reproducible against an earlier
version:

```
POST /api/entity
Origin: https://evil.example
Content-Type: text/plain

-> 201 {"id": "...", "name": "pwned"}
```

Browsers always attach `Origin` to such requests, so rejecting foreign origins
blocks them. It now returns `403` and nothing is written.

### 3. Session token

Blocks callers the header checks do not reach — any other local process, or a
container sharing the network namespace. API requests must carry the token in
the `X-GraphMem-Token` header. A custom header cannot be set by a cross-site
fetch without triggering a preflight, and the preflight fails the Origin check,
so the header is unforgeable from another web origin.

The token reaches the browser once, as a query parameter on the URL the server
opens. The document route exchanges it for a `SameSite=Strict` cookie so
reloads work, and injects the token into the served HTML for the SPA to use.

**The API never accepts the cookie as proof — only the header.** Accepting the
cookie would reintroduce exactly the CSRF hole these layers exist to close: a
cross-site request rides ambient cookies but cannot set a custom header.

Static bundle files (`/assets/*`, `/favicon.*`) are exempt from the token —
they are identical for every install and contain no graph data, and the browser
fetches them as subresources with no credential. They are **not** exempt from
the Host and Origin checks.

### Practical guidance

- The URL printed by `graph-mem ui` contains the session token. Treat it as a
  password: do not paste it into a shared channel or a bug report.
- The token is regenerated every run. Restarting invalidates the old one.
- `--host` anything other than loopback exposes the UI to your network. The
  token still protects it, but anyone who observes the URL has full read/write
  access.

---

## Data at rest

The database is a plain SQLite file at `.graphmem/graph.db`. It is **not**
encrypted. Anything the agent stores — architecture notes, credentials it was
careless enough to record, personal data — is readable by anything that can
read the file.

Add `.graphmem/` to `.gitignore` unless you intend to commit the graph, and
apply the same care you would to any other file containing project knowledge.

---

## Deliberately out of scope

- **Multi-user access control.** There are no users, roles, or per-entity
  permissions. Anyone who can reach the MCP server or hold the UI token has
  full access to the whole graph.
- **Encryption at rest.** Use filesystem or disk encryption if you need it.
- **Sandboxing the model.** graph-mem constrains what tool calls can *do*; it
  cannot constrain what the model is persuaded to *ask for*. A model convinced
  to delete a graph will delete a graph — within `.graphmem/`, which is the
  boundary that is enforced.
