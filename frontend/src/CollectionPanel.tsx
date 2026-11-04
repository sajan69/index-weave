import type { DocumentPage, Status } from "./api";
interface Props {
  status: Status | null;
  page: DocumentPage;
  selectedId?: string;
  onRefresh: () => void;
  onOpen: (id: string) => void;
  onLoadMore: () => void;
  onSemantic: () => void;
}
export function CollectionPanel({
  status,
  page,
  selectedId,
  onRefresh,
  onOpen,
  onLoadMore,
  onSemantic,
}: Props) {
  return (
    <aside className="panel library">
      <div className="panel-heading">
        <h2>Collection</h2>
        <button className="quiet" onClick={onRefresh}>
          Refresh
        </button>
      </div>
      <p className="muted small">Saved documents in ID order</p>
      <nav aria-label="Saved documents">
        {page.documents.map((document) => (
          <button
            className={
              selectedId === document.id
                ? "document-row selected"
                : "document-row"
            }
            key={document.id}
            onClick={() => onOpen(document.id)}
          >
            <strong>{document.title}</strong>
            <span>{document.id}</span>
            <span className="tags">
              {document.tags.join(" · ") || "No tags"}
            </span>
          </button>
        ))}
      </nav>
      {!page.documents.length && (
        <div className="empty">
          <strong>A place for your notes</strong>
          <p>Create a document or import a text file to begin.</p>
        </div>
      )}
      {page.next_after && (
        <button onClick={onLoadMore}>Load more documents</button>
      )}
      <div className="model-state">
        <span className="eyebrow">SEMANTIC INDEX</span>
        <strong>{status?.semantic_state ?? "Connecting"}</strong>
        <p className="small muted">
          {status?.semantic_state === "unavailable"
            ? "Keyword and exact search are ready without an embedding model."
            : "Refresh after changes to use semantic or hybrid search."}
        </p>
        <button
          onClick={onSemantic}
          disabled={
            !status ||
            status.semantic_state === "unavailable" ||
            status.semantic_state === "building"
          }
        >
          Refresh semantic index
        </button>
      </div>
    </aside>
  );
}
