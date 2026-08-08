// BORRADOR v1 — pendiente de revisión por el gestor/asesor RGPD.

import type { Metadata } from "next";

export const metadata: Metadata = { title: "Privacidad — Vuelazo" };
export const revalidate = false;

export default function PrivacidadPage() {
  return (
    <article className="max-w-2xl space-y-4 text-[14px] leading-relaxed text-vz-ink">
      <h1 className="font-vz-display text-3xl font-semibold">Privacidad</h1>
      <p className="rounded-md bg-vz-paper2 p-3 text-[12px] text-vz-ink-soft">
        Borrador pendiente de revisión profesional antes del lanzamiento.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Qué datos tratamos</h2>
      <p>
        <strong>Lista gratuita:</strong> tu email, para enviarte el resumen
        semanal. <strong>Miembros:</strong> email, preferencias de
        aeropuertos, identificador de Telegram (si vinculas tu cuenta) y
        los datos de la compra que gestiona Stripe (nunca guardamos tu
        tarjeta). Base jurídica: ejecución del contrato de membresía y
        consentimiento para la lista gratuita.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Encargados</h2>
      <p>
        Usamos Stripe (pagos), Resend (envío de email), Turso (base de
        datos), Vercel (alojamiento) y Telegram (mensajería). Con cada
        uno rige su contrato de encargo de tratamiento.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Conservación y bajas</h2>
      <p>
        Todos los emails masivos llevan baja en un clic
        (List-Unsubscribe); la baja se respeta antes de cada envío.
        Puedes pedir acceso, rectificación o supresión escribiendo a
        hola@vuelazo.es. Conservamos los datos de facturación el tiempo
        que exige la normativa fiscal.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Cookies</h2>
      <p>
        Solo cookies técnicas de sesión (imprescindibles para tu cuenta).
        Sin cookies de publicidad ni analítica de terceros.
      </p>
    </article>
  );
}
