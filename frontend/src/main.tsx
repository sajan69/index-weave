import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, request } from "./api";
import type {
  DocumentPage,
  Mode,
  RecordData,
  SearchResult,
  Status,
} from "./api";
import { CollectionPanel } from "./CollectionPanel";
import { SearchPanel } from "./SearchPanel";
import { EditorPanel } from "./EditorPanel";
import type { Draft } from "./EditorPanel";
import "./style.css";

const blank = (revision: number): Draft => ({
  id: "",
  title: "",
  text: "",
  tags: "",
  expected_revision: revision,
  isNew: true,
});
function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [page, setPage] = useState<DocumentPage>({
    revision: 0,
    documents: [],
    next_after: null,
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("text");
  const [results, setResults] = useState<SearchResult | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [searching, setSearching] = useState(false);
  const [conflict, setConflict] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const searchEpoch = useRef(0);
  const editorEpoch = useRef(0);
  const failure = useCallback(
    (error: unknown) =>
      setError(
        error instanceof Error
          ? error.message
          : "The local server is unavailable.",
      ),
    [],
  );
  const refresh = useCallback(async () => {
    const [next, list] = await Promise.all([
      request<Status>("/api/status"),
      request<DocumentPage>("/api/documents"),
    ]);
    setStatus(next);
    setPage(list);
  }, []);
  useEffect(() => {
    let active = true;
    void Promise.all([
      request<Status>("/api/status"),
      request<DocumentPage>("/api/documents"),
    ])
      .then(([next, list]) => {
        if (active) {
          setStatus(next);
          setPage(list);
        }
      })
      .catch(failure);
    return () => {
      active = false;
    };
  }, [failure]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      void request<Status>("/api/status").then(setStatus).catch(failure);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [failure]);
  const discard = () => !dirty || window.confirm("Discard the unsaved draft?");
  const openDocument = async (id: string) => {
    if (saving || !discard()) return;
    const epoch = ++editorEpoch.current;
    try {
      const record = await request<RecordData>(
        "/api/documents/" + encodeURIComponent(id),
      );
      if (epoch !== editorEpoch.current) return;
      setDraft({
        ...record.document,
        tags: record.document.tags.join(", "),
        expected_revision: record.store_revision,
        isNew: false,
      });
      setDirty(false);
      setConflict(false);
      setError("");
      setNotice("");
    } catch (error) {
      failure(error);
    }
  };
  const change = (key: "id" | "title" | "text" | "tags", value: string) => {
    setDraft((previous) =>
      previous ? { ...previous, [key]: value } : previous,
    );
    setDirty(true);
    setNotice("");
  };
  const newDocument = () => {
    if (saving || !discard() || !status) return;
    ++editorEpoch.current;
    setDraft(blank(status.revision));
    setDirty(false);
    setConflict(false);
    setError("");
    setNotice("");
  };
  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (saving || !file || !status || !discard()) return;
    const epoch = ++editorEpoch.current;
    try {
      if (file.size > 65536 || !/\.(txt|md)$/i.test(file.name))
        throw new Error("Choose a UTF-8 .txt or .md file of at most 64 KiB.");
      const text = new TextDecoder("utf-8", {
        fatal: true,
        ignoreBOM: true,
      }).decode(await file.arrayBuffer());
      if (epoch !== editorEpoch.current) return;
      const name = file.name.replace(/\.(txt|md)$/i, "");
      const id =
        name
          .replace(/[^a-zA-Z0-9._-]/g, "-")
          .replace(/^[^a-zA-Z0-9]+/, "")
          .slice(0, 64) || "document";
      ++editorEpoch.current;
      setDraft({
        id,
        title: name.slice(0, 100) || "Imported document",
        text,
        tags: "",
        expected_revision: status.revision,
        isNew: true,
      });
      setDirty(true);
      setConflict(false);
      setError("");
      setNotice("File loaded into a draft. Review its ID and text, then save.");
    } catch (error) {
      failure(error);
    }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload = {
        ...draft,
        tags: draft.tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
      };
      const { isNew, ...body } = payload;
      const result = await request<{ revision: number }>(
        isNew
          ? "/api/documents"
          : "/api/documents/" + encodeURIComponent(draft.id),
        isNew ? "POST" : "PUT",
        body,
      );
      ++searchEpoch.current;
      setSearching(false);
      setResults(null);
      setDraft({ ...draft, isNew: false, expected_revision: result.revision });
      setDirty(false);
      setConflict(false);
      setNotice("Saved at collection revision " + result.revision + ".");
      await refresh();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      failure(error);
    } finally {
      setSaving(false);
    }
  };
  const remove = async () => {
    if (
      saving ||
      !draft ||
      draft.isNew ||
      !window.confirm(
        "Delete this document? Older text can remain in the source log.",
      )
    )
      return;
    setSaving(true);
    setError("");
    try {
      await request(
        "/api/documents/" + encodeURIComponent(draft.id),
        "DELETE",
        { expected_revision: draft.expected_revision },
      );
      ++searchEpoch.current;
      setSearching(false);
      setResults(null);
      setDraft(null);
      setDirty(false);
      setNotice("Document deleted from the live collection.");
      await refresh();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      failure(error);
    } finally {
      setSaving(false);
    }
  };
  const search = async (event: FormEvent) => {
    event.preventDefault();
    setSearching(true);
    setError("");
    const epoch = ++searchEpoch.current;
    try {
      const result = await request<SearchResult>("/api/search", "POST", {
        query,
        mode,
        limit: 20,
      });
      if (epoch === searchEpoch.current) setResults(result);
    } catch (error) {
      failure(error);
    } finally {
      if (epoch === searchEpoch.current) setSearching(false);
    }
  };
  const refreshSemantic = async () => {
    if (!status) return;
    try {
      await request("/api/semantic/refresh", "POST", {
        expected_revision: status.revision,
      });
      setStatus({ ...status, semantic_state: "building" });
      setNotice(
        "Building the semantic index. Keyword search remains available.",
      );
    } catch (error) {
      failure(error);
    }
  };
  const loadMore = async () => {
    if (!page.next_after) return;
    try {
      const next = await request<DocumentPage>(
        "/api/documents?after=" + encodeURIComponent(page.next_after),
      );
      if (next.revision !== page.revision) {
        await refresh();
        return;
      }
      setPage({ ...next, documents: [...page.documents, ...next.documents] });
    } catch (error) {
      failure(error);
    }
  };
  const download = () => {
    if (!draft) return;
    const url = URL.createObjectURL(
      new Blob([draft.text], { type: "text/plain;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = (draft.id || "document") + ".txt";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const rebaseDraft = async () => {
    try {
      const next = await request<Status>("/api/status");
      setStatus(next);
      setDraft((previous) =>
        previous?.isNew
          ? { ...previous, expected_revision: next.revision }
          : previous,
      );
      setConflict(false);
      setError("");
    } catch (error) {
      failure(error);
    }
  };
  return (
    <div className="shell">
      <header className="masthead">
        <div className="identity">
          <span className="mark" aria-hidden="true">
            IW
          </span>
          <div>
            <p className="eyebrow">LOCAL DOCUMENT WORKSPACE</p>
            <h1>IndexWeave</h1>
          </div>
        </div>
        <div className="collection-status">
          <span className="dot" />{" "}
          <span>
            {status
              ? `${status.document_count} documents · revision ${status.revision}`
              : "Connecting to local collection…"}
          </span>
        </div>
      </header>
      <section className="intro">
        <div>
          <p className="eyebrow">YOUR COLLECTION, WITH CONTEXT</p>
          <h2>Find the note. Keep the source.</h2>
          <p>
            Search your documents, inspect the passage, and keep revisions under
            control.
          </p>
        </div>
        <div className="actions">
          <button
            onClick={() => fileInput.current?.click()}
            disabled={!status || saving}
          >
            Import text
          </button>
          <button
            className="primary"
            onClick={newDocument}
            disabled={!status || saving}
          >
            + New document
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".txt,.md,text/plain,text/markdown"
            aria-label="Import UTF-8 document"
            onChange={(event) => void importFile(event)}
            hidden
          />
        </div>
      </section>
      <div className="feedback" aria-live="polite">
        {error && (
          <div role="alert" className="error">
            {error}
            {conflict && " Your draft has been kept; it was not saved."}
          </div>
        )}
        {notice && <div className="notice">{notice}</div>}
      </div>
      <main className="workspace">
        <CollectionPanel
          status={status}
          page={page}
          selectedId={draft?.id}
          onRefresh={() => void refresh().catch(failure)}
          onOpen={(id) => void openDocument(id)}
          onLoadMore={() => void loadMore()}
          onSemantic={() => void refreshSemantic()}
        />
        <SearchPanel
          status={status}
          query={query}
          mode={mode}
          results={results}
          searching={searching}
          onQuery={setQuery}
          onMode={setMode}
          onOpen={(id) => void openDocument(id)}
          onSearch={(event) => void search(event)}
        />
        <EditorPanel
          draft={draft}
          dirty={dirty}
          saving={saving}
          conflict={conflict}
          onSave={(event) => void save(event)}
          onChange={change}
          onDownload={download}
          onDelete={() => void remove()}
          onReload={() => {
            if (draft) void openDocument(draft.id);
          }}
          onRebase={() => void rebaseDraft()}
        />
      </main>
      <footer>
        One workspace on this machine. Documents stay with the local server.
        Deleted text can remain in older log records.
      </footer>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
