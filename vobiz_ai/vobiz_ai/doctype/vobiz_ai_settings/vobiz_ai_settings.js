frappe.ui.form.on("Vobiz AI Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test LiveKit Connection"), () => {
			frappe.call({
				method: "vobiz_ai.api.livekit.test_livekit_connection",
				freeze: true,
				freeze_message: __("Testing LiveKit connection..."),
				callback(r) {
					if (r.message) {
						frappe.msgprint({
							title: __("LiveKit Connection"),
							message: r.message.message || __("LiveKit connection is working."),
							indicator: r.message.ok ? "green" : "orange",
						});
					}
				},
			});
		});

		frm.add_custom_button(__("Deploy / Sync Dispatch Rules"), () => {
			frappe.confirm(
				__("Sync all active Vobiz Voice Agent Routes to LiveKit dispatch rules?"),
				() => {
					frappe.call({
						method: "vobiz_ai.api.livekit.sync_all_voice_agent_routes",
						freeze: true,
						freeze_message: __("Syncing LiveKit dispatch rules..."),
						callback(r) {
							if (r.message) {
								frappe.msgprint({
									title: __("LiveKit Dispatch Rules"),
									message: r.message.message || __("Dispatch rules synced."),
									indicator: r.message.ok ? "green" : "orange",
								});
							}
						},
					});
				}
			);
		});
	},
});
