// Member sessions (M2, D5) — parallel to the tracker's user auth
// (lib/auth.ts) but a separate cookie and a separate table: members are
// customers, users are tracker operators. Web Crypto only, so the edge
// proxy can verify without a DB read.
//
// Cookie value: "m1.{memberId}.{exp}.{hmac(SESSION_SECRET, id.exp)}"

const enc = new TextEncoder();

export const MEMBER_COOKIE = "vz_member";
const SESSION_DAYS = 30;

function requireSecret(): string {
  const s = process.env.SESSION_SECRET ?? "";
  if (s.length < 16) throw new Error("SESSION_SECRET missing/short");
  return s;
}

async function hmac(secret: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function safeEqual(a: string, b: string): Promise<boolean> {
  const da = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(a)));
  const db_ = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(b)));
  let diff = 0;
  for (let i = 0; i < da.length; i++) diff |= da[i] ^ db_[i];
  return diff === 0;
}

export async function makeMemberSessionValue(memberId: number): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400;
  const mac = await hmac(requireSecret(), `${memberId}.${exp}`);
  return `m1.${memberId}.${exp}.${mac}`;
}

export async function verifyMemberSessionValue(
  value: string,
): Promise<{ memberId: number } | null> {
  const parts = value.split(".");
  if (parts.length !== 4 || parts[0] !== "m1") return null;
  const [, idStr, expStr, mac] = parts;
  const memberId = Number(idStr);
  const exp = Number(expStr);
  if (!Number.isInteger(memberId) || !Number.isInteger(exp)) return null;
  if (exp * 1000 < Date.now()) return null;
  const expect = await hmac(requireSecret(), `${memberId}.${exp}`);
  if (!(await safeEqual(mac, expect))) return null;
  return { memberId };
}

export function memberCookieOptions() {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: SESSION_DAYS * 86400,
  };
}

export async function sha256Hex(value: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(value));
  return Array.from(new Uint8Array(d))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export function newMemberToken(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
