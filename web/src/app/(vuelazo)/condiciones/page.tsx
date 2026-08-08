// BORRADOR v1 — pendiente de revisión por el gestor (D5: la garantía de
// 14 días ES el derecho de desistimiento, honrado en vez de excluido).

import type { Metadata } from "next";

export const metadata: Metadata = { title: "Condiciones — Vuelazo" };
export const revalidate = false;

export default function CondicionesPage() {
  return (
    <article className="max-w-2xl space-y-4 text-[14px] leading-relaxed text-vz-ink">
      <h1 className="font-vz-display text-3xl font-semibold">
        Condiciones de la membresía
      </h1>
      <p className="rounded-md bg-vz-paper2 p-3 text-[12px] text-vz-ink-soft">
        Borrador pendiente de revisión profesional antes de activar pagos.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">El servicio</h2>
      <p>
        La membresía de Vuelazo es un pase anual (12 meses) de pago único
        que da acceso a todas las alertas de chollos verificados, al
        instante, con filtro por aeropuertos. Precio: 39 € IVA incluido
        (29 € para la cohorte fundadora, que se mantiene renovando a
        tiempo). <strong>Sin renovación automática:</strong> al final del
        año decides tú si renuevas.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">
        Garantía de 14 días
      </h2>
      <p>
        Si en los primeros 14 días no te convence, te devolvemos el
        importe íntegro: escribe a hola@vuelazo.es. Esta garantía
        coincide con tu derecho legal de desistimiento (art. 102 y ss.
        TRLGDCU), que honramos sin condiciones.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">
        Lo que Vuelazo no es
      </h2>
      <p>
        No vendemos billetes ni intermediamos en tu reserva. Las tarifas
        avisadas se verifican en vivo en el momento del aviso, pero
        pueden cambiar o agotarse: la reserva y sus condiciones son cosa
        de la aerolínea o agencia donde compres. Las tarifas señaladas
        como posible «tarifa error» pueden no ser honradas por la
        aerolínea — lo avisamos siempre.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Pagos e impuestos</h2>
      <p>
        Los pagos se procesan mediante Stripe (tarjeta o Bizum). Los
        precios incluyen IVA; la factura se emite con los datos que
        facilites en el pago.
      </p>
    </article>
  );
}
