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
