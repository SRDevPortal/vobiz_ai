(() => {
	const settings = frappe.listview_settings["CRM Lead"] || {};
	const previous_onload = settings.onload;

	settings.add_fields = Array.from(new Set([
		...(settings.add_fields || []),
		"vobiz_call_alert_count",
	]));

	settings.onload = function(listview) {
		if (previous_onload) {
			previous_onload(listview);
		}
		install_vobiz_call_badges(listview);
	};

	frappe.listview_settings["CRM Lead"] = settings;

	function install_vobiz_call_badges(listview) {
		if (listview.__vobiz_call_badges_installed) return;
		listview.__vobiz_call_badges_installed = true;

		const previous_refresh = listview.refresh;
		listview.refresh = function() {
			const result = previous_refresh.apply(listview, arguments);
			setTimeout(() => render_vobiz_call_badges(listview), 150);
			return result;
		};

		setTimeout(() => render_vobiz_call_badges(listview), 250);
	}

	function render_vobiz_call_badges(listview) {
		const docs = (listview.data || []).filter((row) => row && row.name);
		if (!docs.length || !listview.$result) return;

		listview.$result.find(".vobiz-call-count").addClass("hidden").text("0");

		frappe.call({
			method: "vobiz_ai.api.call_log.get_lead_call_badges",
			args: { lead_names: docs.map((row) => row.name) },
			callback: (response) => {
				const badges = response.message || {};
				docs.forEach((doc) => add_or_update_button(listview, doc, badges[doc.name] || {}));
			},
		});
	}

	function add_or_update_button(listview, doc, badge) {
		const name = doc.name;
		const $open_chat = listview.$result.find(`.btn-action[data-name="${escape_selector(name)}"]`).first();
		if (!$open_chat.length) return;

		let $button = $open_chat.siblings(`.vobiz-call-list-btn[data-name="${escape_selector(name)}"]`).first();
		if (!$button.length) {
			$button = $(`
				<button type="button" class="btn btn-xs btn-default vobiz-call-list-btn" data-name="">
					<i class="fa fa-phone"></i>
					<span class="vobiz-call-count hidden">0</span>
				</button>
			`);
			$button.attr("data-name", name);
			$button.on("click", (event) => {
				event.preventDefault();
				event.stopPropagation();
				open_vobiz_calls(name);
			});
			$open_chat.after($button);
		}

		const has_server_count = Object.prototype.hasOwnProperty.call(badge, "notification_count");
		const count = cint(has_server_count ? badge.notification_count : 0);
		const latest = badge.latest_query_time || doc.vobiz_latest_query_time || "";
		$button.attr("title", latest
			? __("Unread Vobiz calls: {0}. Latest: {1}", [count, frappe.datetime.str_to_user(latest)])
			: __("Unread Vobiz calls: {0}", [count])
		);
		const $count = $button.find(".vobiz-call-count");
		if (count > 0) {
			$count.removeClass("hidden").text(count > 99 ? "99+" : String(count));
		} else {
			$count.addClass("hidden").text("0");
		}
	}

	function open_vobiz_calls(lead_name) {
		frappe.call({
			method: "vobiz_ai.api.call_log.mark_lead_calls_read",
			args: { lead_name },
			callback: () => {
				frappe.route_options = { crm_lead: lead_name };
				frappe.set_route("List", "Vobiz Call Log");
			},
		});
	}

	function escape_selector(value) {
		if (window.CSS && CSS.escape) {
			return CSS.escape(value);
		}
		return String(value).replace(/(["\\])/g, "\\$1");
	}

	const style = document.createElement("style");
	style.textContent = `
		.vobiz-call-list-btn {
			position: relative;
			min-width: 30px;
			height: 26px;
			margin-left: 6px;
			padding: 3px 8px;
			border-radius: 6px;
		}
		.vobiz-call-list-btn .fa {
			font-size: 12px;
		}
		.vobiz-call-count {
			position: absolute;
			top: -7px;
			right: -7px;
			min-width: 17px;
			height: 17px;
			padding: 0 4px;
			border-radius: 9px;
			background: #f04438;
			color: #fff;
			font-size: 10px;
			font-weight: 700;
			line-height: 17px;
			text-align: center;
			box-shadow: 0 0 0 2px #fff;
		}
	`;
	document.head.appendChild(style);
})();
