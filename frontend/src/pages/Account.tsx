// ACCT: profile, email verification, password, sign out, delete account; and for the admin, the pre-sign-in data.
import { useState } from 'react'
import { api, hasUnsyncedChanges } from '../api'
import { PROVIDER_LABELS } from '../auth/rules'
import {
  checkVerified, deleteAccount, reloadProfile, saveName, sendOwnPasswordReset, signOut, useSession,
} from '../auth/session'
import { Avatar, fullName, useResend } from '../components/Account'
import { Section } from '../components/ui'

export default function Account() {
  const { enabled, user, profile } = useSession()
  if (!enabled) {
    return (
      <div className="mx-auto max-w-[900px] space-y-2 px-3 py-3">
        <h1 className="text-[14px] font-semibold uppercase tracking-wider text-amber">ACCT · Account</h1>
        <Section title="Sign-in is off">
          <p className="text-[12px] text-muted">The app runs as a single local user on this PC. To use accounts (email and Google sign-in, each
            account with its own watchlist and positions), set <code>FIREBASE_PROJECT_ID</code> and the other Firebase values in
            <code> backend/.env</code> (README, "Accounts") and restart.</p>
        </Section>
      </div>
    )
  }
  if (!user) return null
  return (
    <div className="mx-auto max-w-[900px] space-y-2 px-3 py-3">
      <h1 className="text-[14px] font-semibold uppercase tracking-wider text-amber">ACCT · Account</h1>
      <ProfileSection key={`${profile?.first_name}|${profile?.last_name}`} />
      <PasswordSection />
      {profile?.admin && <AdminSection />}
      <Section title="Sign out">
        <SignOutButton />
      </Section>
      <DeleteSection />
    </div>
  )
}

function ProfileSection() {
  const { user, profile } = useSession()
  const [first, setFirst] = useState(profile?.first_name ?? '')
  const [last, setLast] = useState(profile?.last_name ?? '')
  const [msg, setMsg] = useState<string | null>(null)
  const [vmsg, setVmsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { left, resend } = useResend()
  if (!user) return null
  const changed = first.trim() !== (profile?.first_name ?? '') || last.trim() !== (profile?.last_name ?? '')
  return (
    <Section title="Profile">
      <div className="flex flex-wrap items-start gap-4 text-[12px]">
        <Avatar size={56} named />
        <form className="min-w-0 flex-1 space-y-2" onSubmit={async e => {
          e.preventDefault(); setBusy(true); setMsg(null)
          try { setMsg((await saveName(first, last)) ?? 'Saved.') } finally { setBusy(false) }
        }}>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block"><span className="label mb-1 block">First name</span>
              <input className="input w-full" autoComplete="given-name" value={first} onChange={e => { setFirst(e.target.value); setMsg(null) }} /></label>
            <label className="block"><span className="label mb-1 block">Last name</span>
              <input className="input w-full" autoComplete="family-name" value={last} onChange={e => { setLast(e.target.value); setMsg(null) }} /></label>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn btn-primary" disabled={busy || !changed}>Save name</button>
            {msg && <span className="text-muted" role="status">{msg}</span>}
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 pt-1">
            <dt className="label">Email</dt><dd className="min-w-0 break-all">{user.email}</dd>
            <dt className="label">Sign-in</dt><dd>{PROVIDER_LABELS[user.provider] ?? user.provider}</dd>
            <dt className="label">Status</dt>
            <dd>{user.provider !== 'password' || user.emailVerified
              ? <span className="text-bull">Email verified</span>
              : <span className="flex flex-wrap items-center gap-2"><span className="text-warn">Email not verified</span>
                  <button type="button" className="btn !py-0 text-[11px]" disabled={left > 0} onClick={async () => setVmsg(await resend())}>{left > 0 ? `Resend in ${left}s` : 'Resend email'}</button>
                  <button type="button" className="btn !py-0 text-[11px]" onClick={async () => setVmsg((await checkVerified()).text)}>I've verified</button>
                </span>}
              {vmsg && <span className="ml-2 text-muted">{vmsg}</span>}</dd>
            {profile?.created_at && <><dt className="label">Since</dt><dd>{new Date(profile.created_at + 'Z').toLocaleDateString()}</dd></>}
          </dl>
        </form>
      </div>
    </Section>
  )
}

function PasswordSection() {
  const { user } = useSession()
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <Section title="Password">
      {user?.provider === 'password' ? (
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="text-muted">We'll email you a link to choose a new password.</span>
          <button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { setMsg(await sendOwnPasswordReset()) } finally { setBusy(false) } }}>Change password</button>
          {msg && <span role="status">{msg}</span>}
        </div>
      ) : <p className="text-[12px] text-muted">Your password is managed by Google.</p>}
    </Section>
  )
}

