// The signed-out state: log in, sign up and forgot password (three routes). Any other address shows the log-in page and
// is remembered, so the app opens it after signing in.
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { checkPasswordRules, RULE_TEXT, type FormError } from '../auth/rules'
import {
  emailLogin, emailSignup, googleSignIn, keepSignedIn, redirectError, sendReset, setKeepSignedIn, useSession,
} from '../auth/session'

export const AFTER_KEY = 'cse_after_signin'
const AUTH_PATHS = ['/login', '/signup', '/forgot']

function Shell({ title, children }: { title: string; children: ReactNode }) {
  const { config } = useSession()
  return (
    <div className="flex min-h-screen flex-col items-center bg-bg px-4 py-10">
      <div className="mb-5 flex items-center gap-2">
        <img src="/favicon.svg" alt="" className="h-6 w-6" />
        <span className="text-[15px] font-semibold tracking-wider text-amber">{(config?.appName ?? 'CSE Advisor').toUpperCase()}</span>
      </div>
      <main className="card w-full max-w-sm">
        <h1 className="panel-title">{title}</h1>
        <div className="space-y-3 p-4">{children}</div>
      </main>
      <p className="mt-4 max-w-sm text-center text-[11px] text-muted">An analysis tool for the Colombo Stock Exchange. Not financial advice.</p>
    </div>
  )
}

function Field({ label, invalid, ...p }: { label: string; invalid?: boolean } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block">
      <span className="label mb-1 block">{label}</span>
      <input {...p} className={`input w-full !py-1.5 ${invalid ? '!border-bear' : ''}`} aria-invalid={invalid || undefined} />
    </label>
  )
}

function Keep() {
  const [on, setOn] = useState(keepSignedIn)
  return (
    <label className="flex items-center gap-2 text-[12px]">
      <input type="checkbox" checked={on} onChange={e => { setOn(e.target.checked); setKeepSignedIn(e.target.checked) }} />
      Keep me signed in
    </label>
  )
}

function Err({ msg }: { msg: string | null }) {
  return msg ? <p role="alert" className="border border-bear/60 bg-bear/10 px-2 py-1.5 text-[12px] text-bear">{msg}</p> : null
}

function GoogleButton({ busy, setBusy, setErr }: { busy: boolean; setBusy: (b: boolean) => void; setErr: (m: string | null) => void }) {
  return (
    <button type="button" className="btn w-full justify-center !py-1.5" disabled={busy}
            onClick={async () => { setErr(null); setBusy(true); try { setErr(await googleSignIn()) } finally { setBusy(false) } }}>
      <svg aria-hidden="true" width="14" height="14" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
      Continue with Google
    </button>
  )
}

const Or = () => <div className="flex items-center gap-2 text-[11px] text-muted"><span className="h-px flex-1 bg-line" />OR<span className="h-px flex-1 bg-line" /></div>

let redirectChecked = false

function Login() {
  const loc = useLocation()
  const { error } = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(error)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (redirectChecked) return
    redirectChecked = true
    redirectError().then(m => { if (m) setErr(m) })
  }, [])
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setBusy(true)
    try { setErr(await emailLogin(email, password)) } finally { setBusy(false) }   // success: the session moves to the app
  }
  const edit = (f: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => { f(e.target.value); setErr(null) }
  return (
    <Shell title="Log in">
      <form className="space-y-3" onSubmit={submit} noValidate>
        <Field label="Email" type="email" autoComplete="email" value={email} onChange={edit(setEmail)} autoFocus={!loc.state} />
        <Field label="Password" type="password" autoComplete="current-password" value={password} onChange={edit(setPassword)} />
        <div className="flex items-center justify-between gap-2">
          <Keep />
          <Link to="/forgot" state={{ email }} className="text-[12px] text-blue hover:underline">Forgot password?</Link>
        </div>
        <Err msg={err} />
        <button className="btn btn-primary w-full justify-center !py-1.5" disabled={busy}>{busy ? 'Logging in…' : 'Log in'}</button>
      </form>
      <Or />
      <GoogleButton busy={busy} setBusy={setBusy} setErr={setErr} />
      <p className="text-center text-[12px] text-muted">New here? <Link to="/signup" className="text-blue hover:underline">Create an account</Link></p>
    </Shell>
  )
}

