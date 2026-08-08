// BORRADOR v1 — pendiente de revisión por el gestor antes del alta de
// autónomo / primer cobro (D5). Los huecos [·] los rellena Carlos.

import type { Metadata } from "next";

export const metadata: Metadata = { title: "Aviso legal — Vuelazo" };
export const revalidate = false;

export default function AvisoLegalPage() {
  return (
    <article className="max-w-2xl space-y-4 text-[14px] leading-relaxed text-vz-ink">
      <h1 className="font-vz-display text-3xl font-semibold">Aviso legal</h1>
      <p className="rounded-md bg-vz-paper2 p-3 text-[12px] text-vz-ink-soft">
        Borrador pendiente de revisión profesional (gestoría) antes de la
        activación de pagos.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Titular del sitio</h2>
      <p>
        vuelazo.es es un servicio operado por Carlos Pinto Díaz
        [NIF · pendiente], con domicilio a efectos de notificaciones en
        [dirección · pendiente], Castellón, España. Contacto:
        hola@vuelazo.es.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Actividad</h2>
      <p>
        Vuelazo es un servicio de información y alertas sobre tarifas
        aéreas. Vuelazo no es agencia de viajes, no vende billetes, no
        intermedia en la contratación y no aplica enlaces de afiliación:
        los enlaces llevan directamente a Google Flights o a la
        aerolínea. Los precios mostrados se verifican en el momento
        indicado en cada aviso y pueden cambiar o agotarse sin previo
        aviso; la disponibilidad final es responsabilidad del proveedor
        con quien reserves.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Propiedad intelectual</h2>
      <p>
        Los contenidos propios del sitio (textos, gráficas, marca
        Vuelazo) pertenecen a su titular. Los datos de tarifas proceden
        de fuentes de terceros y pertenecen a sus respectivos titulares.
      </p>
      <h2 className="font-vz-display text-xl font-semibold">Legislación</h2>
      <p>
        Este sitio se rige por la legislación española. Para cualquier
        controversia serán competentes los juzgados del domicilio del
        consumidor cuando la ley así lo disponga.
      </p>
    </article>
  );
}
