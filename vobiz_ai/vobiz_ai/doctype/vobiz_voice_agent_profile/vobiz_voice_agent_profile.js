frappe.ui.form.on("Vobiz Voice Agent Profile", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Deploy / Sync to LiveKit"), () => {
			const run_sync = () => {
				frappe.call({
					method: "vobiz_ai.api.livekit.sync_voice_agent_profile",
					args: { profile: frm.doc.name },
					freeze: true,
					freeze_message: __("Syncing LiveKit route..."),
					callback(response) {
						const message = response.message || {};
						if (message.message) {
							frappe.msgprint(message.message);
						}
						frm.reload_doc();
					},
				});
			};

			if (frm.is_dirty()) {
				frm.save().then(() => {
					if (frm.doc.auto_sync_to_livekit) {
						frappe.show_alert({
							message: __("Saved. Auto Sync to LiveKit is enabled, so the sync job has been queued."),
							indicator: "green",
						});
						setTimeout(() => frm.reload_doc(), 2000);
					} else {
						run_sync();
					}
				});
				return;
			}

			run_sync();
		});
	},
});
