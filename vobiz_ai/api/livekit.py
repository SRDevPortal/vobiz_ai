from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import frappe
from frappe.utils import now_datetime

from vobiz_ai.api.utils import get_password, get_settings, has_manager_role
from vobiz_ai.api.utils import normalize_phone


def _require_manager() -> None:
	if frappe.session.user == "Guest" or not has_manager_role():
		frappe.throw("Only Vobiz AI managers can sync LiveKit routes", frappe.PermissionError)


def _get_lk_path(settings) -> str:
	path = (settings.get("livekit_cli_path") or "").strip()
	if path and os.path.exists(path):
		return path
	found = shutil.which("lk")
	if found:
		return found
	for candidate in (
		os.path.expanduser("~/.local/bin/lk"),
		"/home/jagmohan/.local/bin/lk",
		"/usr/local/bin/lk",
	):
		if os.path.exists(candidate):
			return candidate
	frappe.throw("LiveKit CLI was not found. Install lk on the server PATH or set LiveKit CLI Path in Vobiz AI Settings.")


def _livekit_env(settings) -> dict[str, str]:
	livekit_url = (settings.livekit_url or "").strip()
	api_key = get_password(settings, "livekit_api_key")
	api_secret = get_password(settings, "livekit_api_secret")
	env = os.environ.copy()
	if livekit_url and api_key and api_secret:
		env.update(
			{
				"LIVEKIT_URL": livekit_url,
				"LIVEKIT_API_KEY": api_key,
				"LIVEKIT_API_SECRET": api_secret,
			}
		)
	return env


def _livekit_credentials(settings) -> tuple[str, str, str]:
	livekit_url = (settings.livekit_url or "").strip()
	api_key = get_password(settings, "livekit_api_key")
	api_secret = get_password(settings, "livekit_api_secret")
	if not livekit_url or not api_key or not api_secret:
		frappe.throw("LiveKit URL, API Key, and API Secret are required in Vobiz AI Settings.")
	return livekit_url, api_key, api_secret


def _run_async(coro):
	try:
		asyncio.get_running_loop()
	except RuntimeError:
		return asyncio.run(coro)

	loop = asyncio.new_event_loop()
	try:
		return loop.run_until_complete(coro)
	finally:
		loop.close()


def _run_lk(args: list[str], payload: dict[str, Any] | None = None, timeout: int = 45) -> str:
	settings = get_settings()
	project = _get_livekit_cloud_project(settings)
	if project and "--project" not in args:
		args = ["--project", project, *args]
	process = subprocess.run(
		[_get_lk_path(settings), *args],
		input=json.dumps(payload) if payload is not None else None,
		text=True,
		capture_output=True,
		timeout=timeout,
		env=_livekit_env(settings),
		check=False,
	)
	if process.returncode:
		message = (process.stderr or process.stdout or "LiveKit CLI command failed").strip()
		frappe.throw(message)
	return process.stdout or ""


def _run_lk_with_optional_env(args: list[str], extra_env: dict[str, str], timeout: int = 45) -> str:
	settings = get_settings()
	project = _get_livekit_cloud_project(settings)
	if project and "--project" not in args:
		args = ["--project", project, *args]
	env = _livekit_env(settings)
	env.update(extra_env)
	process = subprocess.run(
		[_get_lk_path(settings), *args],
		text=True,
		capture_output=True,
		timeout=timeout,
		env=env,
		check=False,
	)
	if process.returncode:
		message = (process.stderr or process.stdout or "LiveKit CLI command failed").strip()
		frappe.throw(message)
	return process.stdout or ""


def _get_livekit_cloud_project(settings) -> str:
	return (settings.get("livekit_cli_project") or "").strip()


