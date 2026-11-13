/** Section title, optionally with a one-sentence `hint` underneath that
 *  says what the section answers — the plain-language line a reader
 *  needs before the chart or table makes sense. */
export function SectionHeading({
  children,
  hint,
  id,
  as: Tag = "h2",
}: {
  children: React.ReactNode;
  hint?: React.ReactNode;
  id?: string;
  /** h3 when the section sits inside a larger titled group (/ops). */
  as?: "h2" | "h3";
}) {
  return (
    <div className="mb-3">
      <Tag
        id={id}
        className="scroll-mt-16 border-b border-border pb-1.5 font-mono text-[11px] font-semibold uppercase tracking-[2px] text-signature"
      >
        {children}
      </Tag>
      {hint && (
        <p className="mt-2 font-sans text-[13px] leading-snug text-text-mid">
          {hint}
        </p>
      )}
    </div>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-card border border-border bg-bg2 p-4 ${className}`}
    >
      {children}
    </div>
  );
}
