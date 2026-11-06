// The session: 'starting' until Firebase answers, then 'signedOut' (the sign-in pages) or 'ready' (the app, once the
// profile is loaded). The UI renders from useSession(). With sign-in off (no FIREBASE_PROJECT_ID on the server) the app
// goes straight to 'ready' as the local user, exactly as before accounts existed.
import { useSyncExternalStore } from 'react'
import type { User } from 'firebase/auth'
import type { AuthConfig } from './firebase'
import { needsEmailVerification, photoOf, primaryProvider, signupFormError, type FormError, type SignupForm } from './rules'

export type Status = 'starting' | 'signedOut' | 'ready'
export interface Profile {
  uid: string; email: string; first_name: string; last_name: string; admin: boolean; provider: string; auth: boolean
  created_at?: string | null; local_data?: Record<string, number>; telegram_owner?: boolean
}
/** What the UI needs from the Firebase user (a plain snapshot, so React sees changes). */
export interface AccountUser { uid: string; email: string; emailVerified: boolean; displayName: string; photo: string; provider: string; needsVerification: boolean }
interface State { status: Status; enabled: boolean; config: AuthConfig | null; user: AccountUser | null; profile: Profile | null; error: string | null }

type Impl = typeof import('./firebase')
let impl: Impl | null = null
let state: State = { status: 'starting', enabled: false, config: null, user: null, profile: null, error: null }
const subs = new Set<() => void>()
const set = (p: Partial<State>) => { state = { ...state, ...p }; subs.forEach(f => f()) }
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }

export function useSession() { return useSyncExternalStore(subscribe, () => state) }
export const session = () => state

const snapshot = (u: User): AccountUser => ({
  uid: u.uid, email: u.email ?? '', emailVerified: u.emailVerified, displayName: u.displayName ?? '', photo: photoOf(u),
  provider: primaryProvider(u), needsVerification: needsEmailVerification(u),
})

/** Authorization header for /api calls (Firebase refreshes the token by itself). */
export async function authHeader(): Promise<Record<string, string>> {
  const t = impl ? await impl.idToken() : null
  return t ? { Authorization: 'Bearer ' + t } : {}
}

async function me(method: 'GET' | 'POST' | 'PUT', body?: unknown): Promise<Profile> {
  const r = await fetch('/api/me', { method, headers: { 'Content-Type': 'application/json', ...(await authHeader()) }, body: body ? JSON.stringify(body) : undefined })
  if (!r.ok) { let m = `${r.status}`; try { m = (await r.json()).detail ?? m } catch { /* */ } throw new Error(m) }
  return r.json()
}

// The name typed at sign-up. Firebase signs the new account in (onAuthStateChanged) while createUser... is still running,
// before the form could save the name, so it is kept here and sent with the first profile call.
let pendingName: { first_name: string; last_name: string } | null = null

async function onUser(u: User | null) {
  if (!u) { pendingName = null; set({ status: 'signedOut', user: null, profile: null }); return }
  set({ user: snapshot(u) })
  let profile: Profile | null = null
  try { profile = await me('POST', pendingName ?? {}) }
  catch (e) { console.warn('Profile not loaded yet:', e) }                    // keep going; the app retries on its next call
  pendingName = null
  if (impl?.currentUser()?.uid === u.uid) set({ status: 'ready', profile, error: null })
}

let started = false
/** Call once at app start. */
export async function startSession() {
  if (started) return
  started = true
  // don't spin forever if the server or Firebase never answers (offline, blocked scripts)
  const timer = window.setTimeout(() => { if (state.status === 'starting') set({ status: 'signedOut', error: "Can't reach the server. Check your connection and try again." }) }, 12000)
  let cfg: AuthConfig
  try {
    const r = await fetch('/api/auth/config')
    cfg = await r.json()
  } catch {
    window.clearTimeout(timer)
    set({ status: 'signedOut', error: "Can't reach the server. Check your connection and try again." })
    return
  }
  set({ config: cfg, enabled: !!cfg.enabled })
  if (!cfg.enabled) {
    window.clearTimeout(timer)
    let profile: Profile | null = null
    try { profile = await me('GET') } catch { /* server still starting: the pages show their own errors */ }
    set({ status: 'ready', profile })
    return
  }
  try {
    impl = await import('./firebase')
    impl.start(cfg, u => { window.clearTimeout(timer); void onUser(u) })
  } catch (e) {
    window.clearTimeout(timer)
    console.warn('Firebase failed to load:', e)
    set({ status: 'signedOut', error: "Sign-in couldn't load. Check your connection and reload the page." })
  }
}

