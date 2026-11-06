// Run with `npm test` (Node's own test runner; Node 22.18+ runs TypeScript directly).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  afterSignIn, authErrorMessage, checkPasswordRules, initials, isQuietAuthError, needsEmailVerification, passwordNeedsText,
  photoOf, primaryProvider, shouldRedirectAfter, signupFormError,
} from './rules.ts'

test('wrong email and wrong password give the same message', () => {
  for (const code of ['auth/invalid-credential', 'auth/invalid-login-credentials', 'auth/wrong-password', 'auth/user-not-found'])
    assert.equal(authErrorMessage({ code }), 'Incorrect email or password.')
  assert.equal(authErrorMessage({ code: 'auth/operation-not-allowed' }, 'Google'), "Google sign-in isn't available yet. Use Google or email instead.")
  assert.equal(authErrorMessage({ code: 'auth/some-new-code' }), 'Something went wrong. Please try again.')
  assert.equal(authErrorMessage(null), 'Something went wrong. Please try again.')
})

test('closing the Google window is quiet; a blocked pop-up falls back to redirect', () => {
  assert.ok(isQuietAuthError({ code: 'auth/popup-closed-by-user' }) && isQuietAuthError({ code: 'auth/cancelled-popup-request' }))
  assert.ok(!isQuietAuthError({ code: 'auth/popup-blocked' }))
  assert.ok(shouldRedirectAfter({ code: 'auth/popup-blocked' }) && shouldRedirectAfter({ code: 'auth/operation-not-supported-in-this-environment' }))
})

test('password rules and the message list only what is missing', () => {
  const r = checkPasswordRules('abcdefgh', 'abcdefgh')
  assert.ok(r.len && r.lower && r.match && !r.upper && !r.num && !r.special && !r.isValid)
  assert.equal(passwordNeedsText(r), 'Your password needs an uppercase letter, a number, a symbol.')
  assert.ok(checkPasswordRules('Abcdef1!', 'Abcdef1!').isValid)
  assert.ok(checkPasswordRules('Abcdef1\\', 'Abcdef1\\').special)
  assert.ok(!checkPasswordRules('', '').match)
})

test('sign-up checks run in order, one message at a time', () => {
  const ok = { firstName: 'Kavi', lastName: 'Perera', email: 'k@x.org', password: 'Abcdef1!', confirm: 'Abcdef1!' }
  assert.equal(signupFormError(ok), null)
  assert.deepEqual(signupFormError({ ...ok, lastName: '  ' }), { field: null, message: 'Enter your first name, last name and email address.' })
  assert.equal(signupFormError({ ...ok, password: '', confirm: '' })?.message, 'Create a password to continue.')
  assert.deepEqual(signupFormError({ ...ok, confirm: '' }), { field: 'confirm', message: 'Confirm your password.' })
  assert.equal(signupFormError({ ...ok, confirm: 'Abcdef1?' })?.message, "Passwords don't match.")
  assert.deepEqual(signupFormError({ ...ok, password: 'short', confirm: 'short' }),
    { field: 'password', message: 'Your password needs at least 8 characters, an uppercase letter, a number, a symbol.' })
})

test('provider, verification, photo and initials', () => {
  const email = { emailVerified: false, providerData: [{ providerId: 'password' }] }
  const google = { emailVerified: true, photoURL: null, providerData: [{ providerId: 'google.com', photoURL: 'https://lh3/p.jpg' }] }
  assert.equal(primaryProvider(email), 'password')
  assert.equal(primaryProvider(google), 'google.com')
  assert.equal(primaryProvider({ providerData: [{ providerId: 'google.com' }, { providerId: 'password' }] }), 'password')
  assert.ok(needsEmailVerification(email) && !needsEmailVerification(google) && !needsEmailVerification({ ...email, emailVerified: true }))
  assert.equal(photoOf(google), 'https://lh3/p.jpg')
  assert.equal(photoOf(email), '')
  assert.equal(initials('kavi', 'perera'), 'KP')
  assert.equal(initials('', ''), '?')
})

test('after sign-in: the page asked for, never a sign-in page or another site', () => {
  assert.equal(afterSignIn('/stock/JKH.N0000?tab=cn'), '/stock/JKH.N0000?tab=cn')
  assert.equal(afterSignIn('/login'), '/')
  assert.equal(afterSignIn('//evil.example'), '/')
  assert.equal(afterSignIn('https://evil.example'), '/')
  assert.equal(afterSignIn(null), '/')
})
