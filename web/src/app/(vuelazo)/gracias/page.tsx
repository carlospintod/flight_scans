import type { Metadata } from "next";

export const metadata: Metadata = { title: "Gracias — Vuelazo" };
export const revalidate = false;

export default function GraciasPage() {
  return (
    <div className="mx-auto max-w-2xl pt-6">
      <h1 className="font-vz-display text-3xl font-semibold">
        ✈️ Bienvenido a bordo
      </h1>
      <p className="mt-4 text-[16px] leading-relaxed text-vz-ink">
        Tu pase anual está activo. En unos segundos te llega un email con
        dos enlaces:
      </p>
      <ol className="mt-5 space-y-4 text-[14px] text-vz-ink-soft">
        <li className="rounded-lg border border-vz-line bg-white/60 p-4">
          <span className="font-semibold text-vz-ink">1 · Tu cuenta</span> —
          elige desde qué aeropuertos quieres alertas (MAD, BCN, VLC, ALC).
        </li>
        <li className="rounded-lg border border-vz-line bg-white/60 p-4">
          <span className="font-semibold text-vz-ink">2 · Telegram</span> —
          un toque y el bot te mete en el canal privado de miembros: ahí
          llegan los vuelazos al instante.
        </li>
      </ol>
      <p className="mt-6 text-[13px] leading-relaxed text-vz-ink-soft">
        ¿No ves el email? Mira en spam/promociones. Si no aparece en 10
        minutos, escríbenos: hola@vuelazo.es.
      </p>
      <p className="mt-8 border-t border-vz-line pt-4 text-[12px] text-vz-ink-soft">
        Recuerda: sin renovación automática. Un pago, 12 meses, y el año
        que viene decides tú. Garantía de devolución de 14 días.
      </p>
    </div>
  );
}
