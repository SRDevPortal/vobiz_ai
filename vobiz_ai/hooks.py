app_name = "vobiz_ai"
app_title = "Vobiz AI"
app_publisher = "SRIAAS"
app_description = "Vobiz AI telephony integration for ERPNext and Frappe CRM"
app_email = "webdevelopersriaas@gmail.com"
app_license = "MIT"

required_apps = ["frappe", "crm", "healthcare"]

after_install = "vobiz_ai.install.after_install"
after_migrate = "vobiz_ai.install.after_migrate"

doctype_js = {
    "CRM Lead": "public/js/vobiz_tabs.js",
    "Patient": "public/js/vobiz_tabs.js",
    "Vobiz Call Log": "public/js/vobiz_call_log.js",
    "Vobiz Error Log": "public/js/vobiz_error_log.js",
}

doctype_list_js = {
    "CRM Lead": "public/js/crm_lead_vobiz_list.js",
    "Patient": "public/js/patient_vobiz_list.js",
}

permission_query_conditions = {
    "Vobiz Call Log": "vobiz_ai.permissions.get_call_log_permission_query_conditions",
    "Vobiz Error Log": "vobiz_ai.permissions.get_error_log_permission_query_conditions",
}

has_permission = {
    "Vobiz Call Log": "vobiz_ai.permissions.has_call_log_permission",
    "Vobiz Error Log": "vobiz_ai.permissions.has_error_log_permission",
}

scheduled_events = {
    "all": [
        "vobiz_ai.api.processing.enqueue_queued_webhook_events",
    ],
}

website_route_rules = [
    {"from_route": "/vobiz-error-log", "to_route": "vobiz_error_log"},
]

doc_events = {
    "CRM Lead": {
        "before_save": "vobiz_ai.api.utils.update_phone_search_fields",
    },
    "Patient": {
        "before_save": "vobiz_ai.api.utils.update_phone_search_fields",
    },
}

fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "Vobiz AI"]]},
    {"dt": "Property Setter", "filters": [["module", "=", "Vobiz AI"]]},
    {"dt": "Workspace", "filters": [["module", "=", "Vobiz AI"]]},
    {"dt": "Role", "filters": [["name", "in", ["Vobiz AI Manager"]]]},
]
