export type Mode = "text" | "strict" | "semantic" | "hybrid";
export interface Status {
  revision: number;
  document_count: number;
  semantic_state: "unavailable" | "stale" | "building" | "ready" | "error";
  semantic_revision: number | null;
}
export interface DocumentData {
  id: string;
  title: string;
  text: string;
  tags: string[];
}
export interface DocumentInfo {
  id: string;
  title: string;
  tags: string[];
  document_revision: number;
}
export interface DocumentPage {
  revision: number;
  documents: DocumentInfo[];
  next_after: string | null;
}
export interface RecordData {
  store_revision: number;
  document_revision: number;
  document: DocumentData;
}
export interface Hit {
  id: string;
  title: string;
  document_revision: number;
  score: number;
  passage: { text: string; start: number; end: number; field: string };
}
export interface SearchResult {
  index: { snapshot_revision: number };
  hits: Hit[];
  total_matches: number;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      "X-IndexWeave-Request": "1",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(
      response.status,
      data.message ??
        "The local server refused this request. Check the collection and input limits.",
    );
  return data as T;
}