function AdminSection() {
  const { profile } = useSession()
  const [msg, setMsg] = useState<string | null>(null)
  const local = profile?.local_data ?? {}
  const nLocal = Object.values(local).reduce((a, b) => a + b, 0)
  return (
    <Section title="Admin">
      <div className="space-y-2 text-[12px]">
        <p className="text-muted">You can change shared market data (corporate actions, statements, sectors), run the evening job and the
          candidate ranking. Other accounts can only change their own watchlist, positions and display settings.</p>
        {nLocal > 0 && (
          <p className="flex flex-wrap items-center gap-2">
            <span>Data from before sign-in was switched on: {local.watchlist ?? 0} watchlist stocks, {local.positions ?? 0} positions, {local.user_prefs ?? 0} display settings.</span>
            <button className="btn btn-primary" onClick={async () => {
              try { const r = await api.claimLocal(); setMsg(`Moved into your account: ${r.moved.watchlist} stocks, ${r.moved.positions} positions, ${r.moved.preferences} settings.`); await reloadProfile() }
              catch (e) { setMsg((e as Error).message) }
            }}>Move it to my account</button>
          </p>
        )}
        <p className="flex flex-wrap items-center gap-2">
          <span>Telegram digest and alerts: {profile?.telegram_owner ? <b className="text-bull">your watchlist and positions</b> : 'another account (or the pre-sign-in data)'}.</span>
          {!profile?.telegram_owner && <button className="btn" onClick={async () => {
            try { await api.telegramOwner(); await reloadProfile(); setMsg('Telegram now reports on your stocks.') } catch (e) { setMsg((e as Error).message) }
          }}>Use my stocks</button>}
        </p>
        {msg && <p role="status" className="text-amber">{msg}</p>}
      </div>
    </Section>
  )
}

function SignOutButton() {
  const [err, setErr] = useState<string | null>(null)
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <span className="text-muted">Signs you out on this device. Your data stays in your account.</span>
      <button className="btn" onClick={async () => setErr(await signOut(hasUnsyncedChanges()))}>Sign out</button>
      {err && <span role="alert" className="text-bear">{err}</span>}
    </div>
  )
}

function DeleteSection() {
  const { user, profile } = useSession()
  const [typed, setTyped] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  if (!user) return null
  const emailAccount = user.provider === 'password'
  return (
    <section className="card border-bear/60">
      <h2 className="panel-title !text-bear">Delete account</h2>
      <form className="space-y-2 p-3 text-[12px]" onSubmit={async e => {
        e.preventDefault(); setErr(null); setBusy(true)
        try { const m = await deleteAccount(typed, pw); if (m) setErr(m); else setDone(true) } finally { setBusy(false) }
      }}>
        <p className="text-muted">Deletes {fullName(profile) ? <b className="text-fg">{fullName(profile)}</b> : 'this account'}, your watchlist, positions and settings. This can't be undone.
          {!emailAccount && ' Google will ask you to confirm.'}</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="block"><span className="label mb-1 block">Type DELETE to confirm</span>
            <input className="input w-full" value={typed} onChange={e => { setTyped(e.target.value); setErr(null) }} autoComplete="off" /></label>
          {emailAccount && <label className="block"><span className="label mb-1 block">Your password</span>
            <input className="input w-full" type="password" autoComplete="current-password" value={pw} onChange={e => { setPw(e.target.value); setErr(null) }} /></label>}
        </div>
        {err && <p role="alert" className="text-bear">{err}</p>}
        {done && <p role="status" className="text-bull">Your account has been deleted.</p>}
        <button className="btn !border-bear !text-bear" disabled={busy}>{busy ? 'Deleting…' : 'Delete my account'}</button>
      </form>
    </section>
  )
}
