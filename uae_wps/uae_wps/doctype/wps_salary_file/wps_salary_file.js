// Copyright (c) 2026, Sourav Singh and contributors
// For license information, please see license.txt

const LOCKED_STATUSES = ["Generated", "Sent to Bank", "Accepted", "Rejected"];

frappe.ui.form.on("WPS Salary File", {
	refresh(frm) {
		frm.clear_custom_buttons();
		const intro = [];
		if (frm.doc.due_date) {
			intro.push(__("Due date {0}.", [frappe.datetime.str_to_user(frm.doc.due_date)]));
		}
		if (frm.doc.percent_of_wages_due || frm.doc.percent_of_wages_due === 0) {
			intro.push(__("{0}% of wages due is in this file.", [frm.doc.percent_of_wages_due]));
		}
		if (frm.doc.payment_status) {
			intro.push(frm.doc.payment_status);
		}
		const omissions = frm.doc.omissions || [];
		if (omissions.length) {
			const shown = omissions.slice(0, 10);
			for (const row of shown) {
				intro.push(
					__("{0}: {1}", [
						frappe.utils.escape_html(row.employee_name || row.employee || ""),
						frappe.utils.escape_html(row.reason || ""),
					])
				);
			}
			if (omissions.length > 10) {
				intro.push(__("and {0} more omitted.", [omissions.length - 10]));
			}
		}
		if (frm.doc.validation_summary) {
			intro.push(frappe.utils.escape_html(frm.doc.validation_summary).replaceAll("\n", "<br>"));
		}
		if (intro.length) {
			frm.set_intro(intro.join("<br>"), "yellow");
		}

		if (frm.doc.docstatus === 1 && ["Generated", "Sent to Bank"].includes(frm.doc.status)) {
			if (frm.doc.status === "Generated") {
				frm.add_custom_button(__("Mark as Sent to Bank"), () => {
					frm.call({
						doc: frm.doc,
						method: "mark_as_sent_to_bank",
						freeze: true,
					}).then(() => frm.reload_doc());
				});
			}
			frm.add_custom_button(__("Record Bank Response"), () => {
				frappe.new_doc("WPS Bank Response", {
					wps_salary_file: frm.doc.name,
					company: frm.doc.company,
				});
			});
			return;
		}

		if (frm.is_new() || frm.doc.docstatus > 0 || LOCKED_STATUSES.includes(frm.doc.status)) return;

		frm.add_custom_button(__("Fetch Salary Slips"), () => {
			frm.call({
				doc: frm.doc,
				method: "fetch_salary_slips",
				freeze: true,
				freeze_message: __("Fetching Salary Slips"),
			}).then(() => frm.reload_doc());
		});
		frm.add_custom_button(__("Generate SIF File"), () => {
			const generate = () => {
				frm.call({
					doc: frm.doc,
					method: "generate_sif",
					freeze: true,
					freeze_message: __("Generating the SIF file"),
				}).then(() => frm.reload_doc());
			};
			if (frm.is_dirty()) {
				frm.save().then(generate);
				return;
			}
			generate();
		}).addClass("btn-primary");
	},
});
