import { cookies } from "next/headers";

/**
 * Single-operator auth: an httpOnly cookie `ft_session` holding
 * "<exp>.<hmac>" where hmac = HMAC-SHA256(SESSION_SECRET, exp).
 * Web Crypto only, so the same code verifies in middleware (edge) and
 * route handlers (node). No user table — APP_PASSWORD is the identity.
 */

export const SESSION_COOKIE = "ft_session";
const SESSION_DAYS = 30;

function enc(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

function toHex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function hmac(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", enc(secret) as BufferSource, { name: "HMAC", hash: "SHA-256" },
    false, ["sign"],
  );
  return toHex(await crypto.subtle.sign("HMAC", key, enc(message) as BufferSource));
}

/** Constant-time-ish comparison via digest equality: hash both sides,
 *  compare hashes — length and content leaks don't survive SHA-256. */
async function safeEqual(a: string, b: string): Promise<boolean> {
  const [da, db_] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc(a) as BufferSource),
    crypto.subtle.digest("SHA-256", enc(b) as BufferSource),
  ]);
  const ua = new Uint8Array(da);
  const ub = new Uint8Array(db_);
  let diff = 0;
  for (let i = 0; i < ua.length; i++) diff |= ua[i] ^ ub[i];
  return diff === 0;
}

export async function checkPassword(candidate: string): Promise<boolean> {
  const expected = process.env.APP_PASSWORD ?? "";
  if (!expected) return false; // no password configured -> ops locked
  return safeEqual(candidate, expected);
}

export async function makeSessionValue(): Promise<string> {
  const secret = requireSecret();
  const exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86_400;
  return `${exp}.${await hmac(secret, String(exp))}`;
}

export async function verifySessionValue(
  value: string | undefined,
): Promise<boolean> {
  if (!value) return false;
  const dot = value.indexOf(".");
  if (dot <= 0) return false;
  const exp = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  if (!/^\d+$/.test(exp) || Number(exp) * 1000 < Date.now()) return false;
  const expected = await hmac(requireSecret(), exp);
  return safeEqual(sig, expected);
}

/** For route handlers: true when the request carries a valid session. */
export async function isAuthed(): Promise<boolean> {
  const store = await cookies();
  return verifySessionValue(store.get(SESSION_COOKIE)?.value);
}

// ---------------------------------------------------------------------------
// v2 user sessions (link-based multi-user auth, product plan M3).
// Cookie value: "v2.{userId}.{exp}.{sv}.{hmac(userId.exp.sv)}".
// The proxy verifies statelessly (crypto only, zero DB reads per
// navigation); mutating routes additionally compare sv against
// users.session_version (bump = revoke everywhere). The legacy
// "{exp}.{hmac}" APP_PASSWORD session stays as the /ops break-glass.
// ---------------------------------------------------------------------------

export interface UserSession {
  userId: number;
  sv: number;
}

export async function makeUserSessionValue(
  userId: number, sessionVersion: number,
): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86_400;
  const payload = `${userId}.${exp}.${sessionVersion}`;
  return `v2.${payload}.${await hmac(requireSecret(), payload)}`;
}

export async function verifyUserSessionValue(
  value: string | undefined,
): Promise<UserSession | null> {
  if (!value || !value.startsWith("v2.")) return null;
  const parts = value.split(".");
  if (parts.length !== 5) return null;
  const [, userId, exp, sv, sig] = parts;
  if (!/^\d+$/.test(userId) || !/^\d+$/.test(exp) || !/^\d+$/.test(sv)) {
    return null;
  }
  if (Number(exp) * 1000 < Date.now()) return null;
  const expected = await hmac(requireSecret(), `${userId}.${exp}.${sv}`);
  if (!(await safeEqual(sig, expected))) return null;
  return { userId: Number(userId), sv: Number(sv) };
}

/** Server-component/route helper: the current v2 user session, or null.
 *  Crypto-only — callers that MUTATE must also check session_version
 *  and role against the DB (see requireUser in users.ts). */
export async function getUserSession(): Promise<UserSession | null> {
  const store = await cookies();
  return verifyUserSessionValue(store.get(SESSION_COOKIE)?.value);
}

/** Break-glass: the legacy APP_PASSWORD session also authorizes /ops. */
export async function isOpsBreakGlass(): Promise<boolean> {
  const store = await cookies();
  const v = store.get(SESSION_COOKIE)?.value;
  if (!v || v.startsWith("v2.")) return false;
  return verifySessionValue(v);
}

const TOKEN_BYTES = 24;

export function newLinkToken(): string {
  const buf = new Uint8Array(TOKEN_BYTES);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function hashToken(token: string): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", enc(token) as BufferSource));
}

export function sessionCookieOptions() {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: SESSION_DAYS * 86_400,
  };
}

function requireSecret(): string {
  const s = process.env.SESSION_SECRET ?? "";
  if (s.length < 16) {
    throw new Error(
      "SESSION_SECRET missing/too short — set a long random value in the env.",
    );
  }
  return s;
}
