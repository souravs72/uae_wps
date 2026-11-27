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
		if self.status != previous_status:
			frappe.throw(_("Status changes when salary slips are fetched, not by editing the field."))
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
