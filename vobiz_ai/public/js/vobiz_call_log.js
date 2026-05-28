frappe.ui.form.on('Vobiz Call Log', {
	refresh(frm) {
		if (frm.doc.recording_url) {
			frm.add_custom_button(__('Open Recording'), () => {
				window.open(frm.doc.recording_url, '_blank', 'noopener=yes');
			});
		}

		if (frm.doc.transcription_text || frm.doc.transcript_text) {
			frm.add_custom_button(__('Retry AI Score'), () => {
				frappe.call({
					method: 'vobiz_ai.api.ai.retry_score',
					args: { call_log: frm.doc.name },
					freeze: true,
					freeze_message: __('Queueing AI score...'),
					callback() {
						frappe.show_alert({ message: __('AI score queued'), indicator: 'green' });
					},
				});
			});
		}
	},
});
