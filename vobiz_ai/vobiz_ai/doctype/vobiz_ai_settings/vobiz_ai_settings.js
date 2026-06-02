frappe.ui.form.on("Vobiz AI Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Sync Base URL to LiveKit"), () => {
			frappe.call({
				method: "vobiz_ai.api.livekit.sync_frappe_base_url_secret",
				freeze: true,
				freeze_message: __("Updating LiveKit agent secret..."),
				callback(r) {
					if (r.message) {
						frappe.msgprint({
							title: __("LiveKit Secret Updated"),
							message: r.message.message || __("Frappe base URL synced to LiveKit."),
							indicator: r.message.ok ? "green" : "orange",
						});
					}
				},
			});
		});
	},
});
