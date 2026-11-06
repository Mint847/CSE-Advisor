// Account pieces shown inside the app: the avatar, the account menu (header) and the "verify your email" banner.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { hasUnsyncedChanges } from '../api'
import { initials, PROVIDER_LABELS } from '../auth/rules'
import {
  checkVerified, dismissVerifyBanner, resendVerification, signOut, useSession, verifyBannerDismissed,
} from '../auth/session'

export const fullName = (p: { first_name?: string; last_name?: string } | null | undefined) =>
  [p?.first_name, p?.last_name].filter(Boolean).join(' ')

/** Google photo when there is one (no-referrer, or Google's image server may refuse it), else initials in a circle.
 *  `named` = the name is shown next to it, so the picture is decorative. */
export function Avatar({ size = 22, named = false }: { size?: number; named?: boolean }) {
  const { user, profile } = useSession()
  const [failed, setFailed] = useState(false)
  const photo = user?.photo
  useEffect(() => setFailed(false), [photo])
  const name = fullName(profile) || user?.email || ''
  const style = { width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.42)) }
  if (photo && !failed)
    return <img src={photo} referrerPolicy="no-referrer" onError={() => setFailed(true)} alt={named ? '' : name}
                className="shrink-0 rounded-full object-cover" style={style} />
  return (
    <span role={named ? undefined : 'img'} aria-label={named ? undefined : name} aria-hidden={named || undefined}
          className="inline-flex shrink-0 items-center justify-center rounded-full bg-amber font-semibold text-black" style={style}>
      {initials(profile?.first_name, profile?.last_name)}
    </span>
  )
}

/** Opens from the avatar in the header: who is signed in, and the account actions. */
export function AccountMenu() {
  const { enabled, user, profile } = useSession()
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('mousedown', onDown) }
  }, [open])
  if (!enabled || !user) return null
  return (
    <div className="relative" ref={ref}>
      <button className="flex items-center rounded-full" aria-haspopup="menu" aria-expanded={open} aria-label="Account menu" onClick={() => setOpen(o => !o)}>
        <Avatar />
      </button>
      {open && (
        <div role="menu" className="card absolute right-0 top-8 z-50 w-72 max-w-[calc(100vw-24px)] text-[12px]">
          <div className="flex items-center gap-2 border-b border-line p-3">
            <Avatar size={36} named />
            <div className="min-w-0">
              <div className="truncate font-semibold text-fg">{fullName(profile) || user.email}</div>
              <div className="truncate text-muted">{user.email}</div>
              <div className="text-[11px] text-muted">Signed in with {PROVIDER_LABELS[user.provider] ?? user.provider}{profile?.admin ? ' · admin' : ''}</div>
            </div>
          </div>
          <Link role="menuitem" to="/account" className="block px-3 py-1.5 hover:bg-panel-3" onClick={() => setOpen(false)}>Profile &amp; account</Link>
          <Link role="menuitem" to="/settings" className="block px-3 py-1.5 hover:bg-panel-3" onClick={() => setOpen(false)}>Settings</Link>
          <button role="menuitem" className="block w-full px-3 py-1.5 text-left hover:bg-panel-3"
                  onClick={async () => { const m = await signOut(hasUnsyncedChanges()); if (m) setErr(m) }}>Sign out</button>
          {err && <p role="alert" className="px-3 pb-2 text-bear">{err}</p>}
        </div>
      )}
    </div>
  )
}

/** Resend, with a 60 s cooldown (Firebase rate-limits verification emails). */
export function useResend() {
  const [left, setLeft] = useState(0)
  useEffect(() => { if (!left) return; const t = window.setTimeout(() => setLeft(l => l - 1), 1000); return () => window.clearTimeout(t) }, [left])
  const resend = async () => { const m = await resendVerification(); setLeft(60); return m }
  return { left, resend }
}

/** Email-and-password accounts that haven't verified yet. Dismissed for this browser session only. */
export function VerifyBanner() {
  const { user } = useSession()
  const [hidden, setHidden] = useState(verifyBannerDismissed)
  const [msg, setMsg] = useState<string | null>(null)
  const { left, resend } = useResend()
  if (!user?.needsVerification || hidden) return null
  return (
    <div role="status" className="border-b border-amber/40 bg-[#1d1405]">
      <div className="mx-auto flex max-w-[1700px] flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-[12px]">
        <span className="font-semibold text-amber">VERIFY YOUR EMAIL</span>
        <span className="text-fg">We sent a link to {user.email}.</span>
        <button className="btn !py-0 text-[11px]" disabled={left > 0} onClick={async () => setMsg(await resend())}>
          {left > 0 ? `Resend in ${left}s` : 'Resend email'}
        </button>
        <button className="btn !py-0 text-[11px]" onClick={async () => setMsg((await checkVerified()).text)}>I've verified</button>
        {msg && <span className="text-muted">{msg}</span>}
        <button className="ml-auto text-muted hover:text-fg" aria-label="Dismiss" onClick={() => { dismissVerifyBanner(); setHidden(true) }}>✕</button>
      </div>
    </div>
  )
}