def _require_cloud_agent_sync_settings(settings) -> None:
	missing = []
	if not _get_livekit_cloud_project(settings):
		missing.append("LiveKit Cloud Project ID / Slug")
	if not (settings.get("livekit_url") or "").strip():
		missing.append("LiveKit URL")
	if not get_password(settings, "livekit_api_key"):
		missing.append("LiveKit API Key")
	if not get_password(settings, "livekit_api_secret"):
		missing.append("LiveKit API Secret")
	if not (settings.get("livekit_cloud_agent_id") or "").strip():
		missing.append("LiveKit Cloud Agent ID")
	if missing:
		frappe.throw("Please set these fields in Vobiz AI Settings first: " + ", ".join(missing))


def _require_livekit_route_sync_settings(settings) -> None:
	missing = []
	if not (settings.get("livekit_url") or "").strip():
		missing.append("LiveKit URL")
	if not get_password(settings, "livekit_api_key"):
		missing.append("LiveKit API Key")
	if not get_password(settings, "livekit_api_secret"):
		missing.append("LiveKit API Secret")
	if missing:
		frappe.throw("Please set these fields in Vobiz AI Settings first: " + ", ".join(missing))


def _json_from_lk_output(output: str) -> dict[str, Any]:
	match = re.search(r"\{.*\}", output or "", flags=re.S)
	if not match:
		return {}
	return frappe.parse_json(match.group(0))


def _safe_slug(value: str) -> str:
	value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "").strip("-").lower()
	return value or "voice-agent"


def _parse_agent_list(output: str) -> list[dict[str, str]]:
	rows = []
	for line in (output or "").splitlines():
		if "│" not in line or "CA_" not in line:
			continue
		parts = [part.strip() for part in line.strip("│").split("│")]
		if len(parts) >= 2 and parts[0].startswith("CA_"):
			rows.append({"id": parts[0], "dispatch_name": parts[1]})
	return rows


def _find_agent_id_by_dispatch_name(dispatch_name: str) -> str:
	output = _run_lk(["agent", "list"])
	for row in _parse_agent_list(output):
		if row["dispatch_name"] == dispatch_name:
			return row["id"]
	return ""


def _agent_config_path(settings, profile) -> Path:
	source_dir = Path((settings.get("livekit_agent_source_dir") or "/home/jagmohan/gemini_live_agent").strip())
	return source_dir / f"livekit.{_safe_slug(profile.profile_key or profile.name)}.toml"


def _read_agent_id_from_config(config_path: Path) -> str:
	if not config_path.exists():
		return ""
	match = re.search(r'\bid\s*=\s*"([^"]+)"', config_path.read_text(encoding="utf-8"))
	return match.group(1) if match else ""


def _ensure_agent_config(settings, profile, agent_id: str = "") -> Path:
	source_dir = Path((settings.get("livekit_agent_source_dir") or "/home/jagmohan/gemini_live_agent").strip())
	if not source_dir.exists():
		frappe.throw(f"LiveKit Agent Source Directory does not exist: {source_dir}")

	config_path = _agent_config_path(settings, profile)
	base_config = source_dir / "livekit.toml"
	project_block = '[project]\n'
	if base_config.exists():
		text = base_config.read_text(encoding="utf-8")
		match = re.search(r"(?ms)^\[project\].*?(?=^\[|\Z)", text)
		if match:
			project_block = match.group(0).strip() + "\n"

	content = project_block.rstrip() + "\n\n[agent]\n"
	if agent_id:
		content += f'  id = "{agent_id}"\n'
	elif config_path.exists():
		existing_id = _read_agent_id_from_config(config_path)
		if existing_id:
			content += f'  id = "{existing_id}"\n'
	config_path.write_text(content, encoding="utf-8")
	return config_path


