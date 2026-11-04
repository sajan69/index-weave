# Local API and interface

The server opens one existing workspace chosen at startup and holds its writer
lease until shutdown. It never creates, resets or repairs a workspace on an HTTP
request. Stop it before using another writer, source compaction or tail repair.
Independent source readers remain available between commits. The React interface
and HTTP API use the same custom store and retrieval engine as the CLI.

## Reproducible setup

Use CPython 3.12+ on a trusted POSIX local filesystem and Node 24 for the
frontend build. From the checkout:

```sh
python -m venv .venv
npm ci --ignore-scripts
npm run build
.venv/bin/pip install . -r requirements-api.txt
mkdir -p collections
.venv/bin/indexweave create collections/notes
.venv/bin/indexweave-serve collections/notes
```

Open `http://127.0.0.1:8765`. The interface starts with an empty collection;
create a document or explicitly select a UTF-8 `.txt` or `.md` file. Imports are
limited to 64 KiB, decoded strictly and loaded into a draft for review before
saving. Original file contents are not changed. Markdown is stored and displayed
as plain source text. Download returns the displayed text, including unsaved
edits; it is not a whole-workspace backup.

Vite builds the React/TypeScript sources into ignored `src/indexweave/web/`. The
subsequent wheel build includes those static assets. No CDN, external font,
image service, analytics or remotely hosted UI is required. Build the interface
before installing a checkout when browser access is needed. Core CLI-only wheels
can still operate without the optional API requirements.

For semantic and hybrid modes, first acquire the verified local model using
[the explicit model setup](SEMANTIC.md), then start:

```sh
.venv/bin/indexweave-serve collections/notes --model artifacts/minilm
```

The model is loaded once at startup. Use **Refresh semantic index** after edits.
Keyword and exact search work without a model and while semantic refresh runs.
The server does not download a model or upload document text for inference.

## Requests and revisions

Every `/api/` request requires `X-IndexWeave-Request: 1`. Mutations and searches
with bodies require `Content-Type: application/json`. Browser requests must be
same-origin. For example, from another terminal on the same machine:

```sh
curl -H 'X-IndexWeave-Request: 1' http://127.0.0.1:8765/api/status
curl -H 'X-IndexWeave-Request: 1' -H 'Content-Type: application/json' -d '{"id":"guide","title":"Guide","text":"A local note.","tags":["notes"],"expected_revision":0}' http://127.0.0.1:8765/api/documents
```

| Method and route                       | Behavior                                                        |
| -------------------------------------- | --------------------------------------------------------------- |
| `GET /api/status`                      | Current revision, live count and semantic state                 |
| `GET /api/documents?after=ID&limit=25` | ID-ordered metadata page, limit 1–100, next cursor              |
| `GET /api/documents/ID`                | Full source text, document revision and captured store revision |
| `POST /api/documents`                  | Create a new ID; refuses an existing ID                         |
| `PUT /api/documents/ID`                | Replace an existing document under the same ID                  |
| `DELETE /api/documents/ID`             | Delete from the live collection                                 |
| `POST /api/search`                     | `query`, `mode` and `limit`                                     |
| `POST /api/semantic/refresh`           | Start one bounded background build at `expected_revision`       |

Create and update take `id`, `title`, `text`, `tags` and `expected_revision`.
Delete takes `expected_revision`. All are strict Pydantic models: unknown
fields, boolean revisions and incorrect types are refused. The engine
additionally checks UTF-8 byte limits, IDs, tags, capacities and the required
global revision. Updates never merge or silently overwrite a stale draft. The
interface keeps a rejected draft for review. Existing-document conflicts require
reviewing the saved version; a new-document draft can explicitly adopt the
current revision, while the server still refuses ID collisions.

Search modes are `text` (default API free-text OR BM25), `strict` (required
terms/phrases/tags), `semantic` and `hybrid`. Existing CLI default behavior
remains strict. Every result identifies its actual captured source revision. A
concurrent later write does not change an already captured result; the UI labels
results by revision and discards pending search replies after its own mutation.

HTTP responses use 422 for invalid input, 404 for missing documents, 409 for
conflicts or stale/unavailable semantic generations, and 503 for
busy/unavailable storage or unknown commit outcomes. Error responses do not echo
submitted text, validation inputs, filesystem paths or tracebacks. A transport
failure or unknown write acknowledgement does not prove a commit was absent.
Preserve the draft, stop the server and inspect the store before deciding to
retry; no automatic repair, reset or blind write retry is performed.

## Cache and worker behavior

The service retains immutable lexical results by current source revision. A
successful mutation invalidates its lexical and semantic caches. The next
lexical query rebuilds from the current snapshot. Semantic refresh captures a
snapshot under the application lock, computes outside that lock on one worker,
and publishes only if the current revision still matches. A build overtaken by
an edit is discarded. Old vectors are never relabelled with a new revision.

States are `unavailable` (no model), `stale`, `building`, `ready` and `error`.
Only one build may be pending/running; additional refresh requests are refused.
Search admission is nonblocking and allows one expensive search at a time.
Encoder builds and queries are serialized, and shutdown waits for its worker.
This is an in-memory cache, not a persisted vector service or a distributed job
queue. Cache construction obeys existing engine bounds; it does not certify an
exact memory ceiling or every possible concurrency interleaving.

## Browser and filesystem boundary

The supplied launcher binds only `127.0.0.1`, permits ports 1024–65535 and
disables proxy-header trust and access logging. Starlette's
[TrustedHostMiddleware](https://starlette.dev/middleware/) and the local
boundary require the exact configured `127.0.0.1:port` or `localhost:port`.
Foreign or null Origins, cross-site/same-site API Fetch Metadata, missing custom
API headers and preflight requests are refused. No CORS permission is granted.
JSON-only bodies and the custom-header pattern follow
[OWASP's AJAX/API CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
The constant header is a browser boundary, not authentication or a secret.

The ASGI boundary limits both declared and streamed bodies to 512 KiB before
Pydantic parses them. Responses set no-store, nosniff, frame denial, no-referrer
and a same-origin-only content security policy. React renders document strings
as text, without HTML execution or Markdown extensions. Static files are served
only from the packaged UI assets; requests cannot choose a workspace, model path
or arbitrary filesystem file.

This is one trusted local user's workspace. Another local process can issue API
requests; malware, hostile same-user processes, reverse proxies, shared hosting,
network/team access, user accounts and multi-tenant document isolation are
outside this scope. These controls are not a production security certification.
Existing [storage trust and plaintext limits](SECURITY.md) still apply.

FastAPI 0.162.0, Pydantic 2.14.2 and Starlette 1.10.0 (MIT) and Uvicorn 0.54.0
(BSD) were reviewed and installed on 4 November 2026. HTTPX2 2.16.0 (BSD)
supplies the maintained Starlette test client path. React/React DOM 19.3.1 and
Vite 8.3.5 (MIT), ESLint 10.14.0 and typescript-eslint 8.72.1 were selected from
current npm metadata. TypeScript 6.0.3 is selected because that lint parser's
published peer range excludes TypeScript 7. Dependency versions are pinned in
the requirement files and npm lock; no vulnerability-scan result is claimed.
