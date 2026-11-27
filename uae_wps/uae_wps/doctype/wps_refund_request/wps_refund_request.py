# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class WPSRefundRequest(Document):
	"""Employer RFR file against an Accepted WPS Salary File."""

	def validate(self):
		if self.is_new():
			self.status = "Draft"
		self._load_from_salary_file()
		self._validate_employees()
		self.total_refund = flt(sum(flt(row.refund_amount) for row in self.employees or []), 2)

	def before_submit(self):
		if not self.rfr_file:
			frappe.throw(_("Generate the RFR file before submitting this refund request."))
		self._lock_salary_file()
		self._load_from_salary_file()
		self._validate_employees()
		self.total_refund = flt(sum(flt(row.refund_amount) for row in self.employees or []), 2)

	def _lock_salary_file(self) -> None:
		"""Serialize refunds against one SIF so concurrent submits cannot both clear the cap."""
		if not self.wps_salary_file:
			return
		frappe.db.sql(
			"select name from `tabWPS Salary File` where name = %s for update",
			self.wps_salary_file,
		)

	def _load_from_salary_file(self) -> None:
		if not self.wps_salary_file:
			frappe.throw(_("Select a WPS Salary File."))
		salary_file = frappe.get_doc("WPS Salary File", self.wps_salary_file)
		salary_file.check_permission("read")
		if salary_file.docstatus != 1 or salary_file.status != "Accepted":
			frappe.throw(
				_("WPS Salary File {0} must be Accepted before a refund can be requested.").format(
					salary_file.name
				)
			)
		if not salary_file.ack_file_name:
			frappe.throw(_("WPS Salary File {0} has no ACK file name.").format(salary_file.name))
		if not salary_file.file_name:
			frappe.throw(_("WPS Salary File {0} has no SIF file name.").format(salary_file.name))

		self.company = salary_file.company
		self.currency = salary_file.currency
		self.sif_file_name = salary_file.file_name
		self.ack_file_name = salary_file.ack_file_name
		self.employer_bank_account = salary_file.employer_bank_account
		self.wps_agent = salary_file.wps_agent
		self.wps_file_format = salary_file.wps_file_format
		self.employer_account_no = _employer_account_no(salary_file.employer_bank_account)
		self.flags.salary_file_doc = salary_file

	def _validate_employees(self) -> None:
		if not self.employees:
			frappe.throw(_("Add at least one employee to refund."))
		salary_file = self.flags.get("salary_file_doc") or frappe.get_doc(
			"WPS Salary File", self.wps_salary_file
		)
		salary_file.check_permission("read")
		salary_rows = {row.employee: row for row in (salary_file.employees or [])}
		if not self.wps_file_format:
			frappe.throw(_("WPS Salary File {0} has no file format.").format(self.wps_salary_file))
		if not frappe.has_permission("WPS File Format", "read", self.wps_file_format):
			frappe.throw(
				_("You need permission to read WPS File Format {0}.").format(self.wps_file_format),
				frappe.PermissionError,
			)
		fmt = frappe.get_doc("WPS File Format", self.wps_file_format)
		codes = {row.code for row in (fmt.refund_reason_codes or [])}
		prior = _prior_refunds(self.wps_salary_file, self.name)
		for row in self.employees:
			source = salary_rows.get(row.employee)
			if not source:
				frappe.throw(_("{0} is not on WPS Salary File {1}.").format(row.employee, self.wps_salary_file))
			account = source.employee_iban or source.employee_account_no
			row.mohre_person_id = source.mohre_person_id
			row.agent_routing_code = source.agent_routing_code
			row.employee_account = account
			sif_credit = flt(flt(source.fixed_amount) + flt(source.variable_amount), 2)
			already = flt(prior.get(row.employee), 2)
			row.max_refund_amount = flt(sif_credit - already, 2)
			if flt(row.refund_amount) <= 0:
				frappe.throw(
					_("{0}: refund amount must be greater than zero.").format(row.employee_name or row.employee)
				)
			if flt(row.refund_amount, 2) > flt(row.max_refund_amount, 2):
				frappe.throw(
					_("{0}: refund amount {1} is above the remaining SIF credit {2}.").format(
						row.employee_name or row.employee, row.refund_amount, row.max_refund_amount
					)
				)
			if row.refund_reason_code not in codes:
				frappe.throw(
					_("{0}: refund reason code {1} is not on WPS File Format {2}.").format(
						row.employee_name or row.employee, row.refund_reason_code, self.wps_file_format
					)
				)

	@frappe.whitelist(methods=["POST"])
	def generate_rfr(self) -> dict:
		self.check_permission("write")
		self.check_permission("submit")
		if self.is_new():
			frappe.throw(_("Save the refund request before generating the RFR file."))
		self.load_from_db()
		self.check_permission("write")
		self.check_permission("submit")
		if self.status == "Generated" or self.docstatus == 1:
			frappe.throw(_("This refund request already has an RFR file."))
		self._load_from_salary_file()
		self._validate_employees()
		from uae_wps.rfr import generate_rfr_content
		from uae_wps.uae_wps.doctype.wps_salary_file.wps_salary_file import attach_generated_file

		content, filename, created = generate_rfr_content(self)
		file_doc = attach_generated_file(self, filename, content, "rfr_file")
		self.status = "Generated"
		self.rfr_file = file_doc.file_url
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


def _prior_refunds(wps_salary_file: str, exclude_name: str | None) -> dict[str, float]:
	"""Sum refund amounts already submitted for this salary file, by employee.

	Uses unrestricted reads after the caller has verified permission on the salary file.
	Totals must include every submitted refund against that SIF, not only rows the
	current user can list, or the remaining credit would be overstated.
	"""
	filters = {"wps_salary_file": wps_salary_file, "docstatus": 1}
	names = frappe.get_all("WPS Refund Request", filters=filters, pluck="name")
	if exclude_name:
		names = [name for name in names if name != exclude_name]
	if not names:
		return {}
	rows = frappe.get_all(
		"WPS Refund Request Employee",
		filters={"parent": ["in", names], "parenttype": "WPS Refund Request"},
		fields=["employee", "refund_amount"],
	)
	totals: dict[str, float] = {}
	for row in rows:
		totals[row.employee] = flt(totals.get(row.employee), 2) + flt(row.refund_amount, 2)
	return totals


def _employer_account_no(bank_account: str | None) -> str:
	if not bank_account:
		return ""
	row = frappe.db.get_value("Bank Account", bank_account, ["iban", "bank_account_no"], as_dict=True)
	if not row:
		return ""
	return (row.iban or row.bank_account_no or "").strip()
