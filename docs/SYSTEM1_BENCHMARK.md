# Benchmark System-1 — simulación local

Respuestas HTTP y tokens simulados. Tiempos medidos del cliente local, sin inferencia.
La autoaprobación de este ensayo permite explícitamente scores sin calibrar.
En código incompleto, la decisión es false con confianza 0.99: no es una aprobación.
— significa que no se dispone de una medida; no equivale a cero.

| Escenario | Validación | Score | Reviewer | Resultado | Cliente ms antes → gate | Tokens mock antes → gate |
|---|---|---|---|---|---|---|
| Informe correcto | passed | 0.99 | No | Aprobación gate | 5.24 → 4.28 | 120 → 26 |
| Código incompleto | passed | 0.99 | Sí | Entrega reviewer mock | 5.26 → 5.76 | 120 → 146 |
| Tests fallidos | failed | — | Sí | Entrega reviewer mock | 5.37 → 5.37 | 120 → 120 |
| Confianza 0.96 | passed | 0.96 | Sí | Entrega reviewer mock | 5.38 → 5.97 | 120 → 146 |
| Broker de juicio caído | passed | — | Sí | Entrega reviewer mock | 5.57 → 5.99 | 120 → — |
| Ollama falla, LAYA acepta | passed | 0.99 | No | Aprobación gate | 5.47 → 4.93 | 120 → 31 |
| Feature off | passed | — | Sí | Entrega reviewer mock | 5.82 → 5.69 | 120 → 120 |
| Shadow mode | passed | 0.99 | Sí | Entrega reviewer mock | 5.31 → 5.92 | 120 → 146 |
| Respuesta inválida | passed | — | Sí | Entrega reviewer mock | 5.37 → 5.97 | 120 → — |
| Revisión obligatoria | passed | — | Sí | Entrega reviewer mock | 5.50 → 5.46 | 120 → 120 |
| Score sin calibrar, política conservadora | passed | 0.99 | Sí | Entrega reviewer mock | 5.35 → 6.15 | 120 → 146 |
