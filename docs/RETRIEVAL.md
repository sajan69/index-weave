# Retrieval experiment contract

The protocol identifier is `hotpot-pool-v1`. This specifies a bounded passage
retrieval experiment, not question answering or a leaderboard reproduction. It
is fixed before model inference, ranking selection or held-out errors. There are
no relevance scores or semantic retrieval results in this document.

## Data and permissions

The [HotpotQA publishers](https://hotpotqa.github.io/) distribute the dataset
and processed Wikipedia under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The dataset is
attributed to Zhilin Yang, Peng Qi, Saizheng Zhang, Yoshua Bengio, William W.
Cohen, Ruslan Salakhutdinov and Christopher D. Manning, _HotpotQA: A Dataset for
Diverse, Explainable Multi-hop Question Answering_, 2018. Wikipedia contributors
remain the authors of the source passages. Derived data must retain this
attribution, licence link, source-title links and a description of changes; it
is not a licence declaration for unrelated application code.

The original development distractor download returned HTTP 503 during the 4
November 2026 review. The separately versioned
[Hugging Face distribution](https://huggingface.co/datasets/hotpotqa/hotpot_qa)
is the candidate acquisition source. A fixed-source metadata audit records its
actual commit, asset digest and licence declaration before ingestion. A mirror
revision/hash is provenance, not authenticated equivalence to an unavailable
original file. Raw passages and questions are not committed in this repository.

## Selection before relevance evaluation

Use the distractor validation split. Sort its unique question IDs by ascending
SHA-256 of UTF-8 `indexweave-hotpot-v1:` followed by the ID, with ID as a tie
breaker. Select the first 200: the first 100 are development and the next 100
are the untouched selection holdout. There is no sampling based on question
text, supporting labels or model errors. Fail if fewer than 200 valid items are
available; do not adapt the rule after examining retrieval outcomes.

The corpus is the deduplicated union of **all** supplied contexts for those 200
questions, including distractors. Join each context's sentence strings in order
without inserting or removing characters. A source unit is that title and
complete joined paragraph, not an entire Wikipedia page. Its stable document ID
is `hp-` plus the first 60 hex characters of SHA-256(title + NUL + paragraph);
verify full-digest equality on any ID collision. Preserve titles, original
sentences, source-title URLs and projection hashes in the local data manifest.

Binary document relevance comes from the dataset's existing supporting-fact
titles mapped to their exact question contexts. Validate sentence indices and
context references; multiple supporting sentences in one paragraph count as one
relevant document. Do not invent labels or index the answer field. Reject schema
errors and source units outside existing storage bounds rather than silently
truncating or filtering favourable examples. Record title/text variants and any
annotation limitations. The pooled corpus is deliberately smaller and easier
than full-Wikipedia retrieval; unlabelled documents are not proof of
irrelevance.

This holdout controls our own development decisions. It does not establish that
the pretrained model never saw these questions or source text during training.
Wikipedia-derived data may overlap its training sources. There is no claimed
prospective, contamination-free or representative end-user evaluation.

## Fixed model and ranking choices

Use one pretrained CPU model:
[sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, declared Apache-2.0. The
model card describes 384-dimensional embeddings and a default 256-wordpiece
input limit. Review and hash its configuration, tokenizer and safetensors
assets; do not load pickle weights, repository Python or custom model modules.
No training, hyperparameter search or alternative-model tournament is included.

Document-body windows contain at most 192 tokenizer wordpieces with stride 160.
Each encoded passage prefixes at most the first 32 title wordpieces. Retain
original code-point offsets for both slices; special tokens must still fit the
256-token model limit. Reject overlong queries instead of silently truncating.
Call maintained `encode_query` and `encode_document` separately, with no custom
prompts for this model. Use CPU float32 normalized vectors and exact cosine
search. A document's semantic score is its maximum passage score; ties use ID.
No approximate index, reranker or generative answer is part of this protocol.

The lexical baseline is full-snapshot BM25 over a bag of normalized free-text
question terms with OR candidate retrieval, using the existing `k1=1.2`,
`b=0.75` and positive IDF. It is **not** the strict required-clause command
applied to a natural-language question. Count each unique term once; no stopword
removal or question-specific rewrite. The same complete paragraph/title units
are used by all methods.

Compare lexical, semantic and a fixed equal-weight reciprocal-rank fusion of
their first 100 document ranks: sum `1 / (60 + rank)` for each list containing
the document. Missing ranks contribute zero. Deduplicate documents before
metrics; ties use ID. No weight or threshold is selected from holdout results.

## Outcomes and reproducibility

Report Recall@10 and binary nDCG@10 per query and their arithmetic means, plus
the fraction of queries retrieving every annotated supporting paragraph at 10.
Use the full annotated relevant-document denominator. Report all three methods
on development and holdout, paired per-query differences and both improved and
worsened cases. Empty results score zero. Questions with no valid relevance
mapping are data errors, not silently excluded metric rows.

Record the source/data/model hashes, query IDs and split, projection changes,
chunking contract, library versions, numerical thread limits and actual compute
environment. Distinguish indexing/encoding time from warm query timing and file
sizes from resident memory. Measurements require actual runs and raw samples;
this protocol supplies no assumed speed or quality gains. No business impact,
production recovery, user adoption or universal semantic-search benefit follows
from this bounded experiment.

## Mechanical validation

`indexweave.retrieval_data` implements the deterministic row selection,
paragraph projection and original sentence preservation,
binary metrics and fixed fusion rule without network access or model inference.
Its synthetic tests exercise hand-calculated metrics, full relevance
denominators, ID-only selection, distractor retention, ambiguous titles,
malformed annotations and storage bounds. These tests are correctness checks,
not retrieval-quality results. `scripts/audit_retrieval_sources.py` audits fixed
public metadata and allowlisted model configurations in CI; it does not download
weights or run a model. The audit artifact distinguishes declared licences and
asset digests from verified downloaded bytes. The audited module allowlist contains the maintained
Transformer, Pooling and optional
[Normalize](https://www.sbert.net/docs/package_reference/sentence_transformer/modules.html)
components; no repository-defined Python modules are accepted.
