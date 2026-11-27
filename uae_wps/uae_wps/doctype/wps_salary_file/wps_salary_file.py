# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from uae_wps.payroll import LOCKED_STATUSES


class WPSSalaryFile(Document):
	"""One HRMS Payroll Entry prepared for a WPS file.

	Employee rows keep a copy of the slip. This document does not recalculate pay.
	"""

	def onload(self):
		from uae_wps.payroll import load_component_rows

		load_component_rows(self)

	def validate(self):
		if self.flags.get("wps_fetching"):
			return
		if self.is_new():
			self.status = "Draft"
			return

		previous_status = frappe.db.get_value(self.doctype, self.name, "status")
		if previous_status in LOCKED_STATUSES:
			frappe.throw(_("This WPS Salary File is {0} and cannot be changed.").format(previous_status))
		if self.flags.get("wps_generating"):
			if self.status != "Generated":
				frappe.throw(_("Generating the SIF file sets the status to Generated."))
			return
		if self.status != previous_status:
			frappe.throw(
				_("Status changes when salary slips are fetched or the SIF file is generated.")
			)
		self._reject_foreign_employee_rows()

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
		file_doc = _attach_sif(self, filename, content)
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


def _attach_sif(doc, filename: str, content: bytes):
	"""Store the SIF under its mandated name.

	Frappe checks upload extensions only while a web request is active. This file is
	produced by the app, so that check is skipped for the insert and then restored.
	"""
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": "sif_file",
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
			_("A SIF file named {0} already exists. Wait a second and generate it again.").format(filename)
		)
	return file_doc
