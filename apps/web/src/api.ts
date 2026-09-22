import { clearPrivateSession, sessionGeneration } from "./session-cache";
export type Data = Record<string, any>;
export class ApiError extends Error {
  constructor(
    message: string,
    public actionReserved: string | null,
    public status?: number,
  ) {
    super(message);
  }
}
export async function api(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<Data> {
  const generation = sessionGeneration();
  const response = await fetch("/api" + path, {
    method,
    signal,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (generation !== sessionGeneration())
    throw new ApiError("Session changed. Sign in again.", null);
  if (response.status === 401) {
    clearPrivateSession();
    window.dispatchEvent(new Event("leam:auth-lost"));
  }
  if (response.ok && ["/auth/login", "/auth/setup"].includes(path))
    clearPrivateSession();
  // Login/setup and a 401 intentionally rotate the generation above. Fence
  // the separate asynchronous body read against any later logout/login too.
  const bodyGeneration = sessionGeneration();
  const data = await response.json();
  if (bodyGeneration !== sessionGeneration())
    throw new ApiError("Session changed. Sign in again.", null);
  if (!response.ok)
    throw new ApiError(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
      response.headers.get("X-Leam-Action-Reserved"),
      response.status,
    );
  return data;
}

// Coalesce only concurrent display reads; every later read still revalidates.
const pendingChatReads = new Map<string, Promise<Data>>();
export function chatRead(path: string, revision = 0): Promise<Data> {
  const generation = sessionGeneration();
  const key = generation + ":" + revision + ":" + path;
  const pending = pendingChatReads.get(key);
  if (pending) return pending;
  if (pendingChatReads.size >= 32)
    return Promise.reject(
      new Error("Too many pending chat reads. Retry shortly."),
    );
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const request = api(path, "GET", undefined, controller.signal)
    .then((data) => {
      if (generation !== sessionGeneration())
        throw new Error("Session changed. Sign in again.");
      return data;
    })
    .finally(() => {
      clearTimeout(timer);
      if (pendingChatReads.get(key) === request) pendingChatReads.delete(key);
    });
  pendingChatReads.set(key, request);
  return request;
}
