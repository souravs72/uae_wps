# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class WPSBankResponse(Document):
	"""ACK or NAK returned by the employer's bank for a submitted SIF."""

	def validate(self):
		if not self.wps_salary_file:
			frappe.throw(_("Select a WPS Salary File."))
		salary_file = frappe.get_doc("WPS Salary File", self.wps_salary_file)
		salary_file.check_permission("write")
		if salary_file.docstatus != 1:
			frappe.throw(
				_("WPS Salary File {0} must be submitted before a bank response is recorded.").format(
					salary_file.name
				)
			)
		if salary_file.status not in {"Generated", "Sent to Bank"}:
			frappe.throw(
				_(
					"WPS Salary File {0} is {1}. Record a bank response only while it is Generated or Sent to Bank."
				).format(salary_file.name, salary_file.status)
			)
		self.company = salary_file.company
		if self.response == "ACK" and not (self.ack_file_name or "").strip():
			frappe.throw(_("Set the ACK File Name. A later refund file needs that name."))
		if self.response == "NAK":
			self.ack_file_name = ""

		self._validate_received_on(salary_file)
		self._reject_duplicate_response()

	def before_submit(self):
		self._lock_salary_file()
		salary_file = frappe.get_doc("WPS Salary File", self.wps_salary_file)
		salary_file.check_permission("write")
		if salary_file.status not in {"Generated", "Sent to Bank"}:
			frappe.throw(
				_(
					"WPS Salary File {0} is {1}. Record a bank response only while it is Generated or Sent to Bank."
				).format(salary_file.name, salary_file.status)
			)
		self._reject_duplicate_response()

	def on_submit(self):
		status = "Accepted" if self.response == "ACK" else "Rejected"
		salary_file = frappe.get_doc("WPS Salary File", self.wps_salary_file)
		salary_file.check_permission("write")
		salary_file.status = status
		salary_file.bank_response = self.name
		salary_file.ack_file_name = self.ack_file_name if self.response == "ACK" else ""
		salary_file.accepted_on = self.received_on if self.response == "ACK" else None
		salary_file.flags.wps_bank_response = True
		salary_file.set_payment_status()
		salary_file.save(ignore_permissions=True)
		salary_file.flags.wps_bank_response = False

	def on_cancel(self):
		salary_file = frappe.get_doc("WPS Salary File", self.wps_salary_file)
		if salary_file.bank_response != self.name:
			return
		salary_file.check_permission("write")
		if frappe.db.exists(
			"WPS Refund Request",
			{"wps_salary_file": self.wps_salary_file, "docstatus": 1},
		):
			frappe.throw(
				_(
					"Cancel WPS Refund Request documents for {0} before cancelling this bank response."
				).format(self.wps_salary_file)
			)
		salary_file.status = "Sent to Bank"
		salary_file.bank_response = None
		salary_file.ack_file_name = None
		salary_file.accepted_on = None
		salary_file.flags.wps_bank_response = True
		salary_file.set_payment_status()
		salary_file.save(ignore_permissions=True)
		salary_file.flags.wps_bank_response = False

	def _validate_received_on(self, salary_file) -> None:
		if not self.received_on:
			return
		received = getdate(self.received_on)
		today = getdate()
		if received > today:
			frappe.throw(_("Received On cannot be after today."))
		if self.response != "ACK" or not salary_file.generated_at:
			return
		generated = getdate(salary_file.generated_at)
		if received < generated:
			frappe.throw(
				_("Received On cannot be before the SIF was generated on {0}.").format(generated)
			)

	def _reject_duplicate_response(self) -> None:
		existing = frappe.db.exists(
			"WPS Bank Response",
			{
				"wps_salary_file": self.wps_salary_file,
				"docstatus": 1,
				"name": ["!=", self.name or ""],
			},
		)
		if existing:
			frappe.throw(
				_("WPS Salary File {0} already has bank response {1}.").format(
					self.wps_salary_file, existing
				)
			)

	def _lock_salary_file(self) -> None:
		frappe.db.sql(
			"select name from `tabWPS Salary File` where name = %s for update",
			self.wps_salary_file,
		)