def _profile_secret_args(settings, profile) -> list[str]:
	base_url = _get_public_frappe_base_url(settings)

	dispatch_name = (profile.livekit_agent_name or "").strip()
	if not dispatch_name:
		frappe.throw("LiveKit Agent Dispatch Name is required on the profile.")

	google_project = profile.google_cloud_project or os.getenv("GOOGLE_CLOUD_PROJECT") or "newapp-496411"
	vertex_location = profile.vertex_location or os.getenv("VERTEX_LOCATION") or "us-central1"
	config_secret = get_password(settings, "voice_agent_config_secret")
	secrets = [
		"GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/creds.json",
		f"GOOGLE_CLOUD_PROJECT={google_project}",
		f"VERTEX_LOCATION={vertex_location}",
		f"FRAPPE_BASE_URL={base_url}",
		f"LIVEKIT_AGENT_NAME={dispatch_name}",
	]
	if config_secret:
		secrets.extend(
			[
				f"VOICE_AGENT_CONFIG_SECRET={config_secret}",
				f"X_VOICE_AGENT_SECRET={config_secret}",
			]
		)

	args: list[str] = []
	credentials_file = (settings.get("livekit_google_credentials_file") or "").strip()
	if credentials_file and os.path.exists(credentials_file):
		args.extend(["--secret-mount", credentials_file])
	for secret in secrets:
		args.extend(["--secrets", secret])
	args.append("--ignore-empty-secrets")
	return args


def ensure_livekit_cloud_agent(profile) -> str:
	settings = get_settings()
	dispatch_name = (profile.livekit_agent_name or "").strip()
	if not dispatch_name:
		frappe.throw("LiveKit Agent Dispatch Name is required on the profile.")

	agent_id = profile.livekit_cloud_agent_id or ""
	if agent_id:
		config_path = _ensure_agent_config(settings, profile, agent_id)
		source_dir = Path((settings.get("livekit_agent_source_dir") or "/home/jagmohan/gemini_live_agent").strip())
		secret_args = _profile_secret_args(settings, profile)
		_run_lk(["agent", "deploy", "--config", str(config_path), "--region", "ap-south", "--silent", *secret_args, str(source_dir)], timeout=900)
		return agent_id

	agent_id = _find_agent_id_by_dispatch_name(dispatch_name)
	config_path = _ensure_agent_config(settings, profile, agent_id)
	source_dir = Path((settings.get("livekit_agent_source_dir") or "/home/jagmohan/gemini_live_agent").strip())
	secret_args = _profile_secret_args(settings, profile)
	common_args = ["--config", str(config_path), "--region", "ap-south", "--silent", *secret_args, str(source_dir)]

	if agent_id:
		profile.db_set("livekit_cloud_agent_id", agent_id, update_modified=False)
	else:
		_run_lk(["agent", "create", *common_args], timeout=900)
		agent_id = _read_agent_id_from_config(config_path) or _find_agent_id_by_dispatch_name(dispatch_name)
		if not agent_id:
			frappe.throw(f"LiveKit Cloud Agent was created but ID could not be resolved for {dispatch_name}.")
		_ensure_agent_config(settings, profile, agent_id)

	if profile.livekit_cloud_agent_id != agent_id:
		profile.db_set("livekit_cloud_agent_id", agent_id, update_modified=False)
	return agent_id


def _dispatch_rule_payload(route, profile) -> dict[str, Any]:
	settings = get_settings()
	base_url = _get_public_frappe_base_url(settings)
	agent_name = (
		route.livekit_agent_name
		or profile.livekit_agent_name
		or settings.livekit_agent_name
		or "vobiz-gemini-live"
	)
	metadata = {
		"company_key": (settings.get("company_key") or "").strip(),
		"frappe_base_url": base_url,
		"voice_agent_profile": profile.name,
		"profile_key": profile.profile_key,
		"did_number": route.did_number,
		"account_mapping": route.account_mapping,
		"trunk_id": route.trunk_id,
		"domain": route.domain,
	}
	metadata = {key: value for key, value in metadata.items() if value}
	rule = {
		"rule": {
			"dispatchRuleIndividual": {
				"roomPrefix": route.room_prefix or "vobiz-",
			}
		},
		"name": f"vobiz-{profile.profile_key}",
		"attributes": {
			"vobiz.voice_agent_profile": profile.name,
			"vobiz.profile_key": profile.profile_key,
			"vobiz.did_number": route.did_number,
		},
		"roomConfig": {
			"agents": [
				{
					"agentName": agent_name,
					"metadata": json.dumps(metadata, ensure_ascii=False),
				}
			]
		},
	}
	if route.livekit_inbound_trunk_id:
		rule["trunkIds"] = [route.livekit_inbound_trunk_id]
	return rule


