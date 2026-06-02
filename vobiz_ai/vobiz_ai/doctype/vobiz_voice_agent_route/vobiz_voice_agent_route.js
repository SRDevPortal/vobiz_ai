frappe.ui.form.on("Vobiz Voice Agent Route", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Deploy / Sync to LiveKit"), () => {
				frappe.call({
					method: "vobiz_ai.api.livekit.sync_voice_agent_route",
					args: { route: frm.doc.name },
					freeze: true,
					freeze_message: __("Syncing route to LiveKit..."),
					callback(r) {
						if (r.message) {
							frappe.msgprint({
								title: __("LiveKit Sync"),
								message: r.message.message || __("Route synced to LiveKit."),
								indicator: r.message.ok ? "green" : "orange",
							});
							frm.reload_doc();
						}
					},
				});
			});
		}
	},
});
