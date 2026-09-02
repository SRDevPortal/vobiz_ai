frappe.ui.form.on('CRM Lead', {
	refresh(frm) {
		mark_vobiz_calls_read(frm);
		render_crm_lead_vobiz_calling(frm);
	},
});

function mark_vobiz_calls_read(frm) {
	if (frm.is_new()) return;
	frappe.call({
		method: 'vobiz_ai.api.call_log.mark_lead_calls_read',
		args: { lead_name: frm.doc.name },
		callback() {
			frm.doc.vobiz_call_alert_count = 0;
		},
	});
}

function render_vobiz_calls(frm, doctype) {
	if (frm.is_new() || !frm.fields_dict.vobiz_ai_calls_html) return;
	frappe.call({
		method: 'vobiz_ai.api.processing.get_related_calls',
		args: { doctype, name: frm.doc.name },
		callback(r) {
			const rows = r.message || [];
			const latest = rows[0] || {};
			const latest_time = latest.start_time ? frappe.datetime.str_to_user(latest.start_time) : 'No calls yet';
			const transcript = latest.transcription_text || latest.transcript_text || '';
			const summary = latest.ai_summary || '';
			const latest_recording = audio_player(latest.recording_url);
			const body = rows.length ? rows.map(row => {
				const row_transcript = row.transcription_text || row.transcript_text || '';
				return `<tr>
					<td><a href="/app/vobiz-call-log/${encodeURIComponent(row.name)}">${frappe.datetime.str_to_user(row.start_time || '')}</a></td>
					<td>${frappe.utils.escape_html(row.status || '')}</td>
					<td>${frappe.utils.escape_html(row.direction || '')}</td>
					<td>${frappe.utils.escape_html(row.customer_number || '')}</td>
					<td>${audio_player(row.recording_url)}</td>
					<td class="vobiz-transcript-cell">${frappe.utils.escape_html(short_text(row_transcript || row.ai_summary || '', 180))}</td>
				</tr>`;
			}).join('') : '<tr><td colspan="6" class="text-muted">No Vobiz calls found.</td></tr>';
			frm.fields_dict.vobiz_ai_calls_html.$wrapper.html(`
				<div class="vobiz-calling-panel">
					<div class="vobiz-calling-head">
						<div>
							<div class="vobiz-calling-title">Vobiz Calling</div>
							<div class="text-muted">Last Call Time: ${frappe.utils.escape_html(latest_time)}</div>
						</div>
						${latest.name ? `<a class="btn btn-xs btn-default" href="/app/vobiz-call-log/${encodeURIComponent(latest.name)}">Open Call Log</a>` : ''}
					</div>
					<div class="vobiz-latest-grid">
						<div>
							<div class="vobiz-label">Recording</div>
							${latest_recording || '<span class="text-muted">No recording available</span>'}
						</div>
						<div>
							<div class="vobiz-label">AI Transcription</div>
							<div class="vobiz-transcript-box">${frappe.utils.escape_html(transcript || summary || 'No transcription available')}</div>
						</div>
					</div>
				</div>
				<div class="table-responsive">
					<table class="table table-bordered table-sm">
						<thead><tr>
							<th>Time</th><th>Status</th><th>Direction</th><th>Customer</th><th>Recording</th><th>AI Transcription</th>
						</tr></thead>
						<tbody>${body}</tbody>
					</table>
				</div>
				<style>
					.vobiz-calling-panel {
						border: 1px solid var(--border-color);
						border-radius: 6px;
						padding: 12px;
						margin-bottom: 12px;
						background: var(--fg-color);
					}
					.vobiz-calling-head {
						display: flex;
						align-items: center;
						justify-content: space-between;
						gap: 12px;
						margin-bottom: 12px;
					}
					.vobiz-calling-title {
						font-weight: 600;
						font-size: 14px;
					}
					.vobiz-latest-grid {
						display: grid;
						grid-template-columns: minmax(240px, 360px) minmax(260px, 1fr);
						gap: 14px;
					}
					.vobiz-label {
						font-size: 12px;
						font-weight: 600;
						margin-bottom: 6px;
						color: var(--text-muted);
					}
					.vobiz-audio {
						width: 100%;
						height: 36px;
					}
					.vobiz-transcript-box {
						max-height: 150px;
						overflow: auto;
						white-space: pre-wrap;
						border: 1px solid var(--border-color);
						border-radius: 6px;
						padding: 8px;
						background: var(--control-bg);
					}
					.vobiz-transcript-cell {
						max-width: 420px;
						white-space: normal;
					}
					@media (max-width: 768px) {
						.vobiz-latest-grid {
							grid-template-columns: 1fr;
						}
					}
				</style>
			`);
		},
	});
}

