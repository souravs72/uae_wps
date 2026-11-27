# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Write the Refund Request File an employer's bank forwards to WPS."""

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from uae_wps.sif import _employer_id, _join, _money


def generate_rfr_content(doc) -> tuple[bytes, str, datetime]:
	"""Return RFR bytes and a file name based on the employer id and creation time."""
	errors: list[str] = []
	company = frappe.db.get_value(
		"Company",
		doc.company,
		["wps_enabled", "mohre_establishment_id"],
		as_dict=True,
	)
	if not company or not company.wps_enabled:
		errors.append(_("Enable WPS on {0} before generating the RFR file.").format(doc.company))

	employer_id = _employer_id(company.mohre_establishment_id if company else None, errors)
	bank_routing = frappe.db.get_value("WPS Agent", doc.wps_agent, "routing_code") if doc.wps_agent else ""
	if len(str(bank_routing or "")) != 9 or not str(bank_routing).isdigit() or set(str(bank_routing)) == {"0"}:
		errors.append(_("WPS Agent {0} needs a 9 digit routing code.").format(doc.wps_agent or _("blank")))
		bank_routing = ""

	sif_name = (doc.sif_file_name or "").strip()
	ack_name = (doc.ack_file_name or "").strip()
	if not sif_name:
		errors.append(_("The refund request has no SIF file name."))
	if not ack_name:
		errors.append(_("The refund request has no ACK file name."))
	employer_account = (doc.employer_account_no or "").strip()
	if not employer_account:
		errors.append(_("Set the employer bank account on the WPS Salary File before generating a refund."))

	employees = list(doc.employees or [])
	if not employees:
		errors.append(_("Add employees before generating the RFR file."))

	created = now_datetime()
	if not isinstance(created, datetime):
		created = datetime.fromisoformat(str(created))

	fdr_rows: list[str] = []
	total = 0.0
	for row in employees:
		label = row.employee_name or row.employee or _("an employee")
		amount = flt(row.refund_amount, 2)
		if amount <= 0:
			errors.append(_("{0}: refund amount must be greater than zero.").format(label))
			continue
		if flt(amount, 2) > flt(row.max_refund_amount, 2):
			errors.append(
				_("{0}: refund amount {1} is above the SIF credit {2}.").format(
					label, amount, row.max_refund_amount
				)
			)
			continue
		fields = [
			"FDR",
			sif_name,
			ack_name,
			str(row.agent_routing_code or ""),
			str(row.mohre_person_id or ""),
			_money(amount, 2),
			str(row.refund_reason_code or ""),
			str(row.employee_account or ""),
			" ",
			"EWPMS",
		]
		line = _join(fields, label, errors)
		if line:
			fdr_rows.append(line)
			total += amount

	if errors:
		frappe.throw(errors, as_list=True)

	fcr = [
		"FCR",
		employer_id,
		_reference(doc.name),
		str(bank_routing),
		employer_account,
		_money(flt(total, 2), 2),
		str(len(fdr_rows)),
		" ",
		" ",
		"EWPMS",
	]
	body = list(fdr_rows)
	body.append(_join(fcr, _("the refund control record"), errors))
	if errors:
		frappe.throw(errors, as_list=True)
	content = ("\r\n".join(body) + "\r\n").encode("ascii")
	filename = f"{employer_id}{created.strftime('%y%m%d%H%M%S')}.RFR"
	return content, filename, created


def _reference(name: str) -> str:
	cleaned = "".join(ch for ch in str(name or "") if ch.isascii() and ch.isalnum())
	return cleaned[:35]
