import type { FormEvent } from "react";
import type { Mode, SearchResult, Status } from "./api";
interface Props {
  status: Status | null;
  query: string;
  mode: Mode;
  results: SearchResult | null;
  searching: boolean;
  onQuery: (value: string) => void;
  onMode: (mode: Mode) => void;
  onOpen: (id: string) => void;
  onSearch: (event: FormEvent) => void;
}
export function SearchPanel({
  status,
  query,
  mode,
  results,
  searching,
  onQuery,
  onMode,
  onOpen,
  onSearch,
}: Props) {
  const semanticReady = status?.semantic_state === "ready";
  return (
    <section className="panel search-panel">
      <div className="panel-heading">
        <h2>Search</h2>
        <span className="badge">Source passages</span>
      </div>
      <form onSubmit={(event) => onSearch(event)} className="search-form">
        <label htmlFor="query">Search your collection</label>
        <div className="search-line">
          <input
            id="query"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="e.g. recovery notes"
            maxLength={2048}
            required
          />
          <button className="primary" disabled={searching || !status}>
            {searching ? "Searching…" : "Search"}
          </button>
        </div>
        <label htmlFor="mode">Search mode</label>
        <select
          id="mode"
          value={mode}
          onChange={(event) => onMode(event.target.value as Mode)}
        >
          <option value="text">Keyword · any matching term</option>
          <option value="strict">Exact terms, phrases and tags</option>
          <option value="semantic" disabled={!semanticReady}>
            Semantic · meaning
          </option>
          <option value="hybrid" disabled={!semanticReady}>
            Hybrid · keyword + meaning
          </option>
        </select>
        <p className="small muted">
          {mode === "strict"
            ? 'Require every term. Use "quoted phrases" and tag:notes.'
            : "Results show original text. Scores are ranking signals, not probabilities."}
        </p>
      </form>
      {results ? (
        <>
          <div className="result-summary">
            <strong>{results.total_matches} matches</strong>
            <span>Revision {results.index.snapshot_revision}</span>
          </div>
          {results.index.snapshot_revision !== status?.revision && (
            <p className="notice small">
              The collection has changed. Search again for the current revision.
            </p>
          )}
          {results.hits.map((hit) => (
            <button
              className="result"
              key={hit.id}
              onClick={() => onOpen(hit.id)}
            >
              <div className="result-title">
                <h3>{hit.title}</h3>
                <span>{hit.score.toFixed(3)}</span>
              </div>
              <p className="passage">{hit.passage.text}</p>
              <span className="small muted">
                {hit.id} · document revision {hit.document_revision}
              </span>
            </button>
          ))}
          {!results.hits.length && (
            <div className="empty">
              No matching documents. Try another term or search mode.
            </div>
          )}
        </>
      ) : (
        <div className="search-empty">
          <span className="search-symbol" aria-hidden="true">
            ⌕
          </span>
          <h3>Start with a question or a keyword</h3>
          <p>Open a result to inspect and edit its complete source document.</p>
        </div>
      )}
    </section>
  );
}
