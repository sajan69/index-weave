# Offline embedding retrieval

The engine supports an optional pretrained CPU encoder, exact passage-vector
search and fixed reciprocal-rank fusion with free-text BM25. The storage and
positional lexical core remain custom. Sentence Transformers, Transformers,
PyTorch, safetensors and NumPy provide model loading, inference and numerical
operations. There is no model training or generative answering.

## Setup and use

Use the repository checkout and its installed package on a trusted POSIX local
filesystem. The optional requirements are separate from the offline storage CLI.
For the evaluated Linux CPU configuration:

```sh
.venv/bin/pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements-semantic.txt
.venv/bin/pip check
mkdir -p artifacts
.venv/bin/indexweave model-fetch artifacts/minilm
.venv/bin/indexweave search collections/example --mode text --query 'where is recovery documented?'
.venv/bin/indexweave search collections/example --mode semantic --model artifacts/minilm --query 'where is recovery documented?'
.venv/bin/indexweave search collections/example --mode hybrid --model artifacts/minilm --query 'where is recovery documented?'
```

`model-fetch` is an explicit network operation for eleven pinned public assets
of `sentence-transformers/all-MiniLM-L6-v2`, revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, declared Apache-2.0. It refuses an
existing destination. Downloads are bounded by expected size, checked against
pinned Git blob or LFS digests, and recorded with actual SHA-256 hashes in
`MODEL-MANIFEST.json`. A failed acquisition may leave a partial new directory;
it is never reset or reused automatically. No document is sent to the model
publisher or another inference service.

Search verifies every local asset before loading. Only the fixed Transformer,
mean Pooling and Normalize configuration is accepted; custom module paths,
remote code, pickle weights and unexpected cache entries are refused. The loader
sets `local_files_only=True`, `trust_remote_code=False`, `token=False` and
safetensors-only weights. Model loading and query inference are offline. Use
only a dedicated private model directory and trusted parents. Checksums and file
modes are not authentication or a hostile-process filesystem sandbox.

The direct dependency versions were reviewed on 4 November 2026: Sentence
Transformers 6.2.0 and Transformers 5.22.1 (Apache-2.0), safetensors 0.8.0
(Apache software licence), NumPy 2.5.4 (BSD and bundled permissive notices), and
PyTorch 2.15.0 (its published compound permissive licence expression).
[The official CPU wheel index](https://download.pytorch.org/whl/cpu/torch/)
supplies the CPU build; other packages come from PyPI. These optional packages
have a substantially larger installation/runtime cost than the storage core.
Exact installed versions are included in retrieval evidence; no dependency
vulnerability certification is claimed.

## Query modes and scores

The existing default `--mode strict` retains required terms, phrases and tag
filters. `--mode text` analyzes ordinary question text into a bag of unique
normalized terms and retrieves documents containing any term. It uses the same
global BM25 formula and source fields as strict search. It does not interpret
question punctuation as query-language operators. Text queries require 1–128
lexical tokens and at most 2,048 UTF-8 bytes.

Semantic search uses title prefixes of at most 32 tokenizer wordpieces and body
windows of 192 wordpieces with stride 160. Windows stop after complete body
coverage. Token offsets refer to original Python code points; there is no HTML
generation or JavaScript UTF-16 offset claim. The actual joined title, newline
and body slice is checked against 256 model tokens including special tokens.
Overlong passages and queries are rejected, never silently truncated.

Vectors are normalized CPU float32 with 384 dimensions. The exact dot product of
normalized vectors is cosine similarity. Nonfinite, zero, wrongly shaped or
nonfloating encoder output is refused. A document's score is its maximum passage
cosine; ties use ASCII document ID. Every live document is considered, including
negative-scoring documents. Scores are not relevance probabilities. Returned
passages are original text slices with source offsets and document revision. For
long documents, only the best retained window is returned.

Hybrid search fuses the first 100 document ranks from each method with equal
weight: sum `1 / (60 + rank)`, using zero for absent ranks and ID for ties. Its
match count describes this candidate union, not every possible relevant
document. The returned score is the fusion score and both input ranks are
included. It does not compare cosine and BM25 values as interchangeable units.

## Snapshots and bounds

`SemanticIndex.from_snapshot(snapshot, encoder)` builds an immutable in-memory
index. Search requires an expected source snapshot and checks its revision and
canonical content digest. Model revision, verified asset digest, dependency
versions and chunking contract are included in metadata. Hybrid search also
checks lexical/semantic source equality. A stale index refuses a new snapshot;
it never relabels old vectors with a new revision.

Build a fresh index after updates or deletions. Changed and deleted records
therefore cannot retain old chunks in the new view. Previously captured views
remain bound to their old snapshots. There is no persisted vector index or
incremental vector refresh in this implementation. CLI semantic searches load
the local model and rebuild vectors each time; this is an explicit latency
tradeoff, not an optimized interactive serving design.

Bounds are 64 chunks per document, 20,000 chunks per index, the existing source
store caps, 256 model tokens per encoded input and 1–100 returned hits. Chunk
validation completes before document encoding. These are not exact resident
memory limits: model weights, framework state and construction temporaries use
additional memory. The model configuration, not a bespoke neural network or
external search database, supplies the actual encoder.

## Reproduction

```sh
.venv/bin/python examples/semantic_demo.py artifacts/minilm
.venv/bin/pip install -r requirements-data.txt
.venv/bin/python scripts/prepare_retrieval_data.py artifacts/evaluation-data
.venv/bin/python scripts/evaluate_retrieval.py artifacts/evaluation-data artifacts/minilm artifacts/retrieval-evaluation.json
```

The fictional lifecycle example runs the actual model, blocks socket connections
in its in-process load/query checks, verifies update/delete snapshot behavior
and executes the installed CLI. It checks mechanics, not human relevance. The
public-data comparison follows the previously fixed
[retrieval protocol](RETRIEVAL.md). It verifies the entire corpus/question/qrel
projection against the frozen digest before creating a new source store. No
selected query is replaced or excluded. Full per-query ranks and raw timing
samples are retained in the evaluation artifact; timing samples are not a
representative-scale performance benchmark. See
[evaluation evidence](EVALUATION.md) for actual runs and limitations.

[Sentence Transformers' API](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
describes local loading and separate query/document encoders.
[NumPy normalization](https://numpy.org/doc/stable/reference/generated/numpy.linalg.norm.html)
supplies the numerical norm operation.
