// Copyright (c) 2026, Sourav Singh and contributors
// For license information, please see license.txt

const LOCKED_STATUSES = ["Generated", "Downloaded", "Submitted", "Accepted", "Rejected"];

frappe.ui.form.on("WPS Salary File", {
	refresh(frm) {
		if (frm.doc.validation_summary) {
			frm.set_intro(
				frappe.utils.escape_html(frm.doc.validation_summary).replaceAll("\n", "<br>"),
				"yellow"
			);
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
