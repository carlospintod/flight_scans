export function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-3 border-b border-line pb-1.5 font-mono text-[11px] font-semibold uppercase tracking-[2px] text-matrix">
      {children}
    </h2>
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
      className={`rounded-card border border-line bg-bg-2 p-4 ${className}`}
    >
      {children}
    </div>
  );
}
