# PROFILEs de planificación personal

Cuatro perfiles atómicos reutilizables para clientes que necesiten que la IA
**entienda**, **descomponga**, **analice** o **aconseje** sobre trabajo. Se
escribieron para Gestión de Tareas IA, pero no saben nada de ella: hablan de
trabajo, no de tareas de nadie en concreto.

| PROFILE | Función | Para qué |
|---|---|---|
| `task-intake` | `understand` | Convertir una petición vaga en un encargo entendido |
| `task-decomposer` | `decompose` | Partir un encargo grande en pasos ejecutables |
| `review-analyzer` | `analyze` | Leer lo que pasó y decir qué se repite |
| `planning-advisor` | `advise` | Aconsejar sobre orden y riesgo, sin poner horas |

## Qué es de Agora y qué es del cliente

Agora ejecuta el trabajo de IA. **El cliente sigue siendo el dueño** de sus
tareas, su calendario, su disponibilidad y su planificador. Ninguno de estos
perfiles decide cuándo se hace algo: `planning-advisor` opina sobre orden y
riesgo, y quien traduce eso a horas concretas es el scheduler del cliente.

Por eso los cuatro `refuses` incluyen explícitamente lo temporal: si una
petición pide agendar, el perfil la rechaza en vez de inventarse un calendario.

## Instalación

Copia los directorios que necesites dentro del `AGENTS` de tu tablero:

```powershell
Copy-Item -Recurse profiles\task-planning\* C:\Agora\workspace\AGENTS\
```

## Skills

Los perfiles **no declaran skills todavía**. Un PROFILE que declara una skill
inexistente no se puede ejecutar (`load_profile_skills` falla al no encontrar
`SKILL.md`), así que cada skill se declara en la fase que la crea y ahí se sube
la versión del perfil. Hasta entonces el perfil funciona con su propio contrato,
que ya es una instrucción completa.