def _find_rule_id_by_name(name: str) -> str:
	return _run_async(_find_rule_id_by_name_async(name))


async def _find_rule_id_by_name_async(name: str) -> str:
	from livekit import api

	settings = get_settings()
	livekit_url, api_key, api_secret = _livekit_credentials(settings)
	livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)
	try:
		response = await livekit_api.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
		for item in response.items:
			if item.name == name:
				return item.sip_dispatch_rule_id or ""
	finally:
		await livekit_api.aclose()
	return ""


def _livekit_dispatch_request(payload: dict[str, Any]):
	from livekit import api

	rule_data = payload.get("rule", {}).get("dispatchRuleIndividual") or {}
	agents = []
	for agent in payload.get("roomConfig", {}).get("agents") or []:
		agents.append(
			api.RoomAgentDispatch(
				agent_name=agent.get("agentName") or "",
				metadata=agent.get("metadata") or "",
			)
	)
	room_config = api.RoomConfiguration(agents=agents) if agents else None
	return api.CreateSIPDispatchRuleRequest(
		rule=api.SIPDispatchRule(
			dispatch_rule_individual=api.SIPDispatchRuleIndividual(
				room_prefix=rule_data.get("roomPrefix") or "vobiz-",
			)
		),
		trunk_ids=payload.get("trunkIds") or [],
		name=payload.get("name") or "",
		attributes=payload.get("attributes") or {},
		room_config=room_config,
	)


def _livekit_dispatch_update(payload: dict[str, Any]):
	from livekit import api

	request = _livekit_dispatch_request(payload)
	return api.SIPDispatchRuleInfo(
		rule=request.rule,
		trunk_ids=request.trunk_ids,
		name=request.name,
		attributes=request.attributes,
		room_config=request.room_config,
	)


def _create_or_update_dispatch_rule(rule_id: str, payload: dict[str, Any]):
	return _run_async(_create_or_update_dispatch_rule_async(rule_id, payload))


async def _create_or_update_dispatch_rule_async(rule_id: str, payload: dict[str, Any]):
	from livekit import api

	settings = get_settings()
	livekit_url, api_key, api_secret = _livekit_credentials(settings)
	livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)
	try:
		if rule_id:
			return await livekit_api.sip.update_sip_dispatch_rule(rule_id, _livekit_dispatch_update(payload))
		return await livekit_api.sip.create_sip_dispatch_rule(_livekit_dispatch_request(payload))
	finally:
		await livekit_api.aclose()


def _mark_sync(route, profile, status: str, rule_id: str = "", error: str = "") -> None:
	now = now_datetime()
	route.db_set(
		{
			"livekit_sync_status": status,
			"livekit_dispatch_rule_id": rule_id or route.livekit_dispatch_rule_id,
			"last_synced_at": now if status == "Synced" else route.last_synced_at,
			"last_sync_error": error,
		},
		update_modified=False,
	)
	profile.db_set(
		{
			"livekit_dispatch_rule_id": rule_id or profile.livekit_dispatch_rule_id,
			"livekit_sync_status": status,
			"last_synced_at": now if status == "Synced" else profile.last_synced_at,
			"last_sync_error": error,
		},
		update_modified=False,
	)


def _mark_profile_sync(profile, status: str, error: str = "") -> None:
	profile.db_set(
		{
			"livekit_sync_status": status,
			"last_sync_error": error,
		},
		update_modified=False,
	)


