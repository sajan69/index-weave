# Storage verification

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
remains in the append log. There is no compaction, lexical index, semantic
model, HTTP server or browser workflow in these results. No retrieval accuracy,
speedup, production adoption, physical durability certification or business
impact is claimed.
