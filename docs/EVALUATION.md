# Storage and lexical verification

The installed storage package was exercised with synthetic documents, private
local temporary workspaces and controlled failure injection. This evaluates the
storage interface; it provides no search-quality, model-inference or end-user
web application results.

## Reproducible evidence

The
[source verification run](https://github.com/sajan69/index-weave/actions/runs/41508679734)
at
[7f1080a](https://github.com/sajan69/index-weave/commit/7f1080a0ef90ec509db7a0f84da276f42d3a025a)
passed the quality job and four installed-wheel storage jobs on 3 November 2026
(Brisbane date). Each storage job built and installed a wheel, checked
dependency consistency, ran all 40 tests and executed
`examples/storage_demo.py`.

| Runner | Python series | Observed outcome                    |
| ------ | ------------- | ----------------------------------- |
| Ubuntu | 3.12          | 40 tests and storage example passed |
| Ubuntu | 3.13          | 40 tests and storage example passed |
| Ubuntu | 3.14          | 40 tests and storage example passed |
| macOS  | 3.12          | 40 tests and storage example passed |

The four jobs produced the same deterministic example result. The recorded
[JSON fixture](../examples/storage-demo.json) identifies the Ubuntu/Python 3.12
source job and preserves its actual result. Later CI runs compare the repeated
example against that result; they are separate executions, not the source of
this record. No test duration is presented as a storage throughput or latency
benchmark.

The example starts with two documents in revision 1, then updates `guide` and
deletes `draft` together in revision 2. The revision-1 snapshot stays unchanged.
After closing the writer, the example appends 70 bytes of an incomplete third
frame. A strict reader refuses it; an explicitly token-matched repair restores
revision 2 and the single remaining ID `guide`.

The repaired log is 554 bytes, with SHA-256
`2ce6cdbe7155faa4d6c65b2f15539cdb0c151962673faa3bf051523c6eed0556`. This is a
fingerprint of fixed synthetic format bytes, not publisher authentication or a
fingerprint of real user data. The before/after inspection objects match. Run it
independently with the installed package:

```sh
python examples/storage_demo.py
```

## What the tests establish

The 40 test methods comprise 8 codec, 3 CLI, 2 capacity, 8 path/input, 6
process/thread, 12 storage and 1 Hypothesis state-machine test. Repeating the
suite on four runners is not 160 independent test designs or a coverage
percentage.

| Scenario                                                                                   | Observed assertion                                                                                                                  |
| ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| Hand-built 191-byte frame                                                                  | Fields, checksums, payload and replayed record agree with the format contract                                                       |
| All 190 nonempty incomplete cuts of that frame                                             | Only the complete validated prefix is reported; no partial transaction is applied                                                   |
| Selected complete header/payload/trailer bit changes and invalid transaction schemas       | Corruption is refused rather than classified as a successful commit or silently repaired                                            |
| Batch update/delete and old snapshots                                                      | One revision per batch; changed documents receive that revision; old immutable snapshots retain their earlier records               |
| Short writes and an interrupted write syscall                                              | One complete frame is reconstructed without duplicate bytes                                                                         |
| Injected append, fsync or gate-release failure                                             | Unknown outcome poisons the writer; a complete unacknowledged frame may be visible after reopening                                  |
| Failed repair synchronization                                                              | Fresh inspection is required; truncation may already have happened                                                                  |
| Abrupt child exits before append, after 70 bytes and SIGKILL after fsync                   | Clean prior state, explicit incomplete-tail refusal, or a complete unacknowledged commit respectively; OS releases the writer lease |
| Separate writer processes and an exclusive access gate                                     | Second writer or concurrent reader is refused where required; an idle writer still permits independent snapshots                    |
| Eight threads using the same expected revision                                             | One mutation succeeds, seven report revision conflicts                                                                              |
| 10,000 live documents                                                                      | The next distinct document is refused without appending                                                                             |
| 510 documents with 64 KiB text each                                                        | A further 15-document batch exceeding the live-byte budget is refused atomically                                                    |
| Unsupported platform, symlinks, hard links, FIFO, loose permissions and unexpected entries | Workspace access is refused; an oversized sparse log is rejected before a data read                                                 |

The state-machine test uses Hypothesis 6.170.14 with a dictionary reference
model, up to 40 generated examples and 35 stateful steps per example,
deterministic settings and no retained example database. It composes puts,
deletes, reopen operations and snapshot comparisons. Those are configuration
bounds, not a claim that every run executes 1,400 distinct operations or
explores all possible histories.

Portalocker 4.4.2 is the runtime dependency. Ruff 0.17.3, Prettier 3.9.10 and
markdown-link-check 3.15.0 provide the lint/format/link checks in the quality
job. The source run checked five documentation links; this document adds its own
source and fixture links to subsequent runs. Python dependencies and Node
development tools are pinned in the repository. The local verification used an
installed wheel on Python 3.12.14; the same 40 tests, example, dependency check,
formatting and link checks passed. Local success is not substituted for the
separate CI executions.

## Limits

These are bounded correctness and process-failure checks on local filesystems.
They do not simulate hardware power removal, dishonest flush acknowledgements,
network filesystems, every possible corruption or interleaving, hostile host
administrators, production workloads or Windows support. Checksums cannot detect
removal of a complete valid suffix without external evidence. Deleted text
remains in the append log. The storage-only run did not exercise lexical
retrieval; those checks are described below. There is no compaction, semantic
model, HTTP server or browser workflow in either result set. No retrieval
accuracy, speedup, production adoption, physical durability certification or
business impact is claimed.

## Lexical search correctness

The lexical implementation at
[`fb24f14bd134a6b17c3f6ef846ac17772b3eec22`](https://github.com/sajan69/index-weave/commit/fb24f14bd134a6b17c3f6ef846ac17772b3eec22)
was verified by
[CI run 41557070029](https://github.com/sajan69/index-weave/actions/runs/41557070029)
on 3 November 2026. All five jobs passed: quality checks and installed-wheel
checks on Ubuntu with CPython 3.12, 3.13 and 3.14, and macOS with CPython 3.12.
Each wheel ran 52 test methods: the 40 storage methods above and 12 lexical
methods. Repeating the methods across environments does not make them 208
different test designs. The quality job passed Ruff lint/format, Prettier and 13
documentation links, including external references without suppressed errors.

The 12 lexical methods cover:

- Unicode normalization with original offsets, fixed-pattern tokenization and
  source/token limits, and bounded query grammar rejection.
- Required terms and tags, repeated-term phrases, within-field adjacency and
  prevention of title/body phrase joins.
- A three-document hand calculation of positive-IDF BM25, including a token-free
  document and unchanged corpus statistics after filtering.
- Source spans, plain-text passages, replacement/deletion after reopening, old
  snapshot stability, immutable nested postings, total counts before limits and
  deterministic ties.
- Index-budget guards using reduced constants, invalid result limits and an
  installed CLI search that leaves the log bytes unchanged.
- A linear-scan reference for phrase/term/tag matches and independently computed
  document statistics/scores on Hypothesis-generated ASCII word corpora. The
  test settings cap generation at 60 examples, each containing 1–12 documents
  with 1–12 words. Four fixed query forms exercise that corpus. This is not a
  representative language benchmark or a relevance-labelled dataset.

The reduced-budget tests exercise refusal without constructing a million-token
fixture. The tokenizer test does construct the 32,768-token field boundary.
Neither case establishes a production memory or latency limit. The local CPython
3.12.14 installed wheel also passed all 52 methods; local execution and CI are
separate runs. The package contains ten Python modules, including the three
lexical modules. No editable-install-only result is used as package proof.

### Worked fictional collection

[The recorded summary](../examples/search-demo.json) comes from the successful
Ubuntu CPython 3.12 job `125658431506` in that source run. The full JSON output
is reproducible with `python examples/search_demo.py`; CI retains it alongside
the wheel as a seven-day artifact. The example creates only a fresh temporary
workspace with four fictional notes, then replaces one and deletes another.

| Query at revision 1         | Returned IDs in order |
| --------------------------- | --------------------- |
| `"write ahead" tag:storage` | `journal`             |
| `"phrase search"`           | `index`               |
| `recovery`                  | `draft`, `journal`    |
| `tag:storage`               | `draft`, `journal`    |

Revision 1 contains four documents, 35 token occurrences and 29 distinct terms.
After replacing `journal` and deleting `draft`, revision 2 contains three
documents, 26 occurrences and 23 distinct terms. A new search for `recovery`
returns no matches, while the retained revision-1 index still returns two. The
new query `durable` returns the updated `journal`. These are explicit mechanical
expectations for authored text, not human assessments of useful relevance.

The canonical live-content digests are:

```text
revision 1: 86a6e4b5fcbacff0f7a1be7a7bbe0edd099b38b82122c3acba8ecdc243d28c78
revision 2: c6b9a62ed48b95a182cc557529e3f0399bd4003870e8dfb62f0d6270f2f9f650
```

All four source-run environments produced those digests and result IDs. Their
recorded Python normalization Unicode versions differed: 15.0.0 on Python 3.12,
15.1.0 on 3.13 and 16.0.0 on 3.14. Agreement on this fictional text does not
prove cross-version agreement for every Unicode input. The pinned `regex`
version was 2026.9.29 throughout.

There are no held-out relevance judgments, Recall@k/nDCG@k results, semantic or
hybrid retrieval results, model provenance, search latency measurements or
representative scale results in this evidence. That lexical-only run built the
in-memory index from a strict storage snapshot; it did not exercise persisted
segments, an HTTP API or a browser workflow. See [search semantics](SEARCH.md)
for the exact query, scoring, resource and source-offset contracts.
