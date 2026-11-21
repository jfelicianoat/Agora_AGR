# Arbitraje de la auditoría de Agora — 19-nov-2026

**Modalidad:** agente revisor independiente (contexto propio, sin permiso de escritura en el
repo ni en `C:\Agora`). Recibió instrucciones, diff, scripts y evidencias, y verificó
ejecutando. Sus scripts están en el scratchpad de la sesión (`arbitro/`).

## Ronda 1 — no aprueba

- A-1 (alta): la instancia programada se duplicaba al salir de `pending`.
- A-2 (media-alta): D-03 introducía un bucle de escrituras con yields gratis.
- A-3 (media): «Abrir resultado» podía ejecutar un `.exe`.
- A-4 (baja-media, preexistente): una tarjeta podía quedar en dos carpetas por mutaciones
  desde dos procesos.
- A-5…A-10 (bajas): el presupuesto se duplicaba, carrera al materializar, errores sin log,
  campos heredados, texto del README y evidencia insuficiente.

Confirmó que la compatibilidad de claves y los demás llamantes no tenían problemas.

## Ronda 2 — no aprueba

- B-1 (media): el API modificaba tarjetas fuera del candado.
- B-2 (baja-media): el candado de fichero se borraba y podía quedar huérfano.
- B-3: «ocupado» salía como 409.
- B-4: el tope contaba en total, no seguidas.
- B-5: evidencia en cp1252; GTI no conoce el código nuevo.

## Ronda 3 — aprueba el código

No quedan objeciones sobre el código. Se verificó entre procesos, entre hilos del API, con
reentrancia, con un fichero de 0 bytes, con un tercero que tiene el fichero abierto y con el
dueño del candado muerto. Tampoco hay posibilidad de interbloqueo entre `operation_lock` y el
candado del tablero.

C-1…C-4 eran correcciones del registro y ya están aplicadas. El estado final que exige la
norma es **IMPLEMENTADO CON VERIFICACIONES PENDIENTES** (IA real y despliegue).

**Lo que el árbitro no comprobó:** la IA real (PC IA apagado), el candado en Linux (rama
`fcntl`) ni la ventana con clics físicos.