const need = () => { if (!impl) throw new Error('Sign-in is off'); return impl }

// ---------------------------------------------------------------- actions for the pages (message or null)
export const keepSignedIn = () => impl?.keepSignedIn() ?? true
export const setKeepSignedIn = (on: boolean) => impl?.setKeepSignedIn(on)

export async function emailSignup(f: SignupForm): Promise<FormError | null> {
  const form = { ...f, firstName: f.firstName.trim(), lastName: f.lastName.trim(), email: f.email.trim() }
  const problem = signupFormError(form)
  if (problem) return problem
  pendingName = { first_name: form.firstName, last_name: form.lastName }     // before the account exists (see above)
  const msg = await need().emailSignup(form.email, form.password)
  if (msg) { pendingName = null; return { field: null, message: msg } }
  return null
}
export const emailLogin = (email: string, password: string) => need().emailLogin(email, password)
export const googleSignIn = () => need().googleSignIn()
export const redirectError = () => (impl ? impl.redirectError() : Promise.resolve(null))
export const sendReset = (email: string) => need().sendReset(email)
export const sendOwnPasswordReset = () => need().sendOwnPasswordReset()
export const resendVerification = () => need().resendVerification()

export async function checkVerified() {
  const r = await need().checkVerified()
  const u = impl?.currentUser()
  if (u) set({ user: snapshot(u) })
  return r
}

// ---------------------------------------------------------------- verification banner (hidden for this browser session)
const VERIFY_DISMISSED_KEY = 'cse_verify_dismissed'
export function dismissVerifyBanner() { try { sessionStorage.setItem(VERIFY_DISMISSED_KEY, state.user?.uid || '') } catch { /* */ } }
export function verifyBannerDismissed() { try { return !!state.user && sessionStorage.getItem(VERIFY_DISMISSED_KEY) === state.user.uid } catch { return false } }

// ---------------------------------------------------------------- profile
export async function reloadProfile() { try { set({ profile: await me('GET') }) } catch (e) { console.warn(e) } }
export async function saveName(first: string, last: string): Promise<string | null> {
  try { set({ profile: await me('PUT', { first_name: first, last_name: last }) }); return null }
  catch (e) { return (e as Error).message || 'Something went wrong. Please try again.' }
}

// ---------------------------------------------------------------- sign out, delete
/** Remove what this app keeps about the user on the device (it may be shared). Watchlist, positions and preferences
 *  live on the server, so only small session keys are here. */
function clearDeviceData() {
  try {
    sessionStorage.removeItem(VERIFY_DISMISSED_KEY)
    for (const k of Object.keys(localStorage)) if (k.startsWith('cse_user_')) localStorage.removeItem(k)
  } catch { /* storage blocked */ }
}

export async function signOut(hasUnsyncedChanges = false): Promise<string | null> {
  if (!impl) return null
  if (hasUnsyncedChanges && !confirm("Some changes haven't synced to your account yet. If you sign out now they will be lost. Sign out anyway?")) return null
  clearDeviceData()
  return impl.signOut()                                                // onAuthStateChanged then shows the sign-in page
}

export async function deleteAccount(typed: string, password: string): Promise<string | null> {
  return need().deleteAccount(typed, password, async () => {
    const r = await fetch('/api/me', { method: 'DELETE', headers: await authHeader() })
    if (!r.ok) throw new Error("Couldn't delete your data. Your account was not deleted. Please try again.")
    clearDeviceData()
  })
}
