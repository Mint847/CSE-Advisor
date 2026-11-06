// Everything that talks to Firebase Auth. Loaded only when sign-in is on (session.ts imports it on demand), so the
// local, sign-in-off app never downloads the SDK. Each action returns a message to show, or null on success.
import { initializeApp } from 'firebase/app'
import {
  browserLocalPersistence, browserSessionPersistence, connectAuthEmulator, createUserWithEmailAndPassword, deleteUser, EmailAuthProvider,
  getAuth, getRedirectResult, GoogleAuthProvider, onAuthStateChanged, reauthenticateWithCredential, reauthenticateWithPopup,
  reload, sendEmailVerification, sendPasswordResetEmail, setPersistence, signInWithEmailAndPassword, signInWithPopup,
  signInWithRedirect, signOut as fbSignOut, type Auth, type User,
} from 'firebase/auth'
import { authErrorMessage, isQuietAuthError, primaryProvider, PROVIDER_LABELS, shouldRedirectAfter } from './rules'

export interface AuthConfig {
  enabled: boolean
  firebase?: { apiKey: string; projectId: string; appId: string; messagingSenderId: string; storageBucket: string; authDomain: string }
  ownDomains?: string[]
  supportEmail?: string
  appName?: string
  emulator?: string
}

const KEEP_KEY = 'cse_keep_signed_in'
const REDIRECT_KEY = 'cse_redirect_signin'

let auth: Auth

/** Start Firebase once. The sign-in helper runs on the app's own domain when the app is served from one of them
 *  (first-party, which Safari and home-screen apps need; the server proxies /__/auth/*), else on firebaseapp.com. */
export function start(cfg: AuthConfig, onUser: (u: User | null) => void) {
  const fb = cfg.firebase!
  const own = (cfg.ownDomains ?? []).includes(location.hostname)
  const app = initializeApp({ ...fb, authDomain: own ? location.hostname : fb.authDomain })
  auth = getAuth(app)
  if (cfg.emulator) connectAuthEmulator(auth, cfg.emulator, { disableWarnings: true })   // local testing (demo-* project)
  onAuthStateChanged(auth, onUser)
}

export const currentUser = () => auth?.currentUser ?? null
export const idToken = async () => (auth?.currentUser ? auth.currentUser.getIdToken() : null)

// ---------------------------------------------------------------- "Keep me signed in"
export function keepSignedIn(): boolean { try { return localStorage.getItem(KEEP_KEY) !== 'false' } catch { return true } }
export function setKeepSignedIn(on: boolean) { try { localStorage.setItem(KEEP_KEY, on ? 'true' : 'false') } catch { /* storage blocked */ } }
// before every sign-in call: LOCAL survives closing the browser, SESSION ends with the tab. Some private modes reject it.
const applyPersistence = () => setPersistence(auth, keepSignedIn() ? browserLocalPersistence : browserSessionPersistence).catch(() => {})

// ---------------------------------------------------------------- email
export async function emailSignup(email: string, password: string): Promise<string | null> {
  try {
    await applyPersistence()
    const cred = await createUserWithEmailAndPassword(auth, email, password)
    sendEmailVerification(cred.user).catch(e => console.warn('Verification email not sent:', e?.code))
    return null
  } catch (err) {
    console.warn('sign-up failed:', (err as { code?: string })?.code)
    return authErrorMessage(err)
  }
}

export async function emailLogin(email: string, password: string): Promise<string | null> {
  if (!email.trim() || !password) return 'Enter your email and password.'
  try { await applyPersistence(); await signInWithEmailAndPassword(auth, email.trim(), password); return null }
  catch (err) { console.warn('log-in failed:', (err as { code?: string })?.code); return authErrorMessage(err) }
}

// ---------------------------------------------------------------- Google (sign-in and sign-up are one flow)
const isHomeScreenApp = () => (navigator as Navigator & { standalone?: boolean }).standalone === true || matchMedia('(display-mode: standalone)').matches

async function redirectSignIn(provider: GoogleAuthProvider): Promise<string | null> {
  try { sessionStorage.setItem(REDIRECT_KEY, 'Google') } catch { /* storage blocked */ }
  try { await signInWithRedirect(auth, provider); return null }
  catch (e) { try { sessionStorage.removeItem(REDIRECT_KEY) } catch { /* */ } return authErrorMessage(e, 'Google') }
}

