import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { AIChatTurn } from "../types";
import { IconSend, IconSparkles } from "./icons";
import { InfoHint } from "./InfoHint";

interface AIInsightPanelProps {
  token: string;
  deviceId: string;
}

/**
 * Phase 10: a Gemini-backed summary of the selected device's recent data
 * (GET /devices/{id}/ai/insight) plus a grounded follow-up chat
 * (POST /devices/{id}/ai/chat) -- both implemented in
 * backend/app/routers/ai.py against real Phase 3/6 pipeline output, never
 * invented data. A 503 here means the backend has no GEMINI_API_KEY set;
 * that's a config gap, not a bug, so it's shown as a plain notice rather
 * than an error state.
 */
export function AIInsightPanel({ token, deviceId }: AIInsightPanelProps) {
  const [summary, setSummary] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [insightLoading, setInsightLoading] = useState(false);
  const [insightError, setInsightError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);

  const [messages, setMessages] = useState<AIChatTurn[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  function loadInsight() {
    setInsightLoading(true);
    setInsightError(null);
    setNotConfigured(false);
    api
      .aiInsight(token, deviceId)
      .then((result) => {
        setSummary(result.summary);
        setGeneratedAt(result.generated_at);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 503) setNotConfigured(true);
        else if (err instanceof ApiError && err.status === 404) setInsightError("No readings yet for this device.");
        else setInsightError(err instanceof ApiError ? err.message : "Could not load an AI insight.");
      })
      .finally(() => setInsightLoading(false));
  }

  // Fresh insight + cleared chat whenever the selected device changes.
  useEffect(() => {
    setSummary(null);
    setGeneratedAt(null);
    setMessages([]);
    setChatError(null);
    loadInsight();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  async function sendChat() {
    const message = chatInput.trim();
    if (!message || chatLoading) return;
    setChatInput("");
    setChatError(null);
    const history = messages;
    setMessages((prev) => [...prev, { role: "user", text: message }]);
    setChatLoading(true);
    try {
      const result = await api.aiChat(token, deviceId, message, history);
      setMessages((prev) => [...prev, { role: "model", text: result.reply }]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) setNotConfigured(true);
      setChatError(err instanceof ApiError ? err.message : "Could not reach the AI assistant.");
      setMessages((prev) => prev.slice(0, -1)); // drop the optimistic turn that never got a reply
      setChatInput(message);
    } finally {
      setChatLoading(false);
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-white/[0.03] dark:shadow-none dark:backdrop-blur-xl">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          <IconSparkles className="h-3.5 w-3.5" /> AI insight
          <InfoHint
            text="A Gemini-generated summary of this device's recent readings and alerts -- not a diagnosis. Ask it follow-up questions below."
            align="left"
          />
        </h2>
        <button
          type="button"
          onClick={loadInsight}
          disabled={insightLoading || notConfigured}
          className="rounded-md px-2 py-1 text-[10px] font-medium text-slate-500 transition hover:bg-slate-100 disabled:opacity-40 dark:text-slate-400 dark:hover:bg-white/[0.06]"
        >
          {insightLoading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {notConfigured ? (
        <p className="rounded-lg bg-slate-50 px-3 py-2.5 text-xs text-slate-500 dark:bg-white/[0.04] dark:text-slate-400">
          AI analysis isn't configured on this backend yet -- set{" "}
          <code className="font-mono">GEMINI_API_KEY</code> to enable it.
        </p>
      ) : insightError ? (
        <p className="rounded-lg bg-rose-50 px-3 py-2.5 text-xs text-rose-600 dark:bg-rose-950/30 dark:text-rose-300">{insightError}</p>
      ) : insightLoading && !summary ? (
        <p className="py-4 text-center text-xs text-slate-400">Asking Gemini about this device…</p>
      ) : summary ? (
        <div>
          <p className="text-sm leading-relaxed text-slate-700 dark:text-slate-200">{summary}</p>
          {generatedAt && (
            <p className="mt-2 font-mono text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
              generated {new Date(generatedAt).toLocaleTimeString()}
            </p>
          )}
        </div>
      ) : null}

      {!notConfigured && (
        <div className="mt-3 border-t border-slate-100 pt-3 dark:border-white/[0.06]">
          <div ref={scrollRef} className="mb-2 max-h-48 space-y-2 overflow-y-auto pr-1">
            {messages.map((m, i) => (
              <div
                key={i}
                className={`rounded-lg px-2.5 py-1.5 text-xs ${
                  m.role === "user"
                    ? "ml-6 bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200"
                    : "mr-6 bg-slate-50 text-slate-700 dark:bg-white/[0.04] dark:text-slate-200"
                }`}
              >
                {m.text}
              </div>
            ))}
            {chatLoading && <p className="pl-1 text-[11px] text-slate-400">Thinking…</p>}
          </div>
          {chatError && <p className="mb-2 text-[11px] text-rose-500 dark:text-rose-400">{chatError}</p>}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              sendChat();
            }}
            className="flex items-center gap-2"
          >
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Ask about this device's data…"
              className="flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
            />
            <button
              type="submit"
              disabled={chatLoading || !chatInput.trim()}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-amber-500 text-white transition hover:bg-amber-600 disabled:opacity-40 dark:bg-amber-500 dark:hover:bg-amber-400"
              aria-label="Send"
            >
              <IconSend className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
