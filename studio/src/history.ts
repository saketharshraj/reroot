// Chat history lives only in this browser (localStorage). Nothing is sent to a database.
import type { Message } from "./Chat";
import type { Workspace } from "./api";

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: Message[];
  workspace: Workspace;
  view: string;
}

const KEY = "reroot.chats.v1";
const CURRENT = "reroot.current.v1";
const MAX = 20;

// Keep what is needed to resume; plantations reload live.
function slim(ws: Workspace): Workspace {
  const { plantations: _p, plantation_check: _c, ...rest } = ws;
  return rest;
}

export function loadAll(): Conversation[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

export function currentId(): string | null {
  try {
    return localStorage.getItem(CURRENT);
  } catch {
    return null;
  }
}

export function setCurrentId(id: string) {
  try {
    localStorage.setItem(CURRENT, id);
  } catch {
    /* storage unavailable: history just won't persist */
  }
}

export function save(c: Conversation) {
  const all = [{ ...c, workspace: slim(c.workspace) }, ...loadAll().filter((x) => x.id !== c.id)]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX);
  // On quota errors drop the oldest conversations until it fits.
  for (let n = all.length; n > 0; n--) {
    try {
      localStorage.setItem(KEY, JSON.stringify(all.slice(0, n)));
      return;
    } catch {
      /* try with fewer */
    }
  }
}

export function remove(id: string) {
  try {
    localStorage.setItem(KEY, JSON.stringify(loadAll().filter((x) => x.id !== id)));
  } catch {
    /* ignore */
  }
}

export function titleFor(messages: Message[]): string {
  const first = messages.find((m) => m.role === "user")?.text ?? "New chat";
  return first.length > 60 ? first.slice(0, 57) + "…" : first;
}
