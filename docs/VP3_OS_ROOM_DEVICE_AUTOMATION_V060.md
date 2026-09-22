# VP3 OS v0.60 — Room & Device Automation Runtime

VP3 OS v0.60 adds a shared local room/device model and a governed physical-action
pipeline on top of the v0.50 Ambient Agent.

The goal is **not** to create a second smart-home brain. Rooms, devices and
physical actions become another bounded capability of the same VP3 Agent,
Approvals, Action Policy and Cognitive Runtime architecture.

## Core architecture

```text
Local provider / discovery source
            ↓
Room & Device Registry
            ↓
Normalized state + capabilities
            ↓
Agent / paired app / Ambient suggestion
            ↓
devices.command proposal
            ↓
Action Request
            ↓
Owner Approval
            ↓
Reserved executing request
            ↓
Local provider driver
            ↓
Physical device
            ↓
State + audit update
```

The provider boundary is intentionally local and pluggable. A future Home
Assistant, Matter, HomeKit bridge, MQTT bridge or VP3-native controller may
register inventory and bind an in-process execution driver without changing
Agent/approval semantics.

v0.60 does **not** claim a third-party integration exists merely because the
provider type is registered.

## Persistent model

Migration 023 adds:

- `automation_rooms`
- `automation_providers`
- `automation_devices`
- `automation_device_actions`
- `automation_suggestions`

Rooms organize local device inventory.

Providers describe the local discovery/execution source. Provider metadata is
owner-local and is not exposed through the Agent device-read tool.

Devices contain:

- stable VP3 device key
- provider identity
- provider device ID
- optional room
- display name
- bounded category
- normalized capabilities
- normalized readable state
- private provider/device metadata
- controllable flag
- last-seen state

## Three separate authority states

A discovered device is not automatically controllable, and a controllable
device is not automatically executable.

```text
discovered
   ↓ owner/provider marks safe category controllable
controllable
   ↓ enabled + connected executable provider + live driver
currently executable
   ↓ owner approves one exact request
command released
```

The UI surfaces this distinction explicitly.

## Safe v0.60 categories

v0.60 can govern commands for:

- light
- outlet
- fan
- thermostat
Discovery is also allowed for:

- scene

- sensor
- camera
- lock
- garage
- security
- appliance
- other

Those additional categories are **discovery-only** in v0.60. Even if a provider
or caller tries to mark them controllable, the registry stores them as
non-controllable. Scenes are intentionally included here because provider-defined
scenes are opaque composites that could indirectly operate a lock, garage or
security device.

This phase therefore does not unlock doors, open garages, arm/disarm security,
operate cameras or run arbitrary appliances.

## Bounded command vocabulary

Lights:

- `on`
- `off`
- `toggle`
- `set_brightness` with 0–100

Outlets and fans:

- `on`
- `off`
- `toggle`

Thermostats:

- `set_temperature` from 50°F to 90°F
- `set_mode`: off / heat / cool / auto

Provider-specific arbitrary command names are not passed through.

## Approval boundary

**Every physical command is approval-required in v0.60.**

`devices.command` is in the global approval-only write-tool set. It cannot be
changed to `safe_automatic` for paired applications.

Owner Control Center buttons also create action requests instead of executing
directly.

The low-level execution primitive requires an `approval_request_id`.

The automation service re-reads that request and verifies:

1. the request exists
2. `action_key == devices.command`
3. `status == executing`
4. normalized device key matches
5. normalized command matches
6. normalized arguments exactly match

Only then can a provider driver be invoked.

A unique database index ensures one physical device action row per approval
request.

## Agent tools

v0.60 adds:

- `homeserver_devices_list` → `devices.list`
- `homeserver_device_command_request` → `devices.command`

The Agent may read device state when the tool is available.

When write proposals are enabled, the Agent may propose a physical command, but
it receives a pending action request rather than execution authority.

Cancelling/interruption continues to use the existing pending-action cleanup
logic from v0.40.

## Paired applications

New coarse permissions:

- `devices.read`
- `devices.control`

They are further narrowed by existing app tool scopes.

A paired application with `devices.control` can create a pending
`devices.command` request. It cannot execute the command directly and cannot
change the tool to safe-automatic.

Paired capability status is aggregate-only:

- room count
- device count
- controllable-device count
- explicit `ambient_direct_execution=false`

It does not expose provider secrets or private device metadata.

## Provider drivers

A provider record may advertise `executable=true`, but the provider is not
currently executable until a local driver is actually registered in-process.

```python
register_driver("home-assistant-local", driver)
```

The driver receives:

- normalized device
- normalized command
- normalized command arguments

It returns a bounded state object.

The core does not pass arbitrary provider command strings through to the
driver.

## Suggestions and Ambient

v0.60 includes persistent automation suggestions.

A suggestion may originate from:

- Agent reasoning
- Cognitive Runtime
- Ambient Runtime
- meeting follow-up logic
- a future local rule engine
- a provider integration

A suggestion itself has **zero execution authority**.

It must be converted into a normal `devices.command` action request and then
approved.

v0.50's Ambient privacy contract remains intact. Presence itself is not
automatically persisted into automation history. A subsystem must explicitly
create a bounded suggestion.

`ambient_direct_execution` is always false in v0.60.

## Control Center

The **Rooms & Devices** view provides:

- room registry
- provider registry
- device registration/discovery surface
- normalized device state
- provider-driver readiness
- safe-category command controls
- pending suggestions
- recent physical action audit

Every command button says **Request** and calls the request endpoint. There is
no direct execution endpoint in the UI.

## Owner endpoints

- `GET /api/v1/control/vp3-os/automation`
- `PUT /api/v1/control/vp3-os/automation/rooms/{room_key}`
- `DELETE /api/v1/control/vp3-os/automation/rooms/{room_key}`
- `PUT /api/v1/control/vp3-os/automation/providers/{provider_key}`
- `PUT /api/v1/control/vp3-os/automation/devices/{device_key}`
- `POST /api/v1/control/vp3-os/automation/devices/{device_key}/request`
- `GET /api/v1/control/vp3-os/automation/actions`
- `GET /api/v1/control/vp3-os/automation/suggestions`
- `POST /api/v1/control/vp3-os/automation/suggestions/{id}/request`
- `POST /api/v1/control/vp3-os/automation/suggestions/{id}/dismiss`

All routes live under the existing owner gateway.

## What v0.60 does not do

- no automatic physical actions from Ambient
- no arbitrary shell/device commands
- no lock/garage/security/camera control
- no cloud-side device execution
- no third-party integration claimed without a bound local driver
- no safe-automatic device commands
- no direct Control Center execution bypass

## Next phase

After v0.60 is hardened, a logical next phase is **VP3 OS v0.70 — Local
Automation Rules & Routines**: owner-defined rules/scenes, schedules and
conditions that still compile into governed device requests rather than
unbounded scripts.
