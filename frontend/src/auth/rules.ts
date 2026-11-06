// Sign-in rules as pure functions (no Firebase import), so they can be unit-tested: `npm test`.

type AuthErr = { code?: string } | null | undefined

/** A friendly message for a Firebase error. Never the raw message or code, and never whether an email has an account. */
export function authErrorMessage(err: unknown, provider = 'This'): string {
  switch ((err as AuthErr)?.code || '') {
    case 'auth/invalid-credential': case 'auth/invalid-login-credentials':
    case 'auth/wrong-password': case 'auth/user-not-found': return 'Incorrect email or password.'
    case 'auth/invalid-email': return 'Enter a valid email address.'
    case 'auth/missing-password': return 'Enter your password.'
    case 'auth/email-already-in-use': return 'This email already has an account. Log in instead.'
    case 'auth/weak-password': case 'auth/password-does-not-meet-requirements':
      return 'Choose a stronger password that meets the requirements below.'
    case 'auth/too-many-requests': return 'Too many attempts. Wait a few minutes and try again.'
    case 'auth/network-request-failed': return "Can't reach the server. Check your connection and try again."
    case 'auth/user-disabled': return 'This account has been disabled.'
    case 'auth/operation-not-allowed': case 'auth/configuration-not-found': case 'auth/invalid-oauth-provider':
      return provider + " sign-in isn't available yet. Use Google or email instead."
    case 'auth/account-exists-with-different-credential':
      return 'This email already has an account. Log in with the method you used before.'
    case 'auth/unauthorized-domain': return "Sign-in isn't allowed from this web address."
    case 'auth/popup-blocked': return 'Your browser blocked the sign-in window. Allow pop-ups and try again.'
    default: return 'Something went wrong. Please try again.'
  }
}

/** The user closed the Google window: show nothing. */
export const isQuietAuthError = (e: unknown) => {
  const c = (e as AuthErr)?.code
  return c === 'auth/popup-closed-by-user' || c === 'auth/cancelled-popup-request'
}
/** The pop-up can't work here: fall back to a full-page redirect. */
export const shouldRedirectAfter = (e: unknown) => {
  const c = (e as AuthErr)?.code
  return c === 'auth/popup-blocked' || c === 'auth/operation-not-supported-in-this-environment'
}

export const SYMBOLS = /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/

export interface PasswordRules { len: boolean; upper: boolean; lower: boolean; num: boolean; special: boolean; match: boolean; isValid: boolean }

/** The same rules as the project's server-side password policy (Firebase console, Authentication > Settings). */
export function checkPasswordRules(password = '', confirm = ''): PasswordRules {
  const r = {
    len: password.length >= 8, upper: /[A-Z]/.test(password), lower: /[a-z]/.test(password),
    num: /[0-9]/.test(password), special: SYMBOLS.test(password), match: password.length > 0 && password === confirm,
  }
  return { ...r, isValid: r.len && r.upper && r.lower && r.num && r.special && r.match }
}

export const RULE_TEXT: [keyof Omit<PasswordRules, 'isValid' | 'match'>, string][] = [
  ['len', 'at least 8 characters'], ['upper', 'an uppercase letter'], ['lower', 'a lowercase letter'], ['num', 'a number'], ['special', 'a symbol'],
]

export function passwordNeedsText(r: PasswordRules): string {
  return 'Your password needs ' + RULE_TEXT.filter(([k]) => !r[k]).map(([, t]) => t).join(', ') + '.'
}

export interface SignupForm { firstName: string; lastName: string; email: string; password: string; confirm: string }
export type FormError = { field: 'password' | 'confirm' | null; message: string }

/** Sign-up checks in order, one message at a time; `field` is the input to highlight. */
export function signupFormError(f: SignupForm): FormError | null {
  if (!f.firstName.trim() || !f.lastName.trim() || !f.email.trim()) return { field: null, message: 'Enter your first name, last name and email address.' }
  if (!f.password) return { field: 'password', message: 'Create a password to continue.' }
  if (!f.confirm) return { field: 'confirm', message: 'Confirm your password.' }
  if (f.password !== f.confirm) return { field: 'confirm', message: "Passwords don't match." }
  const rules = checkPasswordRules(f.password, f.confirm)
  if (!rules.isValid) return { field: 'password', message: passwordNeedsText(rules) }
  return null
}

interface ProviderInfo { providerId: string; photoURL?: string | null }
interface UserLike { emailVerified?: boolean; photoURL?: string | null; providerData?: ProviderInfo[] }

export const PROVIDER_LABELS: Record<string, string> = { password: 'Email & password', 'google.com': 'Google' }

/** 'password' if the account can sign in with a password, else its first provider. */
export function primaryProvider(u: UserLike | null | undefined): string {
  const ids = (u?.providerData || []).map(p => p.providerId)
  return ids.includes('password') ? 'password' : ids[0] || 'password'
}

/** Only email-and-password accounts need to verify (Google has already verified the address). */
export const needsEmailVerification = (u: UserLike | null | undefined) => !!(u && !u.emailVerified && primaryProvider(u) === 'password')

export function photoOf(u: UserLike | null | undefined): string {
  if (!u) return ''
  return u.photoURL || (u.providerData || []).find(p => p.providerId === 'google.com' && p.photoURL)?.photoURL || ''
}

export function initials(first = '', last = ''): string {
  return ((first.trim()[0] || '') + (last.trim()[0] || '')).toUpperCase() || '?'
}

/** Where to go after signing in: the page asked for, but never back to a sign-in page or off-site. */
export function afterSignIn(from: string | null | undefined): string {
  if (!from || !from.startsWith('/') || from.startsWith('//')) return '/'
  return ['/login', '/signup', '/forgot'].some(p => from === p || from.startsWith(p + '?')) ? '/' : from
}
