# Benchmark System-1 — simulación local

Respuestas HTTP y tokens simulados. Tiempos medidos del cliente local, sin inferencia.
La autoaprobación de este ensayo permite explícitamente scores sin calibrar.
En código incompleto, la decisión es false con confianza 0.99: no es una aprobación.
— significa que no se dispone de una medida; no equivale a cero.

| Escenario | Validación | Score | Reviewer | Resultado | Cliente ms antes → gate | Tokens mock antes → gate |
|---|---|---|---|---|---|---|
| Informe correcto | passed | 0.99 | No | Aprobación gate | 5.75 → 4.80 | 120 → 26 |
| Código incompleto | passed | 0.99 | Sí | Entrega reviewer mock | 5.31 → 5.81 | 120 → 146 |
| Tests fallidos | failed | — | Sí | Entrega reviewer mock | 5.18 → 5.08 | 120 → 120 |
| Confianza 0.96 | passed | 0.96 | Sí | Entrega reviewer mock | 5.35 → 5.68 | 120 → 146 |
| Broker de juicio caído | passed | — | Sí | Entrega reviewer mock | 5.28 → 5.98 | 120 → — |
| LAYA falla, Ollama acepta | passed | 0.99 | No | Aprobación gate | 5.09 → 4.19 | 120 → 31 |
| Feature off | passed | — | Sí | Entrega reviewer mock | 5.21 → 5.35 | 120 → 120 |
| Shadow mode | passed | 0.99 | Sí | Entrega reviewer mock | 5.39 → 5.82 | 120 → 146 |
| Respuesta inválida | passed | — | Sí | Entrega reviewer mock | 5.30 → 5.80 | 120 → — |
| Revisión obligatoria | passed | — | Sí | Entrega reviewer mock | 6.64 → 6.60 | 120 → 120 |
| Score sin calibrar, política conservadora | passed | 0.99 | Sí | Entrega reviewer mock | 6.34 → 7.53 | 120 → 146 |
