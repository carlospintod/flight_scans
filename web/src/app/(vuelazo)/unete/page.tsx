import type { Metadata } from "next";
import CheckoutButtons from "@/components/member/CheckoutButtons";

export const metadata: Metadata = {
  title: "Únete a Vuelazo — vuelazos desde tu aeropuerto",
  description:
    "Alertas de chollos de vuelo desde València, Alacant, Madrid y " +
    "Barcelona. El precio normal, demostrado — y el chollo, a tiempo.",
};

export const revalidate = false;

export default function UnetePage() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="font-vz-display text-3xl font-semibold leading-tight sm:text-4xl">
        El precio normal, demostrado —{" "}
        <span className="text-vz-amber">y el chollo, a tiempo.</span>
      </h1>
      <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-vz-ink-soft">
        Vuelazo vigila los precios desde{" "}
        <strong className="text-vz-ink">
          València, Alacant, Madrid y Barcelona
        </strong>{" "}
        todos los días. Cuando un vuelo cae de verdad por debajo de su
        precio normal — verificado en vivo, no rumores de caché — te
        avisamos al instante en Telegram y por email.
      </p>

      <ul className="mt-6 space-y-2 text-[14px] text-vz-ink">
        <li>✓ todos los chollos (5–7/semana), al instante</li>
        <li>✓ solo tus aeropuertos — tú eliges cuáles</li>
        <li>✓ cada aviso con su gráfica: el precio normal, demostrado</li>
        <li>
          ✓ enlaces directos a Google Flights o la aerolínea — no ganamos
          nada con tus clics, solo con tu membresía
        </li>
      </ul>

      <div className="mt-8">
        <CheckoutButtons />
      </div>

      <p className="mt-8 border-t border-vz-line pt-4 text-[13px] leading-relaxed text-vz-ink-soft">
        La lista gratuita seguirá existiendo siempre: 1–3 chollos
        excelentes por semana, 24 horas después que los miembros. Pagar
        va de completitud y velocidad, nunca de calidad.
      </p>
    </div>
  );
}
