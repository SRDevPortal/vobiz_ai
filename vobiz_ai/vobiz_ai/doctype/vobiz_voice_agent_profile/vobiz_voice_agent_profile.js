frappe.ui.form.on("Vobiz Voice Agent Profile", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Deploy / Sync to LiveKit"), () => {
			frm.save().then(() => {
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
			});
		});
	},
});
