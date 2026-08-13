# Cache recall — generated 2026-08-13 20:27Z

Can the free Aviasales cache NOMINATE real deals? Everything below comes from data already collected — no API calls were made.

## A. Fidelity — what the cache claimed vs what Google charged

- verifications with both numbers: **68**
- median gap (live vs cached): **+40%**
- p90 gap: **+204%**
- cached within 25% of live: **25/68** (37%)
- cached price actually usable (live <= cached +10%): **20/68** (29%)

| route | cached | live | gap |
|---|---|---|---|
| VLC->EVN | 137 € | 464 € | +239% |
| VLC->EVN | 137 € | 451 € | +229% |
| VLC->EVN | 137 € | 451 € | +229% |
| BCN->EVN | 91 € | 277 € | +204% |
| BCN->EVN | 91 € | 277 € | +204% |
| BCN->EVN | 91 € | 277 € | +204% |
| BCN->EVN | 91 € | 277 € | +204% |
| BCN->EVN | 91 € | 277 € | +204% |
| BCN->EVN | 91 € | 277 € | +204% |
| VLC->EVN | 137 € | 417 € | +204% |
| BCN->EVN | 91 € | 254 € | +179% |
| BCN->EVN | 91 € | 254 € | +179% |
| BCN->EVN | 91 € | 254 € | +179% |
| BCN->EVN | 91 € | 254 € | +179% |
| BCN->EVN | 91 € | 254 € | +179% |

**The nomination rate that matters is the last one.** Every other cached row costs a verification call to disprove.

## B. Coverage — routes Google's Explore found, priced by the cache

- routes Explore found: **280**
- also present in the cache: **140/280**
- cached within 25% of Google's price: **103/280**

| route | Google (explore) | cache | gap |
|---|---|---|---|
| ALC->ACE | 109 € | 130 € | +19% |
| ALC->ALG | 87 € | 72 € | -17% |
| ALC->BHQ | 2150 € | — | not in cache |
| ALC->BOG | 820 € | 969 € | +18% |
| ALC->BOS | 428 € | — | not in cache |
| ALC->CAI | 285 € | 435 € | +53% |
| ALC->CLO | 1079 € | — | not in cache |
| ALC->CMN | 108 € | 95 € | -12% |
| ALC->CUN | 730 € | 749 € | +3% |
| ALC->EZE | 1066 € | — | not in cache |
| ALC->FEZ | 30 € | 30 € | +0% |
| ALC->FNC | 303 € | 151 € | -50% |
| ALC->FUE | 179 € | 177 € | -1% |
| ALC->GRU | 824 € | — | not in cache |
| ALC->GYE | 811 € | — | not in cache |
| ALC->IAD | 493 € | — | not in cache |
| ALC->JFK | 537 € | — | not in cache |
| ALC->LDH | 2898 € | — | not in cache |
| ALC->LIM | 881 € | 853 € | -3% |
| ALC->LPA | 87 € | 103 € | +18% |

## C. Freshness — how old cached prices are when we read them

- rows with a provider timestamp: **336** (without: 175141)
- median age: **76h**
- p90 age: **152h**

Documented error-fare bookable windows for comparison: under 30 minutes (Miami-Fortaleza), ~2h (China Southern), ~3h (Iberia Rio-Paris), ~24h (Cathay Pacific). A cache whose median age exceeds those windows cannot catch that class of fare at any price — which is a fact about the SOURCE, not about our budget.
