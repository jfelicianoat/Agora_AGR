# RESULTADO DE FASE — F0 núcleo local determinista

Fecha: 2026-09-01  
Rama: `codex/agora-f0`  
Estado: listo para aceptación humana; F1 no iniciada.

## Implementado

- Contrato CARD Markdown con front matter YAML seguro, preservación de campos desconocidos,
  `Record` legible y escritura atómica.
- BOARD con los seis estados requeridos: `pending`, `in-progress`, `done`, `blocked`,
  `archive` y `scheduled`.
- Claim atómico con un único ganador incluso con dos workers concurrentes.
- Censo directo de PROFILE y SKILL en cada ronda, sin caché oculta.
- Matching ciego y determinista por función, `refuses`, descriptor más específico y
  `recipient`; las ambigüedades esperan.
- Gates de origen, bloqueo, intentos e inputs; un input ausente no consume intento.
- Dispatcher sin modelo y harness falso determinista, con cierre únicamente cuando el
  artefacto existe y queda registrado mediante ruta absoluta.
- Recuperación de zombis y scheduler idempotente sin *catch-up*.
- CLI mínima para inicializar BOARD, comprobar solapes y ejecutar o simular una ronda.
- Ejemplo reproducible en `examples/f0-demo`.

## Pruebas automáticas

Comando final:

```powershell
python -m pytest -q --basetemp=tmp\pytest-process-full
```

Resultado final: **21 passed in 0.61s**.

La suite cubre las doce pruebas obligatorias de F0 y nueve verificaciones adicionales:

1. round-trip CARD preservando metadatos, cuerpo y campos desconocidos;
2. dos workers y exactamente un claim ganador;
3. CARD sin PROFILE compatible permanece pendiente;
4. `refuses` elimina candidatos;
5. gana el descriptor más específico;
6. `recipient` válido selecciona directamente y el inválido no lo hace;
7. input ausente espera sin incrementar `attempts`;
8. CARD bloqueada no se despacha;
9. zombi vuelve a `pending` o termina en `blocked` al agotar intentos;
10. `--dry-run` conserva bytes y fecha de modificación;
11. el comprobador de solapes detecta ambigüedad;
12. flujo completo con harness falso, artefacto, rutas absolutas y Record legible;
13. creación de los seis estados del BOARD;
14. YAML corrupto produce error visible;
15. un contrato con `paths` inválido no puede cerrarse;
16. un `--dry-run` tampoco bloquea ni mueve un origen no confiable;
17. una CARD urgente precede a una normal;
18. una CARD sin match permanece pendiente;
19. materialización diaria idempotente;
20. una planificación semanal perdida no ejecuta *catch-up*.
21. dos procesos independientes compiten por una CARD y existe exactamente un ganador.

Comprobaciones adicionales:

- `ruff check .`: **All checks passed**.
- `python -m compileall -q src tests examples`: correcto.
- `mypy` no pudo ejecutarse con la instalación heredada de AI_Broker porque su entorno
  apunta a un Python inexistente de otro equipo; el Python local no tiene `mypy` instalado.
  No se ocultó ni se confundió este fallo de herramienta con un diagnóstico del código.

## Prueba manual de usuario

Se recorrió desde una carpeta vacía el siguiente viaje:

1. inicializar los seis estados;
2. cargar un PROFILE y su SKILL;
3. comprobar que no existen solapes;
4. copiar una CARD humana a `pending`;
5. ejecutar `--dry-run` y comparar el hash antes y después;
6. despachar la CARD;
7. comprobar su presencia en `done` y la existencia del artefacto;
8. leer el artefacto con el PROFILE, función, solicitud y SKILL efectivos.

Resultado observado:

```text
No profile handle overlaps detected.
manual.md: would_dispatch -> summarizer (longest matching handle won)
dry-run-unchanged=True
manual.md: dispatched -> summarizer (longest matching handle won)
done-card=True
artifact=True
```

La primera ejecución manual descubrió que una CARD de origen no confiable podía moverse a
`blocked` durante `--dry-run`. Se corrigió antes de cerrar F0 y se añadió la prueba de
regresión número 16.

## Validación arquitectónica previa para F1–F3

- Athena conserva su `AgentLoop` y su `ModelProvider`; Agora será un adaptador opcional y
  no un segundo router de modelos.
- AI_Broker conserva en exclusiva el enrutado de proveedor/modelo.
- El BOARD será propiedad directa de Agora en el PC principal; los runners remotos usarán
  pull por API y nunca una carpeta compartida.
- F3 debe desactivar la compresión de prompt para trabajos atómicos y usar un umbral de
  zombi superior a los 3000 segundos actuales del broker más margen.
- El token suministrado no se escribió, imprimió ni incorporó al repositorio. El mecanismo
  actual del broker genera y muestra un token si no recibe uno; F3 necesita un supervisor
  local que lo genere en memoria, lo inyecte a broker y runner y redacte la salida, o una
  decisión explícita para usar Windows Credential Manager.
- La conectividad viva con el broker no se pudo comprobar desde este entorno porque las
  conexiones a `localhost` y a la LAN están bloqueadas. F0 no depende de red.

El detalle y las contradicciones resueltas están en `docs/architecture-f0-f3.md`.

## Riesgos residuales y no implementado

- No hay GUI Qt/PySide6, API HTTP, runner remoto, AI_Broker, Athena, OAuth, A2A ni WOL: son
  F1–F8 y se mantienen fuera de F0 por diseño.
- La semántica de claim está probada entre hilos y procesos independientes mediante
  exclusión mutua del sistema de archivos. F2 deberá volver a probarla mediante
  compare-and-swap HTTP.
- Falta resolver y probar en el PC IA el ciclo de vida real del token y la supervisión del
  broker antes de aceptar F3.
- El entorno de desarrollo necesita una instalación local sana de `mypy` para reactivar el
  control de tipos estricto configurado en `pyproject.toml`.

## Cómo validar manualmente

Desde la raíz de Agora, con `PYTHONPATH` apuntando a `src`:

```powershell
python -m agora init .\tmp\acceptance\KANBAN
Copy-Item .\examples\f0-demo\AGENTS .\tmp\acceptance -Recurse
Copy-Item .\examples\f0-demo\CARD.md .\tmp\acceptance\KANBAN\pending\manual.md
python -m agora check-overlap .\tmp\acceptance\AGENTS
python -m agora dispatch .\tmp\acceptance\KANBAN .\tmp\acceptance\AGENTS --dry-run
python -m agora dispatch .\tmp\acceptance\KANBAN .\tmp\acceptance\AGENTS
Get-Content .\tmp\acceptance\artifacts\manual-summary.txt
Get-Content .\tmp\acceptance\KANBAN\done\manual.md
```

## Puerta humana

Se requiere aprobación explícita de F0 antes de iniciar F1. No se ha avanzado de fase.
