# Engine verification

The sections below distinguish synthetic engine verification, public-corpus
retrieval evaluation and installed browser workflows. Each result identifies its
source commit and execution environment.

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
retrieval; those checks are described below. Neither of those two source runs
exercised derived-segment compaction, a semantic model, an HTTP server or a
browser workflow. No retrieval accuracy, speedup, production adoption, physical
durability certification or business impact is claimed.

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

## Persistent segment verification

The persistent implementation at
[`5ee0fc0fded27afa6e97fb44471e877a070de582`](https://github.com/sajan69/index-weave/commit/5ee0fc0fded27afa6e97fb44471e877a070de582)
was exercised in
[CI run 41595935577](https://github.com/sajan69/index-weave/actions/runs/41595935577)
on 3 November 2026. All five jobs passed: quality checks and installed wheels on
Ubuntu with CPython 3.12, 3.13 and 3.14, and macOS with CPython 3.12. Each wheel
passed 72 methods: 52 storage/lexical methods and 20 segment methods. These are
72 test designs repeated across four environments, not 288 distinct designs. The
quality job passed Ruff lint/format for 27 Python files, Prettier and 23 links
without treating network errors as success. The wheel contains thirteen Python
modules. No new runtime dependency was added for segment persistence.

The segment methods exercise persisted posting loads with the tokenizer patched
to fail, incremental analysis of only two changed documents, replacement and
tombstone filtering, compaction and cleanup, old in-memory view stability,
source-log byte equality and the source revision/CAS boundary after compaction.
They also cover generation conflicts, rollback and same-revision content
mismatch, idempotent operations, complete corruption refusal, every truncated
prefix of a small manifest, checksum-valid invalid postings and source offsets,
duplicate/noncanonical/nonfinite/deep JSON, runtime provenance mismatch,
private-file and path guards, existing-directory preservation and reduced-budget
refusal. The reduced budgets test guards; they are not full-capacity load tests.

Failure injection covers partial segment writes, each of four publication sync
boundaries and namespace replacement failure. The expected active generation is
checked after each failure. Before replacement, reads use the old manifest;
after replacement and a failed final directory sync, the new manifest can be
visible despite `CommitUnknown`. Explicit cleanup removes abandoned files only
when the active chain validates. Corrupt active data is never bypassed by
selecting an earlier generation.

Real subprocess tests exit with code 31 immediately before manifest replacement
or receive SIGKILL immediately after the actual replacement. Additional child
processes hold shared reader gates or exclusive writer/gate leases. Compaction,
cleanup and conflicting readers/writers are refused while those locks require
it. Two refresh processes released from a barrier produce exactly one published
generation and one refused attempt. These tests demonstrate those interleavings
and process-exit outcomes, not all possible schedules or physical power loss.

A Hypothesis reference test caps generation at 20 examples of 1–12 document
changes, with periodic compaction and cleanup. After each refresh it compares
search results to an independently rebuilt in-memory lexical index. That
reference already has the separate linear-scan/scoring checks described above.
The segment test does not treat its own serialized bytes as a relevance oracle.

### Worked persistent collection

[The recorded result](../examples/segments-demo.json) is the decoded result of
Ubuntu CPython 3.12 job `125783180783` in that successful source run. Reproduce
it with `python examples/segments_demo.py`. The script uses a new temporary
source workspace and a separate temporary derived index, both containing only
fictional text. It creates two documents, replaces one, deletes one and adds
another, then refreshes, compacts, vacuums and reopens the index.

| Observation                                     | Actual result |
| ----------------------------------------------- | ------------- |
| Index generations                               | 1 → 2 → 3     |
| Referenced segments                             | 1 → 2 → 1     |
| Source revision after refresh and compaction    | 2             |
| Referenced segment file bytes before compaction | 2,308         |
| Referenced segment file bytes after compaction  | 1,233         |
| Unreferenced files removed by explicit vacuum   | 2             |
| Remaining IDs                                   | `a`, `c`      |
| Live token occurrences / distinct terms         | 14 / 11       |

Byte counts sum complete referenced segment files, including their framing and
checksums. They exclude the manifest, lock files, unreferenced files, filesystem
allocation units, process memory and the source log. They are deterministic
observations for this tiny JSON-format fixture, not an average compression
ratio, storage-capacity estimate or latency/throughput improvement. All four
source-run environments produced these counts and the final live-content digest:

```text
2f1725992375c36c10d81cbae1f63348803568c5deabf501d6bbda7ec868b9df
```

The script verifies stale-generation refusal, equality with a rebuilt search
oracle, retained old-view results and byte-for-byte unchanged source-log data.
The recorded CPython 3.12 normalization Unicode version is 15.0.0; the 3.13 and
3.14 jobs recorded 15.1.0 and 16.0.0. Every job built its own compatible index.
The result does not show cross-version reuse of the same index files; the format
intentionally refuses mismatched analyzer provenance.

The local CPython 3.12.14 installed wheel separately passed all 72 methods and
completed the same example. That segment-only run did not exercise source-log
checkpoints, physical power loss, representative scale, relevance-labelled
semantic/hybrid retrieval or end-user browser workflows.
[Persistent index semantics](SEGMENTS.md) states the publication, cleanup,
source-snapshot and filesystem limits.

## Source-checkpoint verification

The checkpoint implementation at
[`aabac0baed8fee5cf6cbfeb897bddfd4db2afa61`](https://github.com/sajan69/index-weave/commit/aabac0baed8fee5cf6cbfeb897bddfd4db2afa61)
was exercised by
[CI run 41643334038](https://github.com/sajan69/index-weave/actions/runs/41643334038)
on 3 November 2026. All five jobs passed: quality checks and installed wheels on
Ubuntu with CPython 3.12, 3.13 and 3.14, and macOS with CPython 3.12. Each wheel
passed 92 methods: the preceding 72 and 20 checkpoint methods. Repeated runs
across environments are not 368 different test designs. The quality job passed
Ruff lint/format for 32 Python files, Prettier and 35 links. The inspected wheel
contains fifteen Python modules, with no additional runtime dependency.

The checkpoint methods cover an independently hand-built byte fixture,
truncation at every prefix of that fixture, selected complete bit flips and
missing commit footer, checksum-valid semantic errors, empty and maximum
revisions, actual 201-record count packing and 21 large-record byte packing,
sequential transactions after the checkpoint, every incomplete prefix of one
following transaction, encoder accounting and reduced-budget guards, and strict
JSON/depth/value/UTF-8 checks.

Integration methods verify source byte equality, old snapshot stability, global
and per-document revisions, compare-and-set writes after compaction,
compatibility with an existing derived index, stale expected revisions,
existing/nested target refusal, source writer-lease refusal, unrepairable
incomplete bases, explicit repair only after a complete base, corrupt source
refusal and the installed CLI. A dictionary reference test caps generation at 20
examples of 1–10 document changes, with repeated compaction every second change.
It compares live text, each document's last-put revision, global revision and
content digest after reopening; these are mechanical state checks, not relevance
judgments.

Fault injection fails each of the five destination initialization sync calls:
two lock files, the log file, the new directory and its parent. The original
source bytes remain equal. A complete but unsynchronized copy can already be
readable after a later sync failure; that observation is not a hardware
durability guarantee. Partial targets are never reset on retry.

Actual child processes exit with code 31 before checkpoint bytes are written,
with code 32 after a 70-byte partial write, or receive SIGKILL immediately after
the real checkpoint-file sync. The expected exit codes are checked. Partial
bases are refused; a complete but unacknowledged copy can be read, and the
source remains unchanged in each case. No physical power removal, faulty
controller, network filesystem or exhaustive concurrency proof is claimed.

### Worked checkpoint copy

[The recorded result](../examples/compaction-demo.json) comes from successful
Ubuntu CPython 3.12 job `125942134731` in that source run. Reproduce it with
`python examples/compaction_demo.py`. All input text is fictional, and all
workspaces are new private temporary directories. The script writes three
initial documents, replaces one five times, then replaces it once more and
deletes another document in revision 7.

| Observation                                               | Actual result         |
| --------------------------------------------------------- | --------------------- |
| Original version-1 log bytes                              | 8,004                 |
| New version-2 checkpoint log bytes, before a later append | 643                   |
| Preserved global revision                                 | 7                     |
| Live document revisions                                   | `guide`: 7, `keep`: 1 |
| Live document count                                       | 2                     |
| New copy's revision after an independent append           | 8                     |
| Original's revision after that append                     | 7                     |

The file-byte counts include complete log headers, metadata, record chunks,
checksums and commit markers. They exclude locks, directory allocation, memory,
indexes and filesystem allocation units. The original 8,004-byte file is kept;
the copy operation initially adds storage rather than freeing it. This small
history-heavy fixture does not establish a general size reduction, capacity,
latency, throughput or compression ratio. An empty source can produce a larger
checkpoint because of framing overhead.

All four source-run environments produced the same 643-byte checkpoint with
SHA-256:

```text
d9121bf417b0100c1935c518d37abdf2de6e2a9fd931614a4cd15a9572884095
```

The preserved live-content digest is:

```text
ecb35b2e47f1cd2d1818a00b286cbf32462be8f16c8adb0fcb6e733cdc5000f2
```

The existing derived index returned the same result against the compacted source
before its later independent write. The old in-memory snapshot still contained
the deleted document, while the checkpoint did not. Version-1 storage examples
also continued to match their original recorded byte-format fixture in every
job; ordinary creation and opening do not silently migrate old workspaces.

The local CPython 3.12.14 installed wheel separately passed all 92 methods and
completed the same example. There are still no labelled semantic/hybrid
retrieval results, model inference, representative-scale measurements, HTTP API
or end-user browser workflows in this evidence. See the exact
[checkpoint contract](CHECKPOINTS.md), including independent-fork semantics and
failure limits.

## Public retrieval input verification

The [fixed protocol](RETRIEVAL.md) was committed before inference or relevance
measurement. The [source audit](../examples/retrieval-sources.json) records the
public dataset revision, declared licences, model assets and four downloaded
configuration hashes. Model weights were not downloaded or executed by that
audit. The model configuration uses maintained Transformer, mean Pooling and
Normalize components, with 384 dimensions and a 256-token sentence limit.

The [recorded data result](../examples/retrieval-data.json) comes from
successful Ubuntu CPython 3.12 data-preparation job `126144783322` in
[source run 41701246266](https://github.com/sajan69/index-weave/actions/runs/41701246266)
at commit `b543896c964458823e6cb6b0e8ca9a566db21818`. That source run had a
separate JSON formatting failure; the data job itself completed successfully. It
installed PyArrow 26.0.1, downloaded only the pinned public validation parquet,
verified its length and SHA-256, and parsed it without the answer column. All
selected contexts and supporting annotations passed the frozen projection rules;
no selected question was replaced or excluded.

- Validation rows checked: 7,405.
- Downloaded file bytes: 27,452,575.
- Selected questions: 100 development and 100 holdout.
- Distinct complete title/paragraph units: 1,984, including distractors.
- Dataset revision: `1908d6afbbead072334abe2965f91bd2709910ab`.
- Download SHA-256:
  `c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6`.
- Projection SHA-256:
  `6f9f7ace0909bd28179a253984f050dc56de92b3e2a4d7c7fee546e789c8ba56`.

The data record freezes the ordered question IDs and split without copying
question text or passages into the repository. The generated corpus carries
HotpotQA/Wikipedia attribution, CC BY-SA 4.0 and transformation notices. The CI
artifact retains only its summary and source audit for seven days. Use the
preparation command in the protocol document to reproduce the corpus locally;
existing destinations are refused.

The installed wheel passed 107 distinct test methods on Ubuntu Python 3.12, 3.13
and 3.14 and macOS Python 3.12 in that source run. These include the prior 92
core methods and 15 new synthetic projection, metric, source-audit and
acquisition-guard methods. The same designs are repeated across environments.
The new tests include hand-calculated rank discounts and full relevance
denominators, deterministic ID selection, retained distractors, invalid
annotations, byte/hash mismatch refusal and non-overwrite guards. Local CPython
3.12.14 separately passed all 107 methods from an installed wheel.

This verifies acquisition, provenance, projection and metric mechanics. It is
not a retrieval-quality, model-inference, latency or capacity result. No neural
ranking scores, held-out errors, AI improvement, representative workload
measurements or browser/API outcomes are reported here.

## CPU semantic and hybrid retrieval

[Source run 41751933815](https://github.com/sajan69/index-weave/actions/runs/41751933815)
at commit `9fc372ede0fb5049927069cc9b52d77fbb3e238b` passed all seven jobs. The
actual CPU inference/evaluation job was `126318834493`. The
[recorded result](../examples/retrieval-evaluation.json) contains its model
asset SHA-256 hashes, dependency/environment versions, aggregate outcomes,
paired-query counts and selected improved/worsened cases. The
[complete per-query metrics](../examples/retrieval-query-metrics.json) preserve
all 200 question IDs, metrics and paired nDCG differences. They are measured
outputs, not synthetic labels or manually chosen expected scores.

The precommitted `hotpot-pool-v1` protocol retained every selected question: 100
development and 100 holdout queries against 1,984 complete title/paragraph units
from the public dataset, including distractors. Supporting-fact annotations
supplied binary document qrels. The engine's actual source store held the corpus
in 20 committed batches. Its live-content digest was
`0bda9873680fe69562abcd26d4a3eff5f8153e97d575a85df2ef7e7838bed9aa`. The semantic
index contained 2,273 windows and 384-dimensional normalized float32 vectors. No
model, weight, fusion constant or query selection was tuned from these outcomes.

| Split       | Method   | Recall@10 | nDCG@10 | All support at 10 |
| ----------- | -------- | --------: | ------: | ----------------: |
| Development | Lexical  |    0.9150 |  0.7999 |              0.83 |
| Development | Semantic |    0.8450 |  0.7588 |              0.71 |
| Development | Hybrid   |    0.9050 |  0.8090 |              0.82 |
| Holdout     | Lexical  |    0.9000 |  0.7897 |              0.80 |
| Holdout     | Semantic |    0.8700 |  0.7786 |              0.75 |
| Holdout     | Hybrid   |    0.9350 |  0.8128 |              0.87 |

The table rounds recorded full-precision values. All-support is the fraction of
queries retrieving every annotated supporting paragraph. Semantic search alone
scored below lexical search on both mean metrics in both splits. Hybrid had the
highest holdout means, but its development recall and all-support rate were
below lexical. These results support retaining the lexical option rather than
assuming a neural or fused ranking always helps.

For holdout nDCG, semantic improved 38 queries, worsened 41 and tied 21 against
lexical; hybrid improved 45, worsened 23 and tied 32. Development counts were
33/45/22 for semantic and 35/30/35 for hybrid. These are paired query counts,
not percentages of users or production impact.

Two inspectable holdout cases illustrate the tradeoff:

- `5a8f541e5542992414482a53`: annotated paragraphs were at lexical ranks 1 and
  3, semantic ranks 11 and 89, and hybrid ranks 4 and 18. Hybrid nDCG@10 fell
  from 0.9197 to 0.2641 and lost one supporting paragraph from the top 10.
- `5abcf46255429959677d6b6b`: lexical ranks 4 and 12 and semantic ranks 1 and 11
  became hybrid ranks 1 and 7. Hybrid retrieved both supporting paragraphs and
  increased nDCG@10 from 0.2641 to 0.8175.

These cases are the most negative and positive holdout hybrid nDCG differences,
with ID tie breaking, not a representative qualitative user study. The source
artifact contains all document ranks and qrels. Independently recomputing binary
Recall@10 and nDCG@10 from all 200 archived rankings reproduced every reported
per-query metric within `1e-12`.

### Runtime and reliability evidence

The source job used Ubuntu x86-64, CPython 3.12.16, NumPy 2.5.4, PyTorch
2.15.0+cpu, Sentence Transformers 6.2.0, Transformers 5.22.1, tokenizers 0.23.3
and safetensors 0.8.0. It reported four logical CPUs; inference explicitly used
two PyTorch threads and CI limited BLAS/OpenMP threads to two. The original
safetensors file was 90,868,376 bytes and passed its pinned SHA-256 check. All
eleven local assets were verified before inference. Asset hashes and installed
package versions are retained in the recorded result.

One observed run took 0.920 seconds to build the lexical index and 50.573
seconds to encode/build the semantic index, excluding package installation,
downloads and initial model loading. These are single shared-runner observations
without repeats or controlled hardware isolation. Raw per-query seconds in the
artifact include model query encoding, source identity checks, scoring and
result assembly. They are not production latency targets, representative-scale
benchmarks or evidence of a speed improvement. CLI semantic search rebuilds
vectors per invocation, so its startup cost remains a practical limitation.

The fictional actual-model example loaded and queried with socket connections
blocked, refused a stale snapshot, preserved an old view, rebuilt after an
update/delete and executed the installed semantic CLI without changing source
log bytes. Its assertions concern consistency and offline mechanics, not human
relevance. All 116 distinct test methods passed on Ubuntu Python 3.12, 3.13 and
3.14 and macOS Python 3.12; the same methods also passed in the CPU inference
job. The nine added methods use an explicitly fake encoder for scalar-cosine,
chunking, immutability, vector/input guards, snapshot and fusion tests. The real
encoder smoke and 200-query comparison ran only in the Linux CPU job.

The raw evaluation, model manifest and source summary are retained in CI's
`retrieval-evaluation-cpu` artifact for seven days. Reproduce them using
[the setup and comparison commands](SEMANTIC.md). Public text and model weights
are downloaded explicitly and are not committed here. English Wikipedia-based
paragraph retrieval in a pooled distractor corpus is narrower than
full-Wikipedia or end-user collection search. Incomplete relevance judgements
and possible pretraining overlap limit interpretation. There is no model
training, prospective/contamination-free evaluation, adoption claim,
HTTP/browser result or representative-scale reliability claim in these
measurements.

## Installed local interface

[Source run 41792797105](https://github.com/sajan69/index-weave/actions/runs/41792797105)
at commit `acce499971a398e2980387cb6a53c322a75f333c` passed all eight jobs on 4
November 2026. The
[interface evidence record](../examples/interface-verification.json) identifies
the source jobs, browser viewports and inspected screenshot hashes. The built
React assets were downloaded into each wheel build; browser tests launched the
installed `indexweave-serve` command against a disposable fictional collection.
They exercised the packaged interface, not a frontend mock server.

All 126 distinct unit test methods passed on Ubuntu CPython 3.12, 3.13 and 3.14,
macOS CPython 3.12, and the separate Linux CPU/model job. Ten added methods
cover strict API validation, global-revision conflicts, collision/missing-ID
guards, writer leasing, declared and streamed body limits, Host/Origin/Fetch
Metadata refusal, query admission, sanitized failures and poisoned unknown write
outcomes. Two of those ten use an explicitly fake encoder with controlled worker
events to check discarded obsolete builds and failed-build lexical availability.
They are mechanical consistency checks, not model-quality evidence. The
installed local wheel separately passed all 126 methods on CPython 3.12.14.

The Chromium/Playwright 1.64.1 browser job (`126450100264`) passed three
workflows:

- Create, exact phrase/tag search, concurrent-tab conflict with the unsaved
  draft retained, explicit reload, save with editor controls paused, and
  deletion.
- Strict UTF-8 Markdown import, inert script-like source text, byte-for-byte
  download preserving a BOM and CRLF, and a refused foreign-origin mutation.
- Invalid UTF-8 refusal and a 390 × 844 mobile viewport without horizontal
  overflow or external browser requests.

The CPU/model job (`126450100150`) repeated those workflows and passed a fourth
using the actual verified MiniLM model. It built a semantic generation, searched
with hybrid ranking, edited the document, observed stale state, explicitly
rebuilt, and retrieved the updated source passage. This demonstrates local model
integration and invalidation; it does not supply new human relevance labels or
alter the frozen retrieval protocol above. Four workflows repeated in two jobs
are four designs, not seven distinct scenarios.

The desktop (1440 × 1000 viewport) and mobile full-page screenshots were
visually inspected for readable controls, source text, panel layout and
overflow. The `indexweave-browser` artifact retains them for seven days. The
quality job passed Ruff, Prettier, ESLint, TypeScript checking, the Vite
production build and all 66 documentation links. API and browser setup is
documented in [the local interface guide](API.md). These synthetic functional
checks do not establish accessibility conformance, every browser engine, a user
study, network/team deployment security, production throughput or
representative-scale memory/latency bounds.

## Repeated bounded runtime measurements

The [five-process benchmark](BENCHMARK.md) records the complete public paragraph
workload, raw observations, timing stages, index sizes and process memory
limits. Its in-process timings do not measure HTTP or browser latency.
