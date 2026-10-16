import { useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { PolicySource, Role } from "./api";
import { fmt } from "./geo";
import type { Conversation } from "./history";
import { Icon, TechBadge } from "./ui";

export interface Step { id: string; tool: string; label: string; tech: string[]; state: "running" | "ok" | "fail"; ms?: number; summary?: string | null }
export interface Message {
  id: string; role: "user" | "assistant"; text: string; steps: Step[]; error?: string; pending?: boolean;
  outline?: string[]; sources?: PolicySource[];
}

export const ROLES: { id: Role; title: string; blurb: string; intro: string; prompt: string }[] = [
  { id: "developer", title: "Project developer", blurb: "Need a compensatory planting plan for forest clearance", intro: "I'm with a highway contractor.",
    prompt: "We're building a 14 km bypass through the Aravallis. What will we clear, and where should we replant? Avoid farmland, prioritise survival." },
  { id: "forest_department", title: "Forest department", blurb: "Approve plans and track plantation survival", intro: "I'm with the forest department.",
    prompt: "How are the plantations in this division doing? Flag anything at risk." },
  { id: "ngo", title: "NGO / field partner", blurb: "Run plantations and coordinate caretakers", intro: "I'm with an NGO that runs plantations.",
    prompt: "Which of our plantations need attention this week? Alert the caretakers if needed." },
  { id: "csr", title: "CSR / sustainability", blurb: "Fund planting and prove it survived", intro: "I'm on a company's CSR team.",
    prompt: "Show me evidence that the plantations we funded are actually surviving." },
];

marked.setOptions({ breaks: true });
const md = (t: string) => ({ __html: DOMPurify.sanitize(marked.parse(t) as string) });
const TOOL_STEPS = (m: Message) => m.steps.filter((s) => s.tool !== "outline_steps");

function ago(ts: number) {
  const m = Math.round((Date.now() - ts) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

interface Props {
  messages: Message[];
  busy: boolean;
  role: Role | null;
  suggestions: string[];
  footprint: { name: string; area_ha: number } | null;
  drawing: boolean;
  history: Conversation[];
  currentId: string;
  onSend: (text: string) => void;
  onPickRole: (role: Role, prompt: string) => void;
  onDraw: () => void;
  onUpload: (f: File) => void;
  onClearFootprint: () => void;
  onStop: () => void;
  onLink: (href: string, text: string) => void;
  onNewChat: () => void;
  onOpenChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
}

function Outline({ m }: { m: Message }) {
  if (!m.outline?.length) return null;
  const done = TOOL_STEPS(m).filter((s) => s.state !== "running").length;
  return (
    <div className="outline">
      <div className="outline-head"><Icon name="list" size={13} /> Plan</div>
      <ol>
        {m.outline.map((s, i) => {
          const state = i < done ? "done" : i === done && m.pending ? "now" : "todo";
          return (
            <li key={i} className={state}>
              <span className="box">{state === "done" ? <Icon name="check" size={10} /> : state === "now" ? <span className="spin" /> : null}</span>
              {s}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Sources({ items }: { items: PolicySource[] }) {
  return (
    <div className="cites">
      <div className="cites-head"><Icon name="shield" size={13} /> Official sources read</div>
      {items.filter((s) => s.available).map((s) => (
        <a key={s.id} href={s.url} target="_blank" rel="noreferrer" className="cite">
          <b>{s.title}</b>
          <span>{s.publisher}</span>
          <span className="cite-meta">
            {s.live ? <em className="live">Live</em> : <em className="cached">Cached copy</em>}
            {s.fetched_at && <> · {new Date(s.fetched_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</>}
            {!s.tls_verified && <> · <em className="warn" title="The government server sends an incomplete certificate chain">TLS unverified</em></>}
          </span>
        </a>
      ))}
    </div>
  );
}

export function Chat(p: Props) {
  const [text, setText] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [p.messages]);

  const send = (t: string) => {
    const v = t.trim();
    if (!v || p.busy) return;
    p.onSend(v);
    setText("");
  };

  // Links in replies (#site-3, #plantation-P3, #place-lon,lat, #layer-ndvi, #view-plan, #media...) drive the canvas.
  const onMdClick = (e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href") ?? "";
    if (a && href.startsWith("#")) {
      e.preventDefault();
      p.onLink(href.slice(1), a.textContent ?? "");
    }
  };

  return (
    <section className="chat" aria-label="Chat with ReRoot">
      <header className="chat-head">
        <span className="chat-title">Ask ReRoot <TechBadge name="Gemini" /></span>
        <span className="spacer" />
        <button className="icon-btn" onClick={() => setShowHistory((s) => !s)} aria-expanded={showHistory} title="Chat history">
          <Icon name="clock" size={15} /> History
        </button>
        <button className="icon-btn" onClick={() => { p.onNewChat(); setShowHistory(false); }} title="Start a new chat" disabled={p.busy}>
          <Icon name="plus" size={15} /> New
        </button>
      </header>

      {showHistory && (
        <div className="history" role="dialog" aria-label="Chat history">
          <p className="muted small">Saved in this browser only.</p>
          {p.history.length === 0 && <p className="muted">No saved chats yet.</p>}
          <ul>
            {p.history.map((c) => (
              <li key={c.id} className={c.id === p.currentId ? "on" : ""}>
                <button className="h-open" onClick={() => { p.onOpenChat(c.id); setShowHistory(false); }} disabled={p.busy}>
                  <b>{c.title}</b>
                  <span>{ago(c.updatedAt)} · {c.messages.filter((m) => m.role === "user").length} messages</span>
                </button>
                <button className="x" onClick={() => p.onDeleteChat(c.id)} aria-label={`Delete chat ${c.title}`}>×</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="chat-scroll" ref={scroller}>
        {p.messages.length === 0 ? (
          <div className="welcome">
            <div className="welcome-mark"><Icon name="leaf" size={22} /></div>
            <h1>Plant where trees will survive.<br />Then prove they did.</h1>
            <p className="muted">
              Tell ReRoot what you're working on. It reads satellite data and official policy, scores every hectare,
              writes the plan and keeps watch after planting. Who are you?
            </p>
            <div className="roles">
              {ROLES.map((r) => (
                <button key={r.id} className="role" onClick={() => p.onPickRole(r.id, r.prompt)}>
                  <b>{r.title}</b>
                  <span>{r.blurb}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          p.messages.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.role === "assistant" && <div className="avatar"><Icon name="leaf" size={14} /></div>}
              <div className="msg-body">
                <Outline m={m} />
                {TOOL_STEPS(m).length > 0 && (
                  <ol className="steps">
                    {TOOL_STEPS(m).map((s) => (
                      <li key={s.id} className={`step ${s.state}`}>
                        <span className="step-icon">
                          {s.state === "running" ? <span className="spin" /> : s.state === "ok" ? <Icon name="check" size={12} /> : <Icon name="x" size={12} />}
                        </span>
                        <span className="step-label">{s.label}</span>
                        <span className="step-tech">{s.tech.map((t) => <TechBadge key={t} name={t} />)}</span>
                        {s.ms != null && <span className="step-ms">{fmt(s.ms / 1000, 1)}s</span>}
                        {s.state === "fail" && s.summary && <span className="step-err">{s.summary}</span>}
                      </li>
                    ))}
                  </ol>
                )}
                {m.text && (m.role === "user"
                  ? <div className="bubble">{m.text}</div>
                  : <div className="md" onClick={onMdClick} dangerouslySetInnerHTML={md(m.text)} />)}
                {m.sources?.length ? <Sources items={m.sources} /> : null}
                {m.pending && !m.text && m.steps.length === 0 && <div className="thinking"><span /><span /><span /></div>}
                {m.error && <div className="msg-error">{m.error}</div>}
              </div>
            </div>
          ))
        )}
      </div>

      {p.suggestions.length > 0 && !p.busy && (
        <div className="suggest" aria-label="Suggestions">
          {p.suggestions.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}
        </div>
      )}

      <form className="composer" onSubmit={(e) => (e.preventDefault(), send(text))}>
        {p.footprint && (
          <div className="attach">
            <Icon name="polygon" size={14} />
            <span><b>{p.footprint.name}</b> · {fmt(p.footprint.area_ha, 1)} ha</span>
            <button type="button" className="x" onClick={p.onClearFootprint} aria-label="Remove footprint">×</button>
          </div>
        )}
        <textarea
          rows={2}
          value={text}
          placeholder={p.role ? "Ask ReRoot anything… e.g. what do the rules say about degraded land?" : "Describe your project, or pick who you are above…"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <div className="composer-bar">
          <button type="button" className={`tool ${p.drawing ? "on" : ""}`} onClick={p.onDraw} title="Draw the project footprint on the map">
            <Icon name="pen" size={15} /> {p.drawing ? "Drawing… double-click to finish" : "Draw area"}
          </button>
          <button type="button" className="tool" onClick={() => file.current?.click()} title="Upload a KML or GeoJSON footprint">
            <Icon name="clip" size={15} /> Upload KML
          </button>
          <input ref={file} type="file" accept=".kml,.geojson,.json,application/vnd.google-earth.kml+xml,application/geo+json" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) p.onUpload(f); e.target.value = ""; }} />
          <span className="spacer" />
          {p.busy ? (
            <button type="button" className="send stop" onClick={p.onStop} aria-label="Stop"><Icon name="stop" size={14} /></button>
          ) : (
            <button type="submit" className="send" disabled={!text.trim()} aria-label="Send"><Icon name="send" size={15} /></button>
          )}
        </div>
      </form>
    </section>
  );
}
