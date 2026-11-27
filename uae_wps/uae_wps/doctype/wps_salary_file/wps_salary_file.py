# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate

from uae_wps.payroll import LOCKED_STATUSES


class WPSSalaryFile(Document):
	"""One HRMS Payroll Entry prepared for a WPS file.

	Employee rows keep a copy of the slip. This document does not recalculate pay.
	"""

	def onload(self):
		from uae_wps.payroll import load_component_rows

		load_component_rows(self)
		self.set_payment_status()

	def validate(self):
		if self.flags.get("wps_fetching"):
			self.set_payment_status()
			return
		if self.flags.get("wps_bank_response"):
			self._validate_bank_response_update()
			self.set_payment_status()
			return
		if self.is_new():
			self.status = "Draft"
			self.set_payment_status()
			return

		previous_status = frappe.db.get_value(self.doctype, self.name, "status")
		if previous_status in LOCKED_STATUSES and not self.flags.get("wps_mark_sent"):
			frappe.throw(_("This WPS Salary File is {0} and cannot be changed.").format(previous_status))
		if self.flags.get("wps_generating"):
			if self.status != "Generated":
				frappe.throw(_("Generating the SIF file sets the status to Generated."))
			self.set_payment_status()
			return
		if self.flags.get("wps_mark_sent"):
			if self.status != "Sent to Bank":
				frappe.throw(_("Marking the file as sent sets the status to Sent to Bank."))
			self.set_payment_status()
			return
		if self.status != previous_status:
			frappe.throw(
				_("Status changes when salary slips are fetched, the SIF file is generated, or a bank response is recorded.")
			)
		self._reject_foreign_employee_rows()
		self.set_payment_status()

	def _validate_bank_response_update(self) -> None:
		"""Only bank-response fields may change on a submitted salary file."""
		if self.status not in {"Accepted", "Rejected", "Sent to Bank"}:
			frappe.throw(
				_("A bank response can only set status to Accepted, Rejected, or Sent to Bank.")
			)
		allowed = {
			"status",
			"bank_response",
			"ack_file_name",
			"accepted_on",
			"payment_status",
			"modified",
			"modified_by",
		}
		db_doc = frappe.get_doc(self.doctype, self.name)
		for field in self.meta.get("fields") or []:
			if field.fieldtype in frappe.model.table_fields:
				continue
			if field.fieldname in allowed:
				continue
			if self.get(field.fieldname) != db_doc.get(field.fieldname):
				frappe.throw(
					_("Bank response updates cannot change {0}.").format(_(field.label or field.fieldname))
				)

	def before_save(self):
		if self.is_new():
			self.flags.previous_employee_names = []
			return
		self.flags.previous_employee_names = frappe.get_all(
			"WPS Salary File Employee",
			filters={"parent": self.name, "parenttype": self.doctype},
			pluck="name",
		)

	def on_update(self):
		from uae_wps.payroll import delete_component_rows

		current = {row.name for row in (self.employees or []) if row.name}
		removed = [name for name in (self.flags.previous_employee_names or []) if name not in current]
		delete_component_rows(removed)

	def before_submit(self):
		if not self.sif_file:
			frappe.throw(_("Generate the SIF file before submitting this WPS Salary File."))

	def on_trash(self):
		from uae_wps.payroll import delete_component_rows

		names = frappe.get_all(
			"WPS Salary File Employee",
			filters={"parent": self.name, "parenttype": self.doctype},
			pluck="name",
		)
		delete_component_rows(names)

	def set_payment_status(self) -> None:
		"""Accepted on or before the due date is paid. Everything else is unpaid or late."""
		if not self.due_date:
			self.payment_status = ""
			return
		due = getdate(self.due_date)
		if self.status == "Accepted" and self.accepted_on:
			accepted = getdate(self.accepted_on)
			self.payment_status = "Paid on time" if accepted <= due else "Paid late"
			return
		if self.status == "Accepted":
			self.payment_status = "Paid"
			return
		today = getdate()
		if today <= due:
			self.payment_status = "Unpaid"
			return
		day2 = add_days(due, 1)
		day5 = add_days(due, 4)
		day11 = add_days(due, 10)
		if today < day5:
			self.payment_status = _("Unpaid. Alerts from {0}.").format(day2)
		elif today < day11:
			self.payment_status = _("Unpaid. Work permits can stop from {0}.").format(day5)
		else:
			self.payment_status = _("Unpaid. Fine and reclassification risk from {0}.").format(day11)

	@frappe.whitelist(methods=["POST"])
	def fetch_salary_slips(self) -> dict:
		self.check_permission("write")
		if self.is_new():
			frappe.throw(_("Save the WPS Salary File before fetching salary slips."))
		# The posted document can carry another file's child names and a forged status.
		self.load_from_db()
		self.check_permission("write")
		from uae_wps.payroll import populate_from_payroll, write_component_rows

		result = populate_from_payroll(self)
		self.flags.wps_fetching = True
		self.save()
		self.flags.wps_fetching = False
		write_component_rows(self)
		return result

	@frappe.whitelist(methods=["POST"])
	def generate_sif(self) -> dict:
		self.check_permission("write")
		self.check_permission("submit")
		if self.is_new():
			frappe.throw(_("Save the WPS Salary File before generating the SIF file."))
		self.load_from_db()
		self.check_permission("write")
		self.check_permission("submit")
		if self.status in LOCKED_STATUSES:
			frappe.throw(
				_("This WPS Salary File is {0} and its SIF file cannot be generated again.").format(self.status)
			)
		from uae_wps.payroll import load_component_rows
		from uae_wps.sif import generate_sif_content

		load_component_rows(self)
		content, filename, created = generate_sif_content(self)
		file_doc = attach_generated_file(self, filename, content, "sif_file")
		self.status = "Generated"
		self.sif_file = file_doc.file_url
		self.file_name = filename
		self.file_hash = frappe.utils.sha256_hash(content)
		self.generated_at = created
		self.generated_by = frappe.session.user
		self.flags.wps_generating = True
		try:
			self.submit()
		except Exception:
			frappe.delete_doc("File", file_doc.name, ignore_permissions=True, force=True)
			raise
		finally:
			self.flags.wps_generating = False
		return {
			"file_name": file_doc.file_name,
			"file_url": file_doc.file_url,
			"file_hash": self.file_hash,
			"status": self.status,
		}

	@frappe.whitelist(methods=["POST"])
	def mark_as_sent_to_bank(self) -> dict:
		self.check_permission("write")
		if self.is_new():
			frappe.throw(_("Save the WPS Salary File first."))
		self.load_from_db()
		self.check_permission("write")
		if self.docstatus != 1 or self.status != "Generated":
			frappe.throw(_("Mark as sent only after the SIF file is generated."))
		self.status = "Sent to Bank"
		self.flags.wps_mark_sent = True
		self.save()
		self.flags.wps_mark_sent = False
		return {"status": self.status}

	def _reject_foreign_employee_rows(self) -> None:
		names = [row.name for row in (self.employees or []) if row.name and not row.is_new()]
		if not names:
			return
		owned = set(
			frappe.get_all(
				"WPS Salary File Employee",
				filters={"name": ["in", names], "parent": self.name, "parenttype": self.doctype},
				pluck="name",
			)
		)
		if any(name not in owned for name in names):
			frappe.throw(_("An employee row on this file belongs to another WPS Salary File."))


def attach_generated_file(doc, filename: str, content: bytes, fieldname: str):
	"""Store a generated bank file under its mandated name.

	Frappe checks upload extensions only while a web request is active. This file is
	produced by the app, so that check is skipped for the insert and then restored.
	"""
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": fieldname,
			"is_private": 1,
			"content": content,
		}
	)
	file_doc.flags.ignore_permissions = True
	request = getattr(frappe.local, "request", None)
	try:
		frappe.local.request = None
		file_doc.insert()
	finally:
		frappe.local.request = request
	if file_doc.file_name != filename:
		frappe.delete_doc("File", file_doc.name, ignore_permissions=True, force=True)
		frappe.throw(
			_("A file named {0} already exists. Wait a second and generate it again.").format(filename)
		)
	return file_doc
