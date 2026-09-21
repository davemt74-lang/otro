# VP3 OS v0.10 — Hardware Platform Foundation

VP3 OS is the shared operating/runtime layer for every VP3 hardware product.

The existing OTRO/HomeServer runtime is the VP3 OS core. VP3 Node, VP3 Desk,
VP3 Studio, VP3 Team Node and VP3 Pocket do **not** get separate agent brains,
memory systems, cloud clients or product-specific server stacks. They run the
same VP3 OS and differ only through a declared hardware profile plus hardware
adapters.

## Architecture

```text
VP3 Cloud
   ↕  existing pairing / Remote Bridge / app scopes
VP3 OS (OTRO)
   ├─ Agent Brain
   ├─ Cognitive Runtime
   ├─ Memory / Knowledge
   ├─ Local Models / Provider Routing
   ├─ Local Voice
   ├─ Meetings
   ├─ Tools / Approvals / Workflows
   ├─ VP3 OS Hardware Platform v0.10
   │    ├─ device profile
   │    ├─ normalized hardware inventory
   │    ├─ privacy state
   │    └─ workload placement contract
   └─ Hardware Adapters v0.20+
        ├─ buttons / LEDs
        ├─ microphone / speaker
        ├─ physical privacy switch
        ├─ display / battery
        ├─ creator audio I/O
        └─ accelerator / storage telemetry
```

## Hardware profiles

v0.10 defines one OS with these profiles:

| Profile | Product | Purpose |
| --- | --- | --- |
| `custom` | User-supplied hardware | DIY mini PC / existing HomeServer install |
| `vp3_node` | VP3 Node | Personal AI appliance |
| `vp3_desk` | VP3 Desk | Desk AI companion |
| `vp3_studio` | VP3 Studio | Creator / production appliance |
| `vp3_team_node` | VP3 Team Node | Shared team appliance |
| `vp3_pocket` | VP3 Pocket | Portable companion |

The runtime selects a profile using `VP3_OS_HARDWARE_PROFILE`. Unknown values
fail back to `custom`.

A profile contains **expected** hardware only. Expected hardware is never
reported as present or ready merely because the product profile was selected.
That state must come from a hardware adapter.

## Hardware abstraction contract

`app/services/vp3_os.py` owns the normalized hardware inventory. Hardware
adapters report component state through `report_hardware_state(...)`.

v0.10 recognizes:

- Agent button
- status light
- microphone
- speaker
- physical privacy switch
- display
- creator audio I/O
- battery
- camera
- storage
- accelerator

This foundation deliberately performs no arbitrary host probing and reads no
MAC address, motherboard serial or hostname. The stable device ID exposed to a
paired VP3 application reuses the existing opaque HomeServer Remote Bridge
identity.

Detailed hardware state is owner-only. Public capability discovery exposes the
OS/profile but not the stable device ID. Authenticated paired apps receive a
sanitized platform projection.

## Privacy contract

v0.10 establishes these invariants:

1. Raw audio is never cloud-allowed by default.
2. Secret workloads resolve to `LOCAL_ONLY`.
3. Raw-audio workloads resolve to `LOCAL_ONLY` only when any explicitly
   required hardware is actually ready; otherwise they `DEFER`.
4. A physical microphone disconnect is never claimed unless a hardware adapter
   explicitly reports a physical disconnect.
5. Pairing/app scope remains authoritative. The VP3 OS placement endpoint can
   narrow cloud use for a request but cannot widen an app whose HomeServer
   scope has `cloud_allowed=false`.
6. The platform layer plans execution location only. It does not bypass the
   existing Agent Brain, Cognitive Runtime, provider routing, permissions,
   tools, approvals or Remote Bridge security.

## Placement modes

The shared placement vocabulary is:

- `LOCAL` — execute on VP3 OS.
- `LOCAL_ONLY` — execute on VP3 OS and do not send the protected workload to
  cloud compute.
- `CLOUD` — use an allowed cloud route.
- `HYBRID` — keep the local/device portion on VP3 OS while allowing a bounded
  cloud reasoning/collaboration portion.
- `DEFER` — required hardware, compute or policy conditions are unavailable.

Placement requests contain metadata only: sensitivity, raw-audio flag,
local-data requirement, cloud-model requirement, collaboration flag and named
hardware requirements. They do not carry private source content.

## API surfaces

Owner-only:

- `GET /api/v1/control/vp3-os`
- `POST /api/v1/control/vp3-os/placement`

Paired-app bearer authenticated:

- `GET /api/v1/vp3-os/status`
- `POST /api/v1/vp3-os/placement`

Remote Bridge:

- `vp3.os.status`
- `vp3.os.placement`

The existing `/api/v1/capabilities` surface advertises VP3 OS without a stable
device identifier. The authenticated capability registry includes the sanitized
device identity and the two remote operations.

## Authority boundaries

v0.10 intentionally does not move existing responsibilities:

- Agent Brain: existing VP3 OS/HomeServer runtime
- cognition: existing Cognitive Runtime
- memory/knowledge: existing local stores and permission model
- inference/provider routing: existing provider runtime
- voice: existing local voice runtime
- cloud connection: existing pairing + Remote Bridge
- hardware I/O: next phase, Hardware Adapters v0.20

This keeps VP3 OS one coherent product rather than layering a second hardware
server on top of HomeServer.

## Next phase

**VP3 OS v0.20 — Hardware Adapters** should implement the first real device
drivers: Agent button, status light, microphone/speaker, and a verifiable
physical microphone power disconnect. The first target should be the VP3 Node
development kit, but the adapter interfaces must remain profile-independent so
the same OS image can boot on every VP3 hardware product.
