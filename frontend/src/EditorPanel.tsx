import type { FormEvent } from "react";
export interface Draft {
  id: string;
  title: string;
  text: string;
  tags: string;
  expected_revision: number;
  isNew: boolean;
}
interface Props {
  draft: Draft | null;
  dirty: boolean;
  saving: boolean;
  conflict: boolean;
  onSave: (event: FormEvent) => void;
  onChange: (key: "id" | "title" | "text" | "tags", value: string) => void;
  onDownload: () => void;
  onDelete: () => void;
  onReload: () => void;
  onRebase: () => void;
}
export function EditorPanel({
  draft,
  dirty,
  saving,
  conflict,
  onSave,
  onChange,
  onDownload,
  onDelete,
  onReload,
  onRebase,
}: Props) {
  return (
    <section className="panel editor">
      <div className="panel-heading">
        <h2>
          {draft
            ? draft.isNew
              ? "New document"
              : "Document source"
            : "Source & editor"}
        </h2>
        {dirty && <span className="badge warm">Unsaved draft</span>}
      </div>
      {draft ? (
        <form onSubmit={(event) => onSave(event)}>
          <fieldset className="editor-fields" disabled={saving}>
            <div className="editor-meta">
              Based on collection revision {draft.expected_revision}
            </div>
            <label htmlFor="document-id">Document ID</label>
            <input
              id="document-id"
              value={draft.id}
              onChange={(event) => onChange("id", event.target.value)}
              disabled={!draft.isNew}
              maxLength={64}
              pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
              required
            />
            <label htmlFor="title">Title</label>
            <input
              id="title"
              value={draft.title}
              onChange={(event) => onChange("title", event.target.value)}
              maxLength={512}
              required
            />
            <label htmlFor="tags">
              Tags <span className="muted">(comma separated)</span>
            </label>
            <input
              id="tags"
              value={draft.tags}
              onChange={(event) => onChange("tags", event.target.value)}
              placeholder="notes, storage"
            />
            <label htmlFor="text">Document text</label>
            <textarea
              id="text"
              value={draft.text}
              onChange={(event) => onChange("text", event.target.value)}
              maxLength={65536}
              required
              spellCheck={false}
            />
            <p className="small muted">
              Plain UTF-8 text or Markdown, up to 64 KiB. Text is displayed
              without executing HTML.
            </p>
            <div className="editor-actions">
              <button className="primary" disabled={saving}>
                {saving ? "Saving…" : "Save document"}
              </button>
              <button type="button" onClick={onDownload}>
                Download text
              </button>
              {!draft.isNew && (
                <button
                  className="danger"
                  type="button"
                  onClick={() => onDelete()}
                  disabled={saving}
                >
                  Delete document
                </button>
              )}
            </div>
            {conflict &&
              (draft.isNew ? (
                <button type="button" onClick={onRebase}>
                  Use current revision for this new document
                </button>
              ) : (
                <button type="button" onClick={() => onReload()}>
                  Reload saved version to review
                </button>
              ))}
          </fieldset>
        </form>
      ) : (
        <div className="empty editor-empty">
          <h3>Keep the original in view</h3>
          <p>
            Select a document or search result to see the full text. Edits
            require the revision you opened.
          </p>
        </div>
      )}
    </section>
  );
}
