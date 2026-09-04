import { FormEvent, useState } from "react";
import { api, ApiError } from "../api";
import { ThemeToggle } from "./ThemeToggle";
import { IconActivity, IconHeartPulse, IconShield, IconZap } from "./icons";

interface LoginScreenProps {
  onLogin: (token: string, refreshToken: string) => void;
  notice?: string | null;
}

export function LoginScreen({ onLogin, notice }: LoginScreenProps) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { access_token, refresh_token } = await api.login(username, password);
      onLogin(access_token, refresh_token);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-4 py-10">
      <ThemeToggle className="absolute right-5 top-5" />
      <div className="grid w-full max-w-4xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_20px_70px_rgba(15,23,42,0.12)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-[0_20px_70px_rgba(0,0,0,0.5)] dark:backdrop-blur-xl md:grid-cols-2">
        {/* brand panel */}
        <div className="relative hidden flex-col justify-between overflow-hidden bg-gradient-to-br from-[#16233f] via-[#101a30] to-[#16233f] p-8 md:flex">
          <div className="absolute -left-16 -top-16 h-56 w-56 animate-pulse-soft rounded-full bg-amber-400/20 blur-3xl" />
          <div className="absolute -bottom-20 -right-10 h-56 w-56 animate-pulse-soft rounded-full bg-amber-500/10 blur-3xl [animation-delay:1s]" />
          <div
            className="pointer-events-none absolute inset-0 opacity-[0.07]"
            style={{
              backgroundImage: "radial-gradient(circle, #fff 1px, transparent 1px)",
              backgroundSize: "20px 20px",
            }}
          />

          <div className="relative">
            <div className="relative flex h-11 w-11 items-center justify-center">
              <span className="absolute inset-0 animate-ping rounded-xl bg-amber-400/40 [animation-duration:2.4s]" />
              <div className="relative flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-amber-400 to-amber-500 text-[#16233f] shadow-[0_0_30px_rgba(245,158,11,0.35)]">
                <IconHeartPulse className="h-5 w-5" />
              </div>
            </div>
            <p className="font-display mt-6 text-2xl font-semibold leading-tight text-white">
              Smart Bandage
              <br />
              <span className="bg-gradient-to-r from-amber-300 to-amber-500 bg-clip-text text-transparent">Care Platform</span>
            </p>
            <p className="mt-3 max-w-xs text-sm text-slate-400">
              Continuous wound and vitals telemetry, streamed in real time from every connected sensor.
            </p>
          </div>

          <ul className="relative space-y-3 text-xs text-slate-400">
            <li className="flex items-center gap-2">
              <IconZap className="h-3.5 w-3.5 text-amber-300" /> Live sensor streaming over WebSocket
            </li>
            <li className="flex items-center gap-2">
              <IconActivity className="h-3.5 w-3.5 text-emerald-300" /> Signal quality &amp; anomaly detection
            </li>
            <li className="flex items-center gap-2">
              <IconShield className="h-3.5 w-3.5 text-amber-200" /> Encrypted, token-authenticated access
            </li>
          </ul>
        </div>

        {/* form panel */}
        <form onSubmit={handleSubmit} className="space-y-5 p-8">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-amber-600 dark:text-amber-300/80">Sign in</p>
            <h1 className="font-display mt-1 text-xl font-semibold text-slate-900 dark:text-white">Monitoring dashboard</h1>
          </div>

          <label className="block text-sm">
            <span className="mb-1.5 block text-slate-500 dark:text-slate-400">Username</span>
            <input
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1.5 block text-slate-500 dark:text-slate-400">Password</span>
            <input
              type="password"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-slate-900 outline-none transition focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/20 dark:border-white/10 dark:bg-slate-950/60 dark:text-slate-100 dark:focus:border-amber-400/60 dark:focus:ring-amber-400/20"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {(error || notice) && (
            <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-600 dark:border-rose-800/60 dark:bg-rose-950/40 dark:text-rose-300">
              {error ?? notice}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-gradient-to-r from-amber-400 to-amber-500 py-2.5 font-medium text-[#16233f] shadow-[0_8px_24px_rgba(245,158,11,0.25)] transition hover:brightness-105 disabled:opacity-50"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>

          <p className="text-center text-xs text-slate-500">
            Dev default: <span className="font-mono text-slate-600 dark:text-slate-400">admin / admin</span> -- see backend/app/security.py
          </p>
        </form>
      </div>
    </div>
  );
}
