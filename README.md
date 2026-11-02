# IndexWeave

A document search engine for maintaining and searching an authorised collection of notes and technical documents. Its design combines a custom persistent document store and lexical index with embedding-based semantic retrieval, an API and a web interface.

The core scope includes document updates and deletion, explicit storage and recovery semantics, keyword and phrase search, and comparison of lexical, semantic and hybrid retrieval. Established libraries provide model inference, API validation and interface tooling. This is a specialised document store, not a general-purpose relational database.

Initial document inputs are plain text and Markdown. Demonstrations and evaluation use synthetic or appropriately licensed public material; private documents are not imported automatically or sent to external model services.

There is no runnable engine, integrated model or measured evaluation in this repository yet. No durability, retrieval-quality, production-readiness or adoption claims are made.