@frappe.whitelist()
def sync_frappe_base_url_secret() -> dict[str, Any]:
	_require_manager()
	settings = get_settings()
	_require_cloud_agent_sync_settings(settings)
	base_url = _get_public_frappe_base_url(settings)
	agent_id = (settings.get("livekit_cloud_agent_id") or "").strip()

	config_secret = get_password(settings, "voice_agent_config_secret")
	secrets = [f"FRAPPE_BASE_URL={base_url}"]
	if config_secret:
		secrets.extend(
			[
				f"VOICE_AGENT_CONFIG_SECRET={config_secret}",
				f"X_VOICE_AGENT_SECRET={config_secret}",
			]
		)

	args = [
		"agent",
		"update-secrets",
		"--id",
		agent_id,
	]
	for secret in secrets:
		args.extend(["--secrets", secret])
	args.append("--ignore-empty-secrets")

	_run_lk_with_optional_env(
		args,
		{},
	)
	return {"ok": True, "message": f"LiveKit agent {agent_id} now uses {base_url}. Agent secrets were updated and LiveKit will restart the agent."}


@frappe.whitelist()
def test_livekit_connection() -> dict[str, Any]:
	_require_manager()
	settings = get_settings()
	_require_livekit_route_sync_settings(settings)
	rule_count = _run_async(_count_livekit_dispatch_rules_async(settings))
	return {
		"ok": True,
		"message": f"LiveKit connection is working. Frappe can call LiveKit SIP APIs with the configured API credentials. Dispatch rules found: {rule_count}.",
	}


async def _count_livekit_dispatch_rules_async(settings) -> int:
	from livekit import api

	livekit_url, api_key, api_secret = _livekit_credentials(settings)
	livekit_api = api.LiveKitAPI(livekit_url, api_key, api_secret)
	try:
		response = await livekit_api.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
		return len(response.items)
	finally:
		await livekit_api.aclose()


@frappe.whitelist()
def sync_all_voice_agent_routes() -> dict[str, Any]:
	_require_manager()
	settings = get_settings()
	_require_livekit_route_sync_settings(settings)
	routes = frappe.get_all(
		"Vobiz Voice Agent Route",
		filters={"active": 1},
		pluck="name",
		order_by="modified desc",
	)
	results = []
	for route in routes:
		try:
			results.append(_sync_voice_agent_route(route))
		except Exception:
			results.append({"ok": False, "route": route, "error": frappe.get_traceback()})
	return {
		"ok": all(row.get("ok") for row in results),
		"count": len(results),
		"results": results,
		"message": f"Synced {sum(1 for row in results if row.get('ok'))} of {len(results)} active LiveKit route(s).",
	}


def _get_public_frappe_base_url(settings) -> str:
	base_url = (settings.get("frappe_base_url") or "").strip().rstrip("/")
	if not base_url:
		frappe.throw("Public Frappe Base URL is required in Vobiz AI Settings.")
	if not base_url.startswith(("http://", "https://")):
		frappe.throw("Public Frappe Base URL must start with http:// or https://")
	return base_url


@frappe.whitelist()
def upsert_voice_agent_route(
	did_number: str,
	voice_agent_profile: str,
	livekit_inbound_trunk_id: str = "",
	display_label: str = "",
	account_mapping: str = "",
) -> str:
	_require_manager()
	normalized_did = normalize_phone(did_number)
	name = frappe.db.get_value("Vobiz Voice Agent Route", {"normalized_did": normalized_did})
	if name:
		doc = frappe.get_doc("Vobiz Voice Agent Route", name)
	else:
		doc = frappe.new_doc("Vobiz Voice Agent Route")

	doc.update(
		{
			"active": 1,
			"display_label": display_label or f"{voice_agent_profile} ({did_number})",
			"did_number": did_number,
			"voice_agent_profile": voice_agent_profile,
			"account_mapping": account_mapping,
			"livekit_inbound_trunk_id": livekit_inbound_trunk_id,
			"livekit_agent_name": get_settings().livekit_agent_name or "vobiz-gemini-live",
			"room_prefix": "vobiz-",
		}
	)
	doc.save(ignore_permissions=True)
	return doc.name


