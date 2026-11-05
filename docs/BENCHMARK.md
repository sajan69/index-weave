# Bounded runtime measurements

[Source run 41945256799](https://github.com/sajan69/index-weave/actions/runs/41945256799)
at commit `1e79629e1afd3218913a1449ca669f9b387353f6` passed all eight jobs.
CPU job `126976201232` completed five independent sequential worker processes.
The [raw record](../examples/runtime-record.json) preserves every worker,
query observation, workload identity, environment and recomputable summary.

## Workload and method

The unchanged licensed HotpotQA projection contains 1,984 complete paragraphs,
including distractors. Each worker imports them in batches of 100 into a fresh
private source store, then measures replay, lexical construction, derived-index
creation/loading, verified model loading and semantic construction separately.
The model and dataset are explicitly acquired before measurement; see
[semantic setup](SEMANTIC.md) and [dataset provenance](RETRIEVAL.md).
Existing limits remain enforced, with no document exclusions or favourable
replacement runs.

Each mode executes one untimed first-query warmup followed by all 100 frozen
development queries in their original order. Text, semantic and complete hybrid
calls are measured separately with `perf_counter_ns`. Hybrid timing includes
both retrieval methods and fusion. Calls include result assembly but exclude
HTTP transport, browser rendering and JSON serialization. No holdout tuning or
ranking changes were performed.

Reproduce on Linux, after the documented data/model preparation and installed
semantic dependencies:

```sh
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  .venv/bin/python scripts/benchmark_runtime.py artifacts/evaluation-data \
  artifacts/minilm artifacts/bounded-runtime
```

The destination must not exist. Failed phases are retained by name/error type;
missing or failed repetitions prevent a successful summary. The coordinator
does not retry or reset failed destinations. CI retains the raw JSON in the
`bounded-runtime-cpu` artifact for seven days; source/index plaintext files are
not uploaded by that artifact.

## Observed results

Phase values below are medians across five process observations, rounded to
three decimal seconds. Full integer nanoseconds are in the raw record.

| Phase                                                   | Median seconds |
| ------------------------------------------------------- | -------------: |
| Import including fsync                                  |          0.035 |
| Source replay                                           |          0.064 |
| Lexical construction                                    |          0.762 |
| Derived-index creation, including its own lexical build |          2.993 |
| Index loading and validation                            |          2.670 |
| Verified model loading                                  |          3.458 |
| Semantic construction                                   |         42.922 |

Query summaries remain separate per process and mode. Each process has 100
observations per mode. The median averages the two middle observations; p95 is
the 95th sorted observation. The ranges below span those five separate
summaries, not a pooled or averaged percentile.

| Mode     | Per-worker median range (ms) | Per-worker p95 range (ms) |
| -------- | ---------------------------: | ------------------------: |
| Text     |                26.019–27.527 |             42.977–45.235 |
| Semantic |                22.199–27.015 |             23.787–29.859 |
| Hybrid   |                64.076–69.233 |             81.952–89.268 |

The source log was 1,408,628 logical bytes; live canonical document bytes were
1,356,700. The referenced segment was 16,365,839 bytes and CURRENT was 444 bytes;
two lock files were empty. Numeric vector payload was 3,491,328 bytes for 2,273
windows of 384 float32 dimensions. These counts exclude filesystem allocation,
Python objects, model weights, tokenizer state and construction temporaries.
Final process lifetime peak RSS ranged from 968,450,048 to 1,001,259,008 bytes.
This includes the loaded model, all retained indexes and earlier temporary
peaks; it is not isolated index memory or an exact capacity ceiling.

The source runner reported Linux x86-64, CPython 3.12.16 and four logical CPUs.
BLAS/OpenMP and PyTorch inference used two threads. Model revision, all asset
identity information and installed numerical/inference package versions are
retained per worker. Independent scalar recomputation from all raw observations
matched the recorded medians and p95 values.

## Interpretation and limits

Fresh processes do not imply cold disk caches or isolated hardware. Short
Wikipedia paragraphs and one shared runner do not represent every technical-note
collection or document-size distribution. Results do not establish production
latency guarantees, throughput, a speed improvement or HTTP service latency.
The existing labelled relevance evaluation is separate from this runtime test.

Linux `ru_maxrss` is lifetime peak resident memory in KiB, converted to bytes;
see [getrusage](https://man7.org/linux/man-pages/man2/getrusage.2.html).
Checkpoints are not additive component allocations. Nanosecond timing units do
not imply nanosecond accuracy; see the Python
[performance counter documentation](https://docs.python.org/3.12/library/time.html#time.perf_counter_ns).
