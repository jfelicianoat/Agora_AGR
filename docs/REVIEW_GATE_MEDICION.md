# Puerta System-1: falsos aceptados y rechazados (AG-01)

Sembrados `review-gate-sembrados.v1` · configuración: enabled, sin sombra, threshold 0.97, uncalibrated_policy=allow.
Mide la política de la puerta con jueces simulados; no la calidad del juez real.

| Juez | Autoaprobados | Falsos aceptados | De ellos deterministas | Falsos rechazados |
|---|---|---|---|---|
| perfecto | 3/14 | 0 | 0 | 0 |
| complaciente | 7/14 | 4 | 0 | 0 |
| dudoso | 0/14 | 0 | 0 | 3 |
| estricto | 0/14 | 0 | 0 | 3 |
| caido | 0/14 | 0 | 0 | 3 |

Incumplidos: ninguno.