export async function googleSignIn(): Promise<string | null> {
  const provider = new GoogleAuthProvider()
  provider.setCustomParameters({ prompt: 'select_account' })
  try {
    await applyPersistence()
    if (isHomeScreenApp()) return redirectSignIn(provider)     // a pop-up opens in Safari outside the app and can't report back
    await signInWithPopup(auth, provider)
    return null
  } catch (err) {
    if (isQuietAuthError(err)) return null
    if (shouldRedirectAfter(err)) return redirectSignIn(provider)
    console.warn('Google sign-in failed:', (err as { code?: string })?.code)
    return authErrorMessage(err, 'Google')
  }
}

/** Once, when the signed-out pages load: the result of a redirect sign-in, so a failed one doesn't vanish silently. */
export async function redirectError(): Promise<string | null> {
  const pending = () => { let p: string | null = null; try { p = sessionStorage.getItem(REDIRECT_KEY); sessionStorage.removeItem(REDIRECT_KEY) } catch { /* */ } return p }
  try {
    const res = await getRedirectResult(auth)
    const p = pending()
    return p && !res?.user && !auth.currentUser ? p + " sign-in didn't finish. Please try again." : null
  } catch (err) { return authErrorMessage(err, pending() || undefined) }
}

// ---------------------------------------------------------------- passwords
export async function sendReset(email: string): Promise<string | null> {
  if (!email.trim()) return 'Enter your email address.'
  try { await sendPasswordResetEmail(auth, email.trim()); return null }
  catch (err) {
    // with email enumeration protection an unknown address is not an error; anything left is shown neutrally
    const code = (err as { code?: string })?.code
    return code === 'auth/user-not-found' ? null : authErrorMessage(err)
  }
}

export async function sendOwnPasswordReset(): Promise<string> {
  const u = auth.currentUser
  if (!u?.email) return ''
  try { await sendPasswordResetEmail(auth, u.email); return "We've sent a password reset link to " + u.email }
  catch (err) { return authErrorMessage(err) }
}

// ---------------------------------------------------------------- email verification
export async function resendVerification(): Promise<string> {
  const u = auth.currentUser
  if (!u) return ''
  try { await sendEmailVerification(u); return 'Verification email sent to ' + u.email }
  catch (err) { return authErrorMessage(err) }
}

export async function checkVerified(): Promise<{ text: string; verified: boolean }> {
  const u = auth.currentUser
  if (!u) return { text: '', verified: false }
  try {
    await reload(u)
    const ok = !!auth.currentUser?.emailVerified
    if (ok) await auth.currentUser?.getIdToken(true)        // the server reads email_verified from the token
    return { text: ok ? 'Email verified. Thank you!' : 'Not verified yet. Open the link in the email we sent.', verified: ok }
  } catch (err) { return { text: authErrorMessage(err), verified: false } }
}

// ---------------------------------------------------------------- sign out, delete
export async function signOut(): Promise<string | null> {
  try { await fbSignOut(auth); return null } catch { return "Couldn't sign out. Please try again." }
}

/** Confirm identity first (Firebase needs a recent sign-in, and data must not go while the account survives), then
 *  `deleteData` (the server deletes this account's rows), then the Firebase account. */
export async function deleteAccount(typed: string, password: string, deleteData: () => Promise<void>): Promise<string | null> {
  const u = auth.currentUser
  if (!u) return null
  if (typed.trim() !== 'DELETE') return 'Type DELETE to confirm.'
  const prov = primaryProvider(u)
  try {
    if (prov === 'password') {
      if (!password) return 'Enter your password to confirm.'
      await reauthenticateWithCredential(u, EmailAuthProvider.credential(u.email || '', password))
    } else {
      await reauthenticateWithPopup(u, new GoogleAuthProvider())
    }
    await deleteData()
    await deleteUser(u)
    return null
  } catch (e) {
    if (isQuietAuthError(e)) return 'Confirm with ' + (PROVIDER_LABELS[prov] || 'your sign-in method') + ' to delete your account.'
    const code = (e as { code?: string })?.code ?? ''
    if (['auth/wrong-password', 'auth/invalid-credential', 'auth/invalid-login-credentials'].includes(code)) return 'Incorrect password.'
    if (!code && e instanceof Error) return e.message                 // the server refused to delete the data
    return authErrorMessage(e)
  }
}