def _copy_profile_route_fields(profile, route) -> None:
	route.update(
		{
			"active": 1 if profile.enabled else 0,
			"display_label": profile.agent_name or profile.name,
			"did_number": profile.did_number,
			"voice_agent_profile": profile.name,
			"account_mapping": profile.account_mapping,
			"livekit_inbound_trunk_id": profile.livekit_inbound_trunk_id,
			"trunk_id": profile.trunk_id,
			"domain": profile.domain,
			"livekit_agent_name": profile.livekit_agent_name or get_settings().livekit_agent_name or "vobiz-gemini-live",
			"room_prefix": profile.room_prefix or "vobiz-",
		}
	)


@frappe.whitelist()
def sync_voice_agent_profile(profile: str) -> dict[str, Any]:
	_require_manager()
	return _sync_voice_agent_profile(profile)


def sync_voice_agent_profile_from_save(profile: str) -> dict[str, Any]:
	frappe.set_user("Administrator")
	return _sync_voice_agent_profile(profile)


def _sync_voice_agent_profile(profile: str) -> dict[str, Any]:
	doc = frappe.get_doc("Vobiz Voice Agent Profile", profile)
	if not doc.enabled:
		frappe.throw("Voice Agent Profile is disabled.")
	if not doc.did_number:
		frappe.throw("DID / Phone Number is required on the profile before syncing.")
	if not doc.livekit_inbound_trunk_id:
		frappe.throw("LiveKit Inbound Trunk ID is required on the profile before syncing.")

	try:
		normalized_did = normalize_phone(doc.did_number)
		route_name = frappe.db.get_value("Vobiz Voice Agent Route", {"voice_agent_profile": doc.name})
		if not route_name and normalized_did:
			route_name = frappe.db.get_value("Vobiz Voice Agent Route", {"normalized_did": normalized_did})

		route = frappe.get_doc("Vobiz Voice Agent Route", route_name) if route_name else frappe.new_doc("Vobiz Voice Agent Route")
		_copy_profile_route_fields(doc, route)
		route.save(ignore_permissions=True)
		result = _sync_voice_agent_route(route.name)
		route.reload()
		doc.db_set(
			{
				"livekit_dispatch_rule_id": route.livekit_dispatch_rule_id,
				"livekit_sync_status": route.livekit_sync_status,
				"last_synced_at": route.last_synced_at,
				"last_sync_error": route.last_sync_error,
			},
			update_modified=False,
		)
		result["route"] = route.name
		return result
	except Exception as e:
		error = frappe.get_traceback()
		_mark_profile_sync(doc, "Failed", error=str(e))
		frappe.log_error(error, "Vobiz LiveKit profile sync failed")
		frappe.db.commit()
		raise


@frappe.whitelist()
def sync_voice_agent_route(route: str) -> dict[str, Any]:
	_require_manager()
	return _sync_voice_agent_route(route)


def _sync_voice_agent_route(route: str) -> dict[str, Any]:
	doc = frappe.get_doc("Vobiz Voice Agent Route", route)
	profile = frappe.get_doc("Vobiz Voice Agent Profile", doc.voice_agent_profile)
	if not doc.active:
		frappe.throw("Route is not active.")
	if not profile.enabled:
		frappe.throw("Voice Agent Profile is disabled.")
	if not doc.livekit_inbound_trunk_id:
		frappe.throw("LiveKit Inbound Trunk ID is required before syncing this route.")

	payload = _dispatch_rule_payload(doc, profile)
	try:
		rule_id = doc.livekit_dispatch_rule_id or _find_rule_id_by_name(payload["name"])
		synced_rule = _create_or_update_dispatch_rule(rule_id, payload)
		rule_id = getattr(synced_rule, "sip_dispatch_rule_id", "") or rule_id or _find_rule_id_by_name(payload["name"])
		_mark_sync(doc, profile, "Synced", rule_id=rule_id)
		return {
			"ok": True,
			"dispatch_rule_id": rule_id,
			"message": f"Route synced to LiveKit dispatch rule {rule_id or '(created)'}",
		}
	except Exception as e:
		error = frappe.get_traceback()
		_mark_sync(doc, profile, "Failed", error=str(e))
		frappe.log_error(error, "Vobiz LiveKit route sync failed")
		frappe.db.commit()
		raise