function Signup() {
  const [f, setF] = useState({ firstName: '', lastName: '', email: '', password: '', confirm: '' })
  const [err, setErr] = useState<FormError | null>(null)
  const [busy, setBusy] = useState(false)
  const rules = checkPasswordRules(f.password, f.confirm)
  const edit = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => { setF({ ...f, [k]: e.target.value }); setErr(null) }
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setBusy(true)
    try { setErr(await emailSignup(f)) } finally { setBusy(false) }
  }
  return (
    <Shell title="Create an account">
      <form className="space-y-3" onSubmit={submit} noValidate>
        <div className="grid grid-cols-2 gap-2">
          <Field label="First name" autoComplete="given-name" value={f.firstName} onChange={edit('firstName')} autoFocus />
          <Field label="Last name" autoComplete="family-name" value={f.lastName} onChange={edit('lastName')} />
        </div>
        <Field label="Email" type="email" autoComplete="email" value={f.email} onChange={edit('email')} />
        <Field label="Password" type="password" autoComplete="new-password" value={f.password} onChange={edit('password')}
               invalid={err?.field === 'password'} aria-describedby="pw-rules" />
        <ul id="pw-rules" className="grid grid-cols-2 gap-x-2 text-[11px]" aria-label="Password requirements">
          {RULE_TEXT.map(([k, t]) => (
            <li key={k} className={rules[k] ? 'text-bull' : 'text-muted'}>{rules[k] ? '✓' : '○'} {t}</li>
          ))}
          <li className={rules.match ? 'text-bull' : 'text-muted'}>{rules.match ? '✓' : '○'} passwords match</li>
        </ul>
        <Field label="Confirm password" type="password" autoComplete="new-password" value={f.confirm} onChange={edit('confirm')}
               invalid={err?.field === 'confirm'} />
        <Keep />
        <Err msg={err?.message ?? null} />
        <button className="btn btn-primary w-full justify-center !py-1.5" disabled={busy}>{busy ? 'Creating…' : 'Create account'}</button>
      </form>
      <Or />
      <GoogleButton busy={busy} setBusy={setBusy} setErr={m => setErr(m ? { field: null, message: m } : null)} />
      <p className="text-center text-[12px] text-muted">Have an account? <Link to="/login" className="text-blue hover:underline">Log in</Link></p>
    </Shell>
  )
}

function Forgot() {
  const loc = useLocation()
  const { config } = useSession()
  const [email, setEmail] = useState<string>((loc.state as { email?: string } | null)?.email ?? '')
  const [sent, setSent] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const send = async (e?: FormEvent) => {
    e?.preventDefault()
    setErr(null); setBusy(true)
    try {
      const m = await sendReset(email)
      if (m) setErr(m); else setSent(email.trim())
    } finally { setBusy(false) }
  }
  return (
    <Shell title="Reset your password">
      {sent ? (
        <>
          <p className="text-[13px]" role="status">If an account exists for <b className="text-fg">{sent}</b>, a reset link is on its way. Check your inbox and spam folder.</p>
          <Err msg={err} />
          <div className="flex gap-2">
            <button className="btn" disabled={busy} onClick={() => send()}>{busy ? 'Sending…' : 'Resend'}</button>
            <Link to="/login" className="btn btn-primary">Back to log in</Link>
          </div>
        </>
      ) : (
        <form className="space-y-3" onSubmit={send} noValidate>
          <p className="text-[12px] text-muted">Enter the email you signed up with and we'll send a link to choose a new password.</p>
          <Field label="Email" type="email" autoComplete="email" value={email} onChange={e => { setEmail(e.target.value); setErr(null) }} autoFocus />
          <Err msg={err} />
          <button className="btn btn-primary w-full justify-center !py-1.5" disabled={busy}>{busy ? 'Sending…' : 'Send reset link'}</button>
        </form>
      )}
      <p className="border-t border-line pt-3 text-[11px] text-muted">
        Not sure which email you used? Try "Continue with Google" on the log-in page, or try your other email addresses.
        {config?.supportEmail ? <> Still stuck? Contact <a className="text-blue hover:underline" href={`mailto:${config.supportEmail}`}>{config.supportEmail}</a>.</> : null}
      </p>
      {!sent && <p className="text-center text-[12px]"><Link to="/login" className="text-blue hover:underline">Back to log in</Link></p>}
    </Shell>
  )
}

export default function SignedOut() {
  const loc = useLocation()
  // remember the page that was asked for; the app opens it after signing in
  useEffect(() => {
    if (!AUTH_PATHS.includes(loc.pathname)) {
      try { sessionStorage.setItem(AFTER_KEY, loc.pathname + loc.search) } catch { /* */ }
    }
  }, [loc.pathname, loc.search])
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot" element={<Forgot />} />
      <Route path="*" element={AUTH_PATHS.includes(loc.pathname) ? <Login /> : <Navigate to="/login" replace />} />
    </Routes>
  )
}

export function StartingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-[12px] text-muted">
        <img src="/favicon.svg" alt="" className="h-5 w-5 animate-pulse" />
        <span className="tracking-wider text-amber">CSE ADVISOR</span> starting…
      </div>
    </div>
  )
}
