(() => {
	const settings = frappe.listview_settings['Patient'] || {};
	const previous_onload = settings.onload;
	const previous_formatters = settings.formatters || {};

	settings.add_fields = Array.from(new Set([
		...(settings.add_fields || []),
		'vobiz_call_indicator',
		'vobiz_call_count',
		'vobiz_lead_temperature',
		'vobiz_lead_score',
		'vobiz_lead_language',
		'vobiz_latest_call_time',
	]));

	settings.formatters = {
		...previous_formatters,
		vobiz_call_indicator(value, df, doc) {
			if (!value && !doc.vobiz_call_count) return '';
			const count = cint(doc.vobiz_call_count || 1);
			const temp = doc.vobiz_lead_temperature || '';
			const color = temp === 'Hot' ? 'red' : temp === 'Warm' ? 'orange' : 'blue';
			return `<span class="indicator-pill ${color} filterable ellipsis" title="New Vobiz Hit">
				New Vobiz Hit (${count})
			</span>`;
		},
		vobiz_lead_temperature(value) {
			if (!value) return '';
			const color = value === 'Hot' ? 'red' : value === 'Warm' ? 'orange' : 'blue';
			return `<span class="indicator-pill ${color} filterable ellipsis">${frappe.utils.escape_html(value)}</span>`;
		},
	};

	settings.onload = function(listview) {
		if (previous_onload) {
			previous_onload(listview);
		}
	};

	frappe.listview_settings['Patient'] = settings;
})();
