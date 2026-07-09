# Vobiz AI Follow-up Routing Changes

Date: 2026-07-09

This document summarizes the changes made in `apps/vobiz_ai` for routing existing patient inbound calls to agent phone numbers based on the patient's Follow-up ID.

## Goal

When an inbound Vobiz AI call comes from an existing Patient, the system now checks the Patient's `sr_followup_id` and returns routing details for the correct follow-up team. A routing group can contain multiple agent phone numbers, and the system selects an available agent based on the configured routing strategy.

## Main App Changed

Changes were made in:

`apps/vobiz_ai`

The assignment app was not used for this implementation because the requested routing is based on Patient Follow-up ID and inbound Vobiz AI call configuration.

## New Configuration DocTypes

### Vobiz Followup Routing Group

Path:

`apps/vobiz_ai/vobiz_ai/vobiz_ai/doctype/vobiz_followup_routing_group/`

Purpose:

Defines which Follow-up ID should route to which team of agents.

Important fields:

- `active`: Enables or disables the routing group.
- `display_label`: Friendly label for the group.
- `sr_followup_id`: Follow-up ID linked to this routing group.
- `did_number`: Optional inbound DID/to number. Leave blank to match all DIDs for the Follow-up ID.
- `strategy`: Agent selection strategy.
- `priority`: Used when more than one routing group can match.
- `reserve_on_config_fetch`: Reserves the selected agent when config is fetched.
- `agents`: Child table containing agent phone numbers.
- `fallback_user`, `fallback_phone`, `fallback_message`: Fallback details when no agent is available.

Supported strategies:

- `Round Robin`
- `First Available`
- `Least Busy`
- `Weighted Balanced`

Desk URL:

`http://localhost:8000/app/vobiz-followup-routing-group`

### Vobiz Followup Routing Agent

Path:

`apps/vobiz_ai/vobiz_ai/vobiz_ai/doctype/vobiz_followup_routing_agent/`

Purpose:

Child table used inside `Vobiz Followup Routing Group` to store multiple agents for the same Follow-up ID.

Important fields:

- `agent_user`: Optional linked Frappe user.
- `agent_phone`: Agent phone number used for call patching.
- `normalized_agent_phone`: Normalized version of the phone number.
- `enabled`: Enables or disables the agent row.
- `availability_status`: `Available`, `Busy`, `Offline`, or `Paused`.
- `priority`: Used for ordering.
- `weight`: Used by weighted balancing.
- `max_active_calls`: Maximum simultaneous calls for the agent phone number. `0` means unlimited.
- `active_call_count`: Current reserved/active calls.
- `today_call_count`: Number of calls routed today.
- `last_patched_at`: Last time this agent was selected.
- `last_transfer_status`: Last transfer result.
- `reservation_token`: Hidden token used to release a reserved agent.

This DocType is a child table, so configuration should be done inside:

`http://localhost:8000/app/vobiz-followup-routing-group`

## Voice Agent Config Changes

File:

`apps/vobiz_ai/vobiz_ai/api/voice_agent.py`

The voice-agent config endpoint now builds patient routing data.

Affected endpoint:

`vobiz_ai.api.voice_agent.get_config`

Also available through:

`vobiz_ai.api.voice_agent.get_voice_agent_config`

New response key:

`patient_routing`

Example shape:

```json
{
  "matched": true,
  "transfer_allowed": true,
  "status": "selected",
  "patient": "PAT-0001",
  "sr_followup_id": "FU-001",
  "routing_group": "VOBIZ-FU-ROUTE-00001",
  "routing_group_label": "Follow-up Team A",
  "strategy": "Round Robin",
  "reservation_token": "...",
  "agent_row": "...",
  "agent_user": "agent@example.com",
  "agent_phone": "9876543210",
  "normalized_agent_phone": "919876543210",
  "selected_agent": {},
  "available_agents": []
}
```

Possible `patient_routing.status` values include:

- `no_patient`: Caller was not matched to a Patient.
- `no_followup_id`: Patient exists but has no Follow-up ID.
- `selected`: An available agent was selected.
- `no_available_agent`: Routing group matched, but no agent is currently available.

## Routing Logic

The system now:

1. Detects the caller phone number from the inbound request.
2. Finds an existing `Patient` by phone number.
3. Reads `Patient.sr_followup_id`.
4. Looks for an active `Vobiz Followup Routing Group` with the same Follow-up ID.
5. Optionally filters by DID/to number if configured on the group.
6. Finds enabled agents with `availability_status = Available`.
7. Skips agents that reached `max_active_calls`.
8. Selects an agent based on the group's strategy.
9. Optionally reserves the selected agent by incrementing `active_call_count`.
10. Returns the selected agent phone number in `patient_routing`.

## Functioning

### 1. Existing Patient Detection

When the inbound voice-agent config API is called, Vobiz AI reads the caller phone number from the request.

The system then searches for an existing `Patient` using known phone fields such as:

- `mobile`
- `mobile_no`
- `phone`
- `custom_whatsapp_number`

If a matching Patient is found, the caller is classified as `Existing Patient`.

The Patient's `sr_followup_id` is also fetched and added to the caller context.

### 2. Follow-up ID Based Routing

After the Patient is identified, the system checks whether the Patient has a Follow-up ID.

If the Patient has no Follow-up ID, routing stops with:

```json
{
  "matched": false,
  "transfer_allowed": false,
  "status": "no_followup_id"
}
```

If the Patient has a Follow-up ID, the system searches for an active `Vobiz Followup Routing Group` where:

- `active = 1`
- `sr_followup_id` matches the Patient's Follow-up ID

If no routing group is found, no agent is selected.

### 3. DID Specific Routing

