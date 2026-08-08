# Seed-list deliverability test (M3 DoD item — procedure)

Run BEFORE the first real bulk send (D4 obligation). Needs: RESEND_API_KEY,
vuelazo.es domain verified in Resend (SPF + DKIM + DMARC records live —
M4a DoD), and 6–10 seed inboxes.

## Seed list

Create/borrow inboxes across the providers Spanish members actually use:
- 2× Gmail (one fresh, one aged)
- 2× Outlook/Hotmail
- 1× Yahoo
- 1× iCloud
- 1–2× corporate (Google Workspace / M365)

## Procedure

1. Verify DNS first: `nslookup -type=TXT vuelazo.es`,
   `..._dmarc.vuelazo.es`, and the Resend DKIM selector — all green in
   the Resend dashboard.
2. Add the seed addresses to `subscribers` (source='seed').
3. Send the real Sunday digest: `python scripts/run_digest.py --send`
   (or dispatch digest.yml with send=true).
4. For each inbox record: folder (inbox / promotions / spam),
   authentication results from "show original" (SPF=pass, DKIM=pass,
   DMARC=pass), rendering (plain text OK), List-Unsubscribe visible
   in the client UI (Gmail: "Darse de baja" chip).
5. Send a member ALERT to the same list (temporarily add seeds to
   `members` with airports=["VLC"]) and repeat the checks — alerts and
   digests take different code paths.
6. Log results in this file; anything but inbox/promotions on Gmail is a
   failure.

## Pass bar & fallback

- ≥ 80% inbox or promotions, 0 hard spam placements, all three auth
  checks pass everywhere.
- If placement fails after DNS + warm-up: the named fallback is **Brevo**
  (EU processor, 300/day free) per D4 — swap `lib/resend_api.py` for a
  Brevo adapter (same surface: send_email + headers), keys via env.

## Warm-up plan (first 2 weeks post-launch)

Day 1–3: seeds only. Day 4–7: + first ~25 real subscribers. Week 2:
full list. Never blast a cold domain with the whole list (Gmail/Yahoo
bulk rules).

## Results (fill in when run)

| date | send | inbox | promo | spam | spf/dkim/dmarc | notes |
|---|---|---|---|---|---|---|
