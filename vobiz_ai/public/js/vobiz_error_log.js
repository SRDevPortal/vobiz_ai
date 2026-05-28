frappe.ui.form.on('Vobiz Error Log', {
	refresh(frm) {
		if (!frm.doc.resolved && (frappe.user_roles.includes('Vobiz AI Manager') || frappe.user_roles.includes('System Manager'))) {
			frm.add_custom_button(__('Retry'), () => {
				frappe.call({
					method: 'vobiz_ai.api.retry.retry_error',
					args: { error_log: frm.doc.name },
					freeze: true,
					callback() {
						frm.reload_doc();
					},
				});
			});
		}
	},
});