Each routing group can optionally have a `did_number`.

If `did_number` is blank, the group can be used for that Follow-up ID across all inbound numbers.

If `did_number` is configured, the group is used only when the inbound call's DID/to number matches the configured DID.

This allows the same Follow-up ID to have different routing rules for different business phone numbers.

### 4. Multiple Agents in Same Team

Each `Vobiz Followup Routing Group` can contain multiple `Vobiz Followup Routing Agent` rows.

Each row stores one agent phone number and its availability settings.

The system only considers an agent available when:

- `enabled = 1`
- `availability_status = Available`
- `agent_phone` is present
- `active_call_count` is below `max_active_calls`

If `max_active_calls = 0`, the agent is treated as unlimited.

### 5. Agent Selection

Once available agents are found, the system chooses one agent based on the routing group's `strategy`.

`Round Robin`:

Uses priority, last patched time, today's call count, and row order to distribute calls.

`First Available`:

Uses the first available agent by priority and row order.

`Least Busy`:

Prefers the agent with the lowest active call count and then lowest daily count.

`Weighted Balanced`:

Uses the agent's `weight` to distribute more calls to higher-weight agents while still considering active calls and priority.

### 6. Agent Reservation

If `reserve_on_config_fetch` is enabled on the routing group, the selected agent is reserved as soon as the voice-agent config is fetched.

Reservation updates the agent row:

- `active_call_count` increases by 1.
- `today_call_count` increases by 1.
- `last_patched_at` is updated.
- `last_transfer_status` becomes `Reserved`.
- `reservation_token` is generated.

This prevents another call from selecting the same agent when the agent has reached their active-call limit.

### 7. Response Sent to Voice Worker

The selected routing result is returned inside the voice-agent config response under:

`patient_routing`

When an agent is selected, the response includes:

- `transfer_allowed = true`
- `agent_phone`
- `normalized_agent_phone`
- `agent_user`
- `routing_group`
- `reservation_token`
- `available_agents`

The voice worker should use this response to patch or transfer the call to the selected agent phone number.

### 8. Transfer Status Update

After the voice worker attempts the transfer, it should call:

`vobiz_ai.api.voice_agent.update_patient_routing_status`

This endpoint records the transfer result and releases the reservation when the call is finished or failed.

For example, when status is `completed`, `failed`, `busy`, `no_answer`, `cancelled`, or `released`, the system reduces the agent's `active_call_count`.

If a `Vobiz Call Log` is provided, the call log is also updated with:

- Follow-up ID
- Routing group
- Selected agent row
- Agent user
- Agent phone
- Transfer status

### 9. No Agent Available Case

If a routing group matches but all agents are unavailable, busy, paused, offline, disabled, or at their active-call limit, the API returns:

```json
{
  "matched": true,
  "transfer_allowed": false,
  "status": "no_available_agent"
}
```

In this case, fallback details from the routing group are returned if configured:

- `fallback_user`
- `fallback_phone`
- `fallback_message`

The voice worker can use these fallback details to decide whether to transfer to a fallback number or continue with normal AI handling.

### 10. Daily Reset

Every day after midnight, the scheduled job resets `today_call_count` to `0` for all routing agents.

This keeps daily balancing strategies fresh for the next day.

## New Routing Status Endpoint

File:

`apps/vobiz_ai/vobiz_ai/api/voice_agent.py`

Endpoint:

`vobiz_ai.api.voice_agent.update_patient_routing_status`

Purpose:

Allows the voice worker to update the selected agent's transfer status and release active call reservation after transfer completion/failure.

Expected important parameters:

- `agent_row`
- `reservation_token`
- `status`
- `call_log`

Release statuses:

- `completed`
- `failed`
- `busy`
- `no answer`
- `no_answer`
- `cancelled`
- `canceled`
- `released`

When one of these statuses is received, `active_call_count` is reduced and `reservation_token` is cleared.

## Vobiz Call Log Changes

File:

`apps/vobiz_ai/vobiz_ai/vobiz_ai/doctype/vobiz_call_log/vobiz_call_log.json`

Added a new `Follow-up Routing` section with fields:

- `sr_followup_id`
- `followup_routing_group`
- `followup_routing_agent`
- `followup_routing_user`
- `followup_routing_phone`
- `followup_transfer_status`

These fields help track which Follow-up ID, routing group, and agent were used for the call.

## Call Processing Change

File:

`apps/vobiz_ai/vobiz_ai/api/processing.py`

When a `Vobiz Call Log` is linked to an existing Patient, the system now copies `Patient.sr_followup_id` into the call log if the field exists.

## Daily Counter Reset

File:

`apps/vobiz_ai/vobiz_ai/hooks.py`

Added scheduled job:

```python
"5 0 * * *": [
    "vobiz_ai.api.voice_agent.reset_followup_routing_daily_counts",
]
```

This resets `today_call_count` for `Vobiz Followup Routing Agent` rows daily after midnight.

## Migration

Migration was run successfully for local site:

```bash
bench --site mysite.localhost migrate
```

After migration, the routing group configuration should be available at:

`http://localhost:8000/app/vobiz-followup-routing-group`

## Important Remaining Integration

The Frappe side now returns the selected agent phone number in `patient_routing`.

The actual LiveKit or voice worker still needs to:

1. Read `patient_routing.transfer_allowed`.
2. Use `patient_routing.agent_phone` or `patient_routing.normalized_agent_phone`.
3. Patch/transfer the call to that phone number.
4. Call `update_patient_routing_status` when the transfer completes, fails, is busy, or is released.

Without this worker-side integration, the configuration and selection logic will exist in Frappe, but calls will not actually be patched to the selected agent phone number.
