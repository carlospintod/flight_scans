// Vuelazo origin hubs + destination display names (M4a/M4b).

export const HUBS: Record<
  string,
  { iata: string; city: string; desde: string }
> = {
  valencia: { iata: "VLC", city: "València", desde: "desde València" },
  alicante: { iata: "ALC", city: "Alacant", desde: "desde Alacant" },
  madrid: { iata: "MAD", city: "Madrid", desde: "desde Madrid" },
  barcelona: { iata: "BCN", city: "Barcelona", desde: "desde Barcelona" },
};

export function hubByIata(iata: string) {
  return Object.entries(HUBS).find(([, h]) => h.iata === iata.toUpperCase());
}

// Display names for common destinations (fallback: the IATA code).
export const DEST_NAMES: Record<string, string> = {
  LON: "Londres", PAR: "París", ROM: "Roma", MIL: "Milán", AMS: "Ámsterdam",
  BER: "Berlín", BRU: "Bruselas", VIE: "Viena", PRG: "Praga", BUD: "Budapest",
  ATH: "Atenas", LIS: "Lisboa", OPO: "Oporto", DUB: "Dublín", EDI: "Edimburgo",
  CPH: "Copenhague", ARN: "Estocolmo", ZRH: "Zúrich", IST: "Estambul",
  RAK: "Marrakech", CMN: "Casablanca", TUN: "Túnez", CAI: "El Cairo",
  TLV: "Tel Aviv", DXB: "Dubái", NYC: "Nueva York", MIA: "Miami",
  CUN: "Cancún", MEX: "Ciudad de México", HAV: "La Habana",
  PUJ: "Punta Cana", BOG: "Bogotá", EZE: "Buenos Aires", SCL: "Santiago",
  LIM: "Lima", BKK: "Bangkok", TYO: "Tokio", DEL: "Delhi",
  MRS: "Marsella", NTE: "Nantes", SVQ: "Sevilla", PMI: "Palma",
  IBZ: "Ibiza", AGP: "Málaga", BIO: "Bilbao", OVD: "Asturias",
  NAP: "Nápoles", VCE: "Venecia", PSA: "Pisa", FCO: "Roma (FCO)",
};

export function destName(iata: string): string {
  return DEST_NAMES[iata.toUpperCase()] ?? iata.toUpperCase();
}