function render_crm_lead_vobiz_calling(frm) {
	if (frm.is_new()) return;
	const target = frm.fields_dict.vobiz_calling_details_html;
	if (!target) return;

	frappe.call({
		method: 'vobiz_ai.api.processing.get_related_calls',
		args: { doctype: 'CRM Lead', name: frm.doc.name },
		callback(r) {
			const rows = r.message || [];
			const latest = rows[0] || {};
			const latest_time = latest.start_time ? frappe.datetime.str_to_user(latest.start_time) : 'No calls yet';
			const transcript = latest.transcription_text || latest.transcript_text || '';
			const summary = latest.ai_summary || '';
			target.$wrapper.html(`
				<div class="vobiz-main-calling-box">
					<div class="vobiz-main-calling-head">
						<div>
							<div class="vobiz-main-title">Latest Call Details</div>
							<div class="text-muted">Last Call Time: ${frappe.utils.escape_html(latest_time)}</div>
						</div>
						${latest.name ? `<a class="btn btn-xs btn-default" href="/app/vobiz-call-log/${encodeURIComponent(latest.name)}">Open Call Log</a>` : ''}
					</div>
					<div class="vobiz-main-grid">
						<div>
							<div class="vobiz-label">Recording</div>
							${audio_player(latest.recording_url) || '<span class="text-muted">No recording available</span>'}
						</div>
						<div>
							<div class="vobiz-label">AI Transcription</div>
							<div class="vobiz-transcript-box">${frappe.utils.escape_html(transcript || summary || 'No transcription available')}</div>
						</div>
					</div>
				</div>
				<style>
					.vobiz-main-calling-box {
						border: 1px solid var(--border-color);
						border-radius: 6px;
						padding: 12px;
						margin: 8px 0 12px;
						background: var(--fg-color);
					}
					.vobiz-main-calling-head {
						display: flex;
						align-items: center;
						justify-content: space-between;
						gap: 12px;
						margin-bottom: 12px;
					}
					.vobiz-main-title {
						font-weight: 600;
					}
					.vobiz-main-grid {
						display: grid;
						grid-template-columns: minmax(240px, 360px) minmax(260px, 1fr);
						gap: 14px;
					}
					.vobiz-label {
						font-size: 12px;
						font-weight: 600;
						margin-bottom: 6px;
						color: var(--text-muted);
					}
					.vobiz-audio {
						width: 100%;
						height: 36px;
					}
					.vobiz-transcript-box {
						max-height: 150px;
						overflow: auto;
						white-space: pre-wrap;
						border: 1px solid var(--border-color);
						border-radius: 6px;
						padding: 8px;
						background: var(--control-bg);
					}
					@media (max-width: 768px) {
						.vobiz-main-grid {
							grid-template-columns: 1fr;
						}
					}
				</style>
			`);
		},
	});
}

function audio_player(url) {
	if (!url) return '';
	const safe_url = frappe.utils.escape_html(url);
	return `<audio class="vobiz-audio" controls preload="none" src="${safe_url}"></audio>`;
}

function short_text(text, limit) {
	if (!text) return '';
	return text.length > limit ? `${text.slice(0, limit)}...` : text;
}
