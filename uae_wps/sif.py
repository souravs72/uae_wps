# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Write the Salary Information File a UAE WPS agent accepts.

The layout is the Ministry of Labour / Central Bank SIF used by the agent banks:
ASCII, comma-separated, no header, each record terminated by CRLF.
One EDR per employee. When the selected file format supports EVP, that employee's
variable pay is repeated as the next line, split into the seven mandated allowances,
and those seven amounts must add up to the EDR variable amount.
One SCR closes the file. Its total is the sum of every EDR fixed and variable amount.
The file name is the 13 digit establishment id, then YYMMDD and HHMMSS.
"""

from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, now_datetime

EVP_COMPONENTS = (
	"Housing Allowance",
	"Conveyance Allowance",
	"Medical Allowance",
	"Annual Passage Allowance",
	"Overtime Allowance",
	"All Other Allowances",
	"Leave Encashment",
)
EARLIEST_PAY_START = date(2010, 10, 1)
EMPLOYER_ID_WIDTH = 13


def generate_sif_content(doc) -> tuple[bytes, str, datetime]:
	"""Return the SIF bytes and the mandated file name, or throw every problem together."""
	errors: list[str] = []
	company = frappe.db.get_value(
		"Company",
		doc.company,
		["wps_enabled", "mohre_establishment_id"],
		as_dict=True,
	)
	if not company or not cint(company.wps_enabled):
		errors.append(_("Enable WPS on {0} before generating the SIF file.").format(doc.company))

	if doc.currency != "AED":
		errors.append(_("A SIF file pays in AED. This file is {0}.").format(doc.currency or _("blank")))

	employer_id = _employer_id(company.mohre_establishment_id if company else None, errors)
	employer_routing = _employer_routing(doc, errors)
	fmt = _file_format(doc, errors)
	created = now_datetime()
	if not isinstance(created, datetime):
		created = datetime.fromisoformat(str(created))

	employees = list(doc.employees or [])
	if not employees:
		errors.append(_("Fetch salary slips before generating the SIF file."))

	edr_rows: list[str] = []
	seen_ids: dict[str, str] = {}
	total = 0.0
	precision = _precision(fmt, errors)
	for row in employees:
		label = row.employee_name or row.employee or _("an employee")
		person_id = _person_id(row, fmt, label, errors)
		if person_id:
			previous = seen_ids.get(person_id)
			if previous:
				errors.append(
					_("{0} and {1} use the same MOHRE Person ID {2}.").format(previous, label, person_id)
				)
			seen_ids[person_id] = label
		routing = _employee_routing(row, label, errors)
		account = _employee_account(row, fmt, label, errors)
		period = _period(doc, row, label, errors)
		fixed, variable = _amounts(row, precision, label, errors)
		leave_days = _leave_days(row, period, label, errors)
		if (
			not person_id
			or not routing
			or not account
			or period is None
			or fixed is None
			or variable is None
			or leave_days is None
		):
			continue
		start, end, days = period
		total += flt(fixed + variable, precision)
		edr = [
			"EDR",
			person_id,
			routing,
			account,
			start.isoformat(),
			end.isoformat(),
			str(days),
			_money(fixed, precision),
			_money(variable, precision),
			str(leave_days),
		]
		lines = [_join(edr, label, errors)]
		if fmt and cint(fmt.supports_evp) and variable:
			lines.append(_evp_line(row, person_id, routing, variable, precision, label, errors))
		edr_rows.append("\r\n".join(line for line in lines if line))

	salary_month = _salary_month(doc, fmt, errors)
	reference = _reference(doc.payroll_entry)
	if not edr_rows and not errors:
		errors.append(_("The SIF file has no employee records."))
	if errors:
		frappe.throw(errors, as_list=True)

	scr = [
		"SCR",
		employer_id,
		employer_routing,
		created.strftime("%Y-%m-%d"),
		created.strftime("%H%M"),
		salary_month,
		str(len(edr_rows)),
		_money(flt(total, precision), precision),
		"AED",
		reference,
	]
	body = list(edr_rows)
	body.append(_join(scr, _("the salary control record"), errors))
	if errors:
		frappe.throw(errors, as_list=True)
	content = ("\r\n".join(body) + "\r\n").encode("ascii")
	filename = f"{employer_id}{created.strftime('%y%m%d%H%M%S')}.SIF"
	return content, filename, created


def _employer_id(raw, errors: list[str]) -> str:
	value = "".join(ch for ch in str(raw or "") if ch.isdigit())
	if not value:
		errors.append(_("Set the MOHRE Establishment ID on the Company. The SIF file needs the 13 digit id."))
		return ""
	if len(value) > EMPLOYER_ID_WIDTH:
		errors.append(_("MOHRE Establishment ID {0} is longer than 13 digits.").format(value))
		return ""
	return value.zfill(EMPLOYER_ID_WIDTH)


def _employer_routing(doc, errors: list[str]) -> str:
	if not doc.wps_agent:
		errors.append(_("Set the WPS Agent before generating the SIF file."))
		return ""
	routing = frappe.db.get_value("WPS Agent", doc.wps_agent, "routing_code") or ""
	if len(str(routing)) != 9 or not str(routing).isdigit() or set(str(routing)) == {"0"}:
		errors.append(
			_("WPS Agent {0} needs the 9 digit routing code assigned by the Central Bank.").format(doc.wps_agent)
		)
		return ""
	return str(routing)


def _file_format(doc, errors: list[str]):
	if not doc.wps_file_format:
		errors.append(_("Select a WPS File Format before generating the SIF file."))
		return None
	if not frappe.db.exists("WPS File Format", doc.wps_file_format):
		errors.append(_("WPS File Format {0} was not found.").format(doc.wps_file_format))
		return None
	fmt = frappe.get_doc("WPS File Format", doc.wps_file_format)
	if not cint(fmt.is_active):
		errors.append(_("WPS File Format {0} is not active.").format(fmt.name))
	if fmt.wps_agent and doc.wps_agent and fmt.wps_agent != doc.wps_agent:
		errors.append(
			_("WPS File Format {0} belongs to {1}, not {2}.").format(fmt.name, fmt.wps_agent, doc.wps_agent)
		)
	if cint(fmt.requires_encryption):
		errors.append(
			_(
				"WPS File Format {0} requires an encrypted file. This app writes the plain SIF the agent specification defines."
			).format(fmt.name)
		)
	if (fmt.field_delimiter or ",") != ",":
		errors.append(_("The SIF record separator is a comma."))
	if (fmt.date_format or "YYYY-MM-DD") != "YYYY-MM-DD":
		errors.append(_("SIF dates are YYYY-MM-DD."))
	if (fmt.salary_month_format or "MMYYYY") not in {"MMYYYY", "MM-YYYY"}:
		errors.append(_("Salary Month Format must be MMYYYY or MM-YYYY."))
	width = cint(fmt.employer_id_length) or EMPLOYER_ID_WIDTH
	if width != EMPLOYER_ID_WIDTH:
		errors.append(
			_("The SIF file name uses a 13 digit employer id. This format asks for {0}.").format(width)
		)
	return fmt


def _precision(fmt, errors: list[str]) -> int:
	if not fmt or fmt.decimal_precision in (None, ""):
		return 2
	precision = cint(fmt.decimal_precision)
	if precision not in {0, 1, 2}:
		errors.append(_("SIF amounts use at most two decimal places."))
		return 2
	return precision


def _person_id(row, fmt, label: str, errors: list[str]) -> str:
	value = str(row.mohre_person_id or "").strip()
	minimum = cint(fmt.employee_id_min_length) if fmt and fmt.employee_id_min_length not in (None, "") else 14
	maximum = cint(fmt.employee_id_max_length) if fmt and fmt.employee_id_max_length not in (None, "") else 35
	if not value:
		errors.append(
			_("{0} has no MOHRE Person ID. Set it on the Employee and fetch the salary slips again.").format(label)
		)
		return ""
	if not value.isascii() or not value.isalnum():
		errors.append(_("{0}: MOHRE Person ID must be letters and digits only.").format(label))
		return ""
	if value.isdigit() and len(value) < minimum:
		value = value.zfill(minimum)
	if len(value) < minimum or len(value) > maximum:
		errors.append(
			_("{0}: MOHRE Person ID must be {1} to {2} characters.").format(label, minimum, maximum)
		)
		return ""
	return value


def _employee_routing(row, label: str, errors: list[str]) -> str:
	routing = str(row.agent_routing_code or "").strip()
	if len(routing) != 9 or not routing.isdigit() or set(routing) == {"0"}:
		errors.append(
			_("{0} has no 9 digit agent routing code. Set the WPS Agent on the Employee and fetch again.").format(
				label
			)
		)
		return ""
	return routing


def _employee_account(row, fmt, label: str, errors: list[str]) -> str:
	iban = "".join(str(row.employee_iban or "").split()).upper()
	account = "".join(str(row.employee_account_no or "").split())
	minimum = (
		cint(fmt.employee_account_min_length) if fmt and fmt.employee_account_min_length not in (None, "") else 1
	)
	maximum = (
		cint(fmt.employee_account_max_length) if fmt and fmt.employee_account_max_length not in (None, "") else 23
	)
	if iban:
		if not (len(iban) == 23 and iban.startswith("AE") and iban.isalnum()):
			errors.append(_("{0}: Employee IBAN must be a 23 character UAE IBAN.").format(label))
			return ""
		if not minimum <= len(iban) <= maximum:
			errors.append(
				_(
					"{0}: this WPS File Format allows an account of {1} to {2} characters, and the IBAN is 23."
				).format(label, minimum, maximum)
			)
			return ""
		return iban
	if not account or not account.isascii() or not account.isalnum():
		errors.append(
			_("{0} has no employee IBAN or account number. Set it and fetch the salary slips again.").format(label)
		)
		return ""
	if not minimum <= len(account) <= maximum:
		errors.append(
			_("{0}: the account number must be {1} to {2} characters.").format(label, minimum, maximum)
		)
		return ""
	return account


def _period(doc, row, label: str, errors: list[str]):
	if not row.pay_start_date or not row.pay_end_date:
		errors.append(_("{0} has no pay start or pay end date.").format(label))
		return None
	start = getdate(row.pay_start_date)
	end = getdate(row.pay_end_date)
	if start < EARLIEST_PAY_START:
		errors.append(_("{0}: pay cannot start before 2010-10-01.").format(label))
		return None
	if end <= start:
		errors.append(_("{0}: the pay end date must be after the pay start date.").format(label))
		return None
	if (start.year, start.month) != (end.year, end.month):
		errors.append(_("{0}: the pay start and pay end dates must be in the same month.").format(label))
		return None
	salary_month = getdate(doc.salary_month) if doc.salary_month else None
	if not salary_month or (salary_month.year, salary_month.month) != (start.year, start.month):
		errors.append(_("{0}: the file salary month must be the month being paid.").format(label))
		return None
	days = date_diff(end, start) + 1
	if cint(row.days_in_period) != days:
		errors.append(
			_("{0}: days in period must be {1}, the calendar days from pay start through pay end.").format(
				label, days
			)
		)
		return None
	return start, end, days


def _amounts(row, precision: int, label: str, errors: list[str]):
	fixed = flt(row.fixed_amount, precision)
	variable = flt(row.variable_amount, precision)
	if fixed < 0 or variable < 0:
		errors.append(_("{0}: fixed and variable amounts cannot be negative.").format(label))
		return None, None
	if not fixed and not variable:
		errors.append(
			_(
				"{0}: fixed and variable salary are both zero. "
				"Set WPS Salary Category on the salary components and fetch again."
			).format(label)
		)
		return None, None
	if len(_money(fixed, precision)) > 15 or len(_money(variable, precision)) > 15:
		errors.append(_("{0}: a salary amount is longer than 15 characters.").format(label))
		return None, None
	return fixed, variable


def _leave_days(row, period, label: str, errors: list[str]) -> int | None:
	if period is None:
		return None
	value = flt(row.unpaid_leave_days)
	if value != int(value) or value < 0:
		errors.append(_("{0}: unpaid leave days must be a whole number.").format(label))
		return None
	days = int(value)
	if days > period[2]:
		errors.append(_("{0}: unpaid leave days cannot exceed the days in the period.").format(label))
		return None
	return days


def _evp_line(
	row, person_id: str, routing: str, variable: float, precision: int, label: str, errors: list[str]
) -> str:
	buckets = {name: 0.0 for name in EVP_COMPONENTS}
	components = list(row.get("salary_components") or [])
	variable_rows = [component for component in components if component.get("component_type") == "Variable"]
	if not variable_rows:
		errors.append(
			_(
				"{0}: variable pay has no component rows to place on the EVP record. Fetch the salary slips again."
			).format(label)
		)
		return ""
	for component in variable_rows:
		bucket = component.get("evp_component")
		if bucket not in buckets:
			errors.append(
				_(
					"{0}: set WPS EVP Component on {1}. It is variable pay and this format writes an EVP record."
				).format(label, component.get("salary_component"))
			)
			continue
		amount = flt(component.get("amount"), precision)
		if amount < 0:
			errors.append(_("{0}: {1} cannot be negative.").format(label, bucket))
			continue
		buckets[bucket] += amount
	if flt(sum(buckets.values()), precision) != flt(variable, precision):
		errors.append(
			_("{0}: the EVP allowances must add up to the variable amount {1}.").format(
				label, _money(variable, precision)
			)
		)
		return ""
	fields = ["EVP", person_id, routing, *(_money(buckets[name], precision) for name in EVP_COMPONENTS)]
	return _join(fields, label, errors)


def _salary_month(doc, fmt, errors: list[str]) -> str:
	if not doc.salary_month:
		errors.append(_("Set the salary month."))
		return ""
	month = getdate(doc.salary_month)
	style = (fmt.salary_month_format if fmt else None) or "MMYYYY"
	if style == "MM-YYYY":
		return f"{month.month:02d}-{month.year}"
	return f"{month.month:02d}{month.year}"


def _reference(payroll_entry: str) -> str:
	cleaned = "".join(ch for ch in str(payroll_entry or "") if ch.isascii() and ch.isalnum())
	return cleaned[:35]


def _money(value: float, precision: int) -> str:
	return f"{flt(value, precision):.{precision}f}"


def _join(fields: list[str], label: str, errors: list[str]) -> str:
	if any((not field.isascii()) or ("," in field) or ("\r" in field) or ("\n" in field) for field in fields):
		errors.append(_("{0}: a SIF field contains a comma or a character outside ASCII.").format(label))
		return ""
	return ",".join(fields)
