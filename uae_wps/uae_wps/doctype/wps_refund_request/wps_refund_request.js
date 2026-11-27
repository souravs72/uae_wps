// Copyright (c) 2026, Sourav Singh and contributors
// For license information, please see license.txt

frappe.ui.form.on("WPS Refund Request", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus > 0 || frm.doc.status === "Generated") return;
		frm.add_custom_button(__("Generate RFR File"), () => {
			const generate = () => {
				frm.call({
					doc: frm.doc,
					method: "generate_rfr",
					freeze: true,
					freeze_message: __("Generating the RFR file"),
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
