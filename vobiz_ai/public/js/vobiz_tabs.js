frappe.ui.form.on('CRM Lead', {
	refresh(frm) {
		render_vobiz_calls(frm, 'CRM Lead');
	},
});

frappe.ui.form.on('Patient', {
	refresh(frm) {
		show_vobiz_patient_indicator(frm);
		render_vobiz_calls(frm, 'Patient');
	},
});

function show_vobiz_patient_indicator(frm) {
	if (frm.is_new()) return;

	const count = cint(frm.doc.vobiz_call_count || 0);
	const indicator = frm.doc.vobiz_call_indicator;
	if (!count && !indicator) return;

	const temp = frm.doc.vobiz_lead_temperature || 'Unscored';
	const score = frm.doc.vobiz_lead_score || 0;
	const label = `New Vobiz Hit (${count || 1}) | ${temp} | Score ${score}`;
	frm.dashboard.add_indicator(label, temp === 'Hot' ? 'red' : temp === 'Warm' ? 'orange' : 'blue');

	frm.add_custom_button(__('Open Vobiz Calls'), () => {
		frappe.route_options = { patient: frm.doc.name };
		frappe.set_route('List', 'Vobiz Call Log');
	});

	frm.add_custom_button(__('Open Vobiz Issue'), () => {
		frappe.route_options = { vobiz_patient: frm.doc.name };
		frappe.set_route('List', 'Issue');
	});
}

function render_vobiz_calls(frm, doctype) {
	if (frm.is_new() || !frm.fields_dict.vobiz_ai_calls_html) return;
	frappe.call({
		method: 'vobiz_ai.api.processing.get_related_calls',
		args: { doctype, name: frm.doc.name },
		callback(r) {
			const rows = r.message || [];
			const body = rows.length ? rows.map(row => {
				const recording = row.recording_url ? `<a href="${frappe.utils.escape_html(row.recording_url)}" target="_blank">Recording</a>` : '';
				return `<tr>
					<td><a href="/app/vobiz-call-log/${encodeURIComponent(row.name)}">${frappe.datetime.str_to_user(row.start_time || '')}</a></td>
					<td>${frappe.utils.escape_html(row.direction || '')}</td>
					<td>${frappe.utils.escape_html(row.status || '')}</td>
					<td>${frappe.utils.escape_html(row.customer_number || '')}</td>
					<td>${frappe.utils.escape_html(row.did_number || '')}</td>
					<td>${frappe.utils.escape_html(row.caller_classification || '')}</td>
					<td>${frappe.utils.escape_html(row.lead_temperature || '')}</td>
					<td>${row.lead_score || ''}</td>
					<td>${row.kamal_involved ? 'Yes' : ''}</td>
					<td>${recording}</td>
				</tr>`;
			}).join('') : '<tr><td colspan="10" class="text-muted">No Vobiz calls found.</td></tr>';
			frm.fields_dict.vobiz_ai_calls_html.$wrapper.html(`
				<div class="table-responsive">
					<table class="table table-bordered table-sm">
						<thead><tr>
							<th>Time</th><th>Direction</th><th>Status</th><th>Customer</th><th>DID</th>
							<th>Type</th><th>Temp</th><th>Score</th><th>Kamal</th><th>Recording</th>
						</tr></thead>
						<tbody>${body}</tbody>
					</table>
				</div>
			`);
		},
	});
}
