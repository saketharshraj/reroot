import { useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { Role } from "./api";
import { fmt } from "./geo";
import { Icon, TechBadge } from "./ui";

export interface Step { id: string; tool: string; label: string; tech: string[]; state: "running" | "ok" | "fail"; ms?: number; summary?: string | null }
export interface Message { id: string; role: "user" | "assistant"; text: string; steps: Step[]; error?: string; pending?: boolean }

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

interface Props {
  messages: Message[];
  busy: boolean;
  role: Role | null;
  suggestions: string[];
  footprint: { name: string; area_ha: number } | null;
  drawing: boolean;
  onSend: (text: string) => void;
  onPickRole: (role: Role, prompt: string) => void;
  onDraw: () => void;
  onUpload: (f: File) => void;
  onClearFootprint: () => void;
  onStop: () => void;
}

export function Chat(p: Props) {
  const [text, setText] = useState("");
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

  return (
    <section className="chat" aria-label="Chat with ReRoot">
      <div className="chat-scroll" ref={scroller}>
        {p.messages.length === 0 ? (
          <div className="welcome">
            <div className="welcome-mark"><Icon name="leaf" size={22} /></div>
            <h1>Plant where trees will survive.<br />Then prove they did.</h1>
            <p className="muted">
              Tell ReRoot what you're working on. It reads satellite data, scores every hectare, writes the plan and
              keeps watch after planting. Who are you?
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
                {m.steps.length > 0 && (
                  <ol className="steps">
                    {m.steps.map((s) => (
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
                {m.text && (m.role === "user" ? <div className="bubble">{m.text}</div> : <div className="md" dangerouslySetInnerHTML={md(m.text)} />)}
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
          placeholder={p.role ? "Ask ReRoot anything…" : "Describe your project, or pick who you are above…"}
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
