import { describe, expect, it } from 'vitest';
import {
  DEMO_ACCOUNT,
  MOCK_SESSION_COOKIE,
  sessionCookie,
  signIn,
  signUp,
  userFromSession,
} from './auth';

describe('mock accounts', () => {
  it('signs in with the demo account, and never returns the password', () => {
    const { user, rejection } = signIn(DEMO_ACCOUNT);
    expect(rejection).toBeUndefined();
    expect(user).toEqual({ id: 'usr_demo', name: 'Demo User', email: 'demo@dataskate.ai' });
  });

  it('gives a wrong password and an unknown email the same answer', () => {
    const wrong = signIn({ email: DEMO_ACCOUNT.email, password: 'not-it' });
    const unknown = signIn({ email: 'nobody@example.com', password: DEMO_ACCOUNT.password });
    expect(wrong.rejection).toEqual(unknown.rejection);
    expect(wrong.rejection.status).toBe(401);
  });

  it('signs up, normalises the email, and then signs in', () => {
    const { user } = signUp({ name: ' Ada ', email: ' Ada@Example.com ', password: 'longenough' });
    expect(user).toMatchObject({ name: 'Ada', email: 'ada@example.com' });
    expect(user).not.toHaveProperty('password');
    expect(signIn({ email: 'ADA@example.com', password: 'longenough' }).user).toEqual(user);
  });

  it.each([
    [{ name: '', email: 'x@example.com', password: 'longenough' }, 400],
    [{ name: 'X', email: 'nope', password: 'longenough' }, 400],
    [{ name: 'X', email: 'x@example.com', password: 'short' }, 400],
    [{ name: 'X', email: DEMO_ACCOUNT.email, password: 'longenough' }, 409],
  ])('rejects %o with %i', (body, status) => {
    expect(signUp(body).rejection.status).toBe(status);
  });

  it('reads the user back out of its session cookie', () => {
    const { user } = signIn(DEMO_ACCOUNT);
    const value = sessionCookie(user).split(';')[0].slice(MOCK_SESSION_COOKIE.length + 1);
    expect(userFromSession(value)).toEqual(user);
    expect(userFromSession(decodeURIComponent(value))).toEqual(user);
    expect(userFromSession('garbage')).toBeNull();
    expect(userFromSession(undefined)).toBeNull();
  });
});
