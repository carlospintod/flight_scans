import { Card } from "@/components/Section";

export const revalidate = false;

export const metadata = {
  title: "Privacy — flight_scans",
  robots: { index: true, follow: false },
};

export default function PrivacyPage() {
  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="font-mono text-lg text-text-bright">PRIVACY</h1>
      <Card className="space-y-4 text-sm leading-relaxed">
        <p>
          This is an invite-only hobby tool, not a commercial service.
          Here is everything it stores and why:
        </p>
        <ul className="list-inside list-disc space-y-2">
          <li>
            <span className="text-text-bright">Your name and email</span> —
            so the owner knows who an account belongs to. Login works by
            personal links; email is never used for marketing and there is
            no newsletter.
          </li>
          <li>
            <span className="text-text-bright">Your searches</span> — the
            routes, date windows, and stay lengths you track, plus the
            flight prices collected for them.
          </li>
          <li>
            <span className="text-text-bright">Operational records</span> —
            when scans ran and how much API budget they used.
          </li>
        </ul>
        <p>
          Data lives in a Turso (libSQL) database in the EU (AWS
          eu-west-1). No analytics trackers, no ads, no data sharing with
          anyone. Prices come from public flight-data sources; your search
          parameters are never shown to other users unless you make a
          search public yourself.
        </p>
        <p>
          <span className="text-text-bright">Deleting your data:</span> the
          Account page has a delete button that permanently removes your
          account, searches, and every collected price row — immediately,
          no backups kept beyond the database provider&apos;s 1-day
          point-in-time window.
        </p>
        <p className="font-mono text-[11px] text-hint">
          Contact: the person who invited you.
        </p>
      </Card>
    </div>
  );
}
