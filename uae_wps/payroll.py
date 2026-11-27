# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Copy a submitted HRMS Payroll Entry into a WPS Salary File.

This follows the same shape as India Payroll's ``populate_deductees``: read
submitted Salary Slips, write a child-table snapshot, and leave payroll
documents unchanged. Amounts are copied. Nothing here recalculates pay.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, cint, date_diff, flt, get_first_day, getdate

LOCKED_STATUSES = ("Generated", "Sent to Bank", "Accepted", "Rejected")
CREDIT_PARENTFIELD = "earnings"
COMPONENT_CATEGORIES = {"Fixed", "Variable", "Excluded"}
PARENTFIELD_ORDER = {"earnings": 0, "deductions": 1, "employer_contributions": 2}


def populate_from_payroll(doc) -> dict:
	"""Replace the file's employee snapshot with submitted slips from its Payroll Entry."""
	if doc.status in LOCKED_STATUSES:
		frappe.throw(
			_("This WPS Salary File is {0}. Fetch is available only while it is Draft or Validated.").format(
				doc.status
			)
		)
	if not isinstance(doc.payroll_entry, str) or not doc.payroll_entry:
		frappe.throw(_("Select a Payroll Entry first."))
	if not isinstance(doc.company, str) or not doc.company:
		frappe.throw(_("Select a Company first."))
	_require_payroll_read(doc.payroll_entry)

	if not frappe.db.get_value("Company", doc.company, "wps_enabled"):
		frappe.throw(_("Enable WPS on {0} before fetching salary slips.").format(doc.company))

	payroll = frappe.db.get_value(
		"Payroll Entry",
		doc.payroll_entry,
		["name", "docstatus", "company", "currency", "bank_account", "start_date", "end_date"],
		as_dict=True,
	)
	if not payroll:
		frappe.throw(_("Payroll Entry {0} was not found.").format(doc.payroll_entry))
	if payroll.docstatus != 1:
		frappe.throw(_("Payroll Entry {0} must be submitted before it can be fetched.").format(payroll.name))
	if payroll.company != doc.company:
		frappe.throw(
			_("Payroll Entry {0} belongs to {1}, not {2}.").format(payroll.name, payroll.company, doc.company)
		)

	_apply_payroll_defaults(doc, payroll)

	submitted = _salary_slips(payroll.name, doc.company, docstatus=1)
	if not submitted:
		drafts = _salary_slips(payroll.name, doc.company, docstatus=0)
		if drafts:
			frappe.throw(
				_("Payroll Entry {0} has {1} draft Salary Slip(s). Submit the slips before fetching.").format(
					payroll.name, len(drafts)
				)
			)
		frappe.throw(_("Payroll Entry {0} has no submitted Salary Slips.").format(payroll.name))

	employee_rows = _employees([slip.employee for slip in submitted])
	accounts = _employee_bank_accounts(list(employee_rows))
	routing_codes = _routing_codes(employee_rows, accounts)
	details = _salary_details([slip.name for slip in submitted])
	categories = _component_categories(details)
	exclusions = _exclusions(doc.company, [slip.employee for slip in submitted], doc.salary_month)

	notes: list[str] = []
	seen_employees: dict[str, int] = defaultdict(int)
	doc.set("employees", [])
	doc.set("omissions", [])
	wages_due = 0.0

	for slip in submitted:
		employee = employee_rows.get(slip.employee)
		gross = flt(slip.gross_pay, 2)
		if not employee:
			notes.append(_("{0}: Employee {1} was not found.").format(slip.name, slip.employee))
			continue

		omit_reason = _omission_reason(slip, employee, exclusions)
		if omit_reason:
			include_in_due = omit_reason == "Salary Withholding"
			if include_in_due:
				wages_due += gross
			doc.append(
				"omissions",
				{
					"employee": employee.name,
					"employee_name": employee.employee_name,
					"salary_slip": slip.name,
					"reason": omit_reason,
					"entitled_amount": gross if include_in_due else 0,
				},
			)
			notes.append(
				_("{0} ({1}): left out because {2}.").format(
					employee.employee_name or employee.name, employee.name, omit_reason
				)
			)
			continue

		seen_employees[employee.name] += 1
		row, row_notes = _employee_snapshot(
			slip,
			employee,
			accounts.get(employee.name),
			routing_codes,
			details.get(slip.name, []),
			categories,
			pay_earnings_total=cint(doc.pay_earnings_total),
		)
		notes.extend(row_notes)
		wages_due += flt(row.get("entitled_amount"), 2)
		doc.append("employees", row)

	if not doc.employees:
		frappe.throw(
			notes or [_("No WPS eligible employees were found on this Payroll Entry.")],
			as_list=True,
		)

	for employee, count in seen_employees.items():
		if count > 1:
			notes.append(
				_("{0}: {1} submitted Salary Slips were found. Both are on this file.").format(
					employee, count
				)
			)

	doc.total_employees = len(doc.employees)
	doc.total_fixed_salary = flt(sum(flt(row.fixed_amount) for row in doc.employees), 2)
	doc.total_variable_salary = flt(sum(flt(row.variable_amount) for row in doc.employees), 2)
	doc.total_salary = flt(doc.total_fixed_salary + doc.total_variable_salary, 2)
	doc.wages_due = flt(wages_due, 2)
	doc.wages_in_file = doc.total_salary
	doc.percent_of_wages_due = (
		flt((doc.wages_in_file / doc.wages_due) * 100, 2) if doc.wages_due else 0
	)
	doc.employees_below_85 = sum(
		1
		for row in doc.employees
		if flt(row.entitled_amount) and flt(row.credit_amount) < flt(row.entitled_amount) * 0.85
	)
	doc.due_date = get_first_day(add_months(getdate(doc.salary_month), 1)) if doc.salary_month else None
	doc.status = "Validated"
	doc.validation_summary = "\n".join(notes)

	return {
		"total_employees": doc.total_employees,
		"total_fixed_salary": doc.total_fixed_salary,
		"total_variable_salary": doc.total_variable_salary,
		"total_salary": doc.total_salary,
		"wages_due": doc.wages_due,
		"percent_of_wages_due": doc.percent_of_wages_due,
		"due_date": doc.due_date,
		"validation_summary": doc.validation_summary,
	}


def write_component_rows(doc) -> None:
	"""Store the component table that sits inside each employee row.

	Saving the WPS Salary File writes the employee rows only. Frappe does not
	save a table that belongs to a child row, so these rows are written here.
	"""
	for employee in doc.employees:
		if not employee.name:
			continue
		_delete_component_rows([employee.name])
		for idx, component in enumerate(employee.get("salary_components") or [], start=1):
			values = component if isinstance(component, dict) else component.as_dict()
			row = frappe.get_doc(
				{
					"doctype": "WPS Salary Component Detail",
					"parent": employee.name,
					"parenttype": "WPS Salary File Employee",
					"parentfield": "salary_components",
					"idx": idx,
					"salary_component": values.get("salary_component"),
					"component_type": values.get("component_type"),
					"evp_component": values.get("evp_component"),
					"amount": values.get("amount"),
				}
			)
			row.db_insert()


def load_component_rows(doc) -> None:
	"""Attach saved component rows so the form and later readers can see them."""
	names = [row.name for row in (doc.employees or []) if row.name]
	if not names:
		return
	rows = frappe.get_all(
		"WPS Salary Component Detail",
		filters={"parent": ["in", names], "parenttype": "WPS Salary File Employee"},
		fields=["name", "parent", "salary_component", "component_type", "evp_component", "amount", "idx"],
		order_by="idx asc",
	)
	grouped: dict[str, list[dict]] = defaultdict(list)
	for row in rows:
		grouped[row.parent].append(row)
	for employee in doc.employees:
		employee.set(
			"salary_components",
			[frappe._dict(component) for component in grouped.get(employee.name, [])],
			as_value=True,
		)


def delete_component_rows(employee_names: list[str]) -> None:
	"""Remove component rows parented to these employee rows and to no other file."""
	names = [name for name in employee_names if isinstance(name, str) and name]
	if not names:
		return
	frappe.db.delete(
		"WPS Salary Component Detail",
		{"parent": ["in", names], "parenttype": "WPS Salary File Employee"},
	)


def _delete_component_rows(employee_names: list[str]) -> None:
	delete_component_rows(employee_names)


def _read_rows(doctype: str, **kwargs):
	"""Read source rows the caller is allowed to see. `get_list` applies user permissions."""
	kwargs["limit_page_length"] = 0
	return frappe.get_list(doctype, **kwargs)


def _require_payroll_read(payroll_entry: str) -> None:
	"""Fetch copies pay, identity, and bank fields, so the caller must be allowed to read them."""
	if not frappe.has_permission("Payroll Entry", "read", payroll_entry):
		frappe.throw(
			_("You need permission to read Payroll Entry {0}.").format(payroll_entry),
			frappe.PermissionError,
		)
	for doctype in ("Salary Slip", "Employee"):
		if not frappe.has_permission(doctype, "read"):
			frappe.throw(
				_("You need permission to read {0} before salary slips can be fetched.").format(doctype),
				frappe.PermissionError,
			)


def _apply_payroll_defaults(doc, payroll) -> None:
	if not doc.currency and payroll.currency:
		doc.currency = payroll.currency
	if not doc.employer_bank_account and payroll.bank_account:
		doc.employer_bank_account = payroll.bank_account
	if not doc.wps_agent and doc.employer_bank_account:
		doc.wps_agent = frappe.db.get_value("Bank Account", doc.employer_bank_account, "wps_agent")
	if not doc.salary_month and payroll.start_date:
		doc.salary_month = getdate(payroll.start_date)


def _salary_slips(payroll_entry: str, company: str, docstatus: int) -> list[dict]:
	return _read_rows(
		"Salary Slip",
		filters={"payroll_entry": payroll_entry, "company": company, "docstatus": docstatus},
		fields=[
			"name",
			"employee",
			"employee_name",
			"start_date",
			"end_date",
			"leave_without_pay",
			"payment_days",
			"gross_pay",
			"net_pay",
			"currency",
			"salary_withholding",
			"salary_withholding_cycle",
		],
		order_by="employee asc, name asc",
	)


def _employees(names: list[str]) -> dict[str, dict]:
	unique = [name for name in dict.fromkeys(names) if name]
	if not unique:
		return {}
	rows = _read_rows(
		"Employee",
		filters={"name": ["in", unique]},
		fields=[
			"name",
			"employee_name",
			"mohre_person_id",
			"wps_eligible",
			"wps_agent",
			"iban",
			"bank_ac_no",
			"date_of_joining",
			"relieving_date",
		],
	)
	return {row.name: row for row in rows}


def _employee_bank_accounts(employees: dict[str, dict]) -> dict[str, dict]:
	names = list(employees)
	if not names:
		return {}
	if not frappe.has_permission("Bank Account", "read"):
		return {}
	rows = _read_rows(
		"Bank Account",
		filters={"party_type": "Employee", "party": ["in", names], "disabled": 0},
		fields=["party", "iban", "bank_account_no", "is_default", "wps_agent"],
		order_by="is_default desc, modified desc",
	)
	chosen: dict[str, dict] = {}
	for row in rows:
		chosen.setdefault(row.party, row)
	return chosen


def _routing_codes(employees: dict[str, dict], accounts: dict[str, dict]) -> dict[str, str]:
	agents = {row.wps_agent for row in employees.values() if row.wps_agent}
	agents.update(row.wps_agent for row in accounts.values() if row.wps_agent)
	agents.discard(None)
	if not agents:
		return {}
	rows = _read_rows(
		"WPS Agent",
		filters={"name": ["in", list(agents)]},
		fields=["name", "routing_code"],
	)
	return {row.name: row.routing_code for row in rows}


def _salary_details(slip_names: list[str]) -> dict[str, list[dict]]:
	if not slip_names:
		return {}
	# Salary Detail is an istable with no DocType permissions. get_list returns
	# nothing useful. Caller must already have passed _require_payroll_read
	# (Salary Slip read). get_all is intentional for the child rows of those slips.
	rows = frappe.get_all(
		"Salary Detail",
		filters={"parent": ["in", slip_names], "parenttype": "Salary Slip"},
		fields=[
			"parent",
			"parentfield",
			"salary_component",
			"amount",
			"idx",
			"depends_on_payment_days",
			"do_not_include_in_total",
			"statistical_component",
			"additional_salary",
		],
		order_by="idx asc",
	)
	grouped: dict[str, list[dict]] = defaultdict(list)
	for row in rows:
		grouped[row.parent].append(row)
	for slip_rows in grouped.values():
		slip_rows.sort(key=lambda detail: (PARENTFIELD_ORDER.get(detail.parentfield, 9), cint(detail.idx)))
	return grouped


def _component_categories(details: dict[str, list[dict]]) -> dict[str, str]:
	names = {
		row.salary_component for slip_rows in details.values() for row in slip_rows if row.salary_component
	}
	if not names:
		return {}
	rows = _read_rows(
		"Salary Component",
		filters={"name": ["in", list(names)]},
		fields=["name", "wps_salary_category", "wps_evp_component"],
	)
	return {row.name: row for row in rows}


def _exclusions(company: str, employees: list[str], salary_month) -> dict[str, str]:
	unique = [name for name in dict.fromkeys(employees) if name]
	if not unique or not salary_month:
		return {}
	if not frappe.has_permission("WPS Employee Exclusion", "read"):
		frappe.throw(
			_("You need permission to read WPS Employee Exclusion before salary slips can be fetched."),
			frappe.PermissionError,
		)
	from frappe.utils import get_last_day

	month = getdate(salary_month)
	month_start = get_first_day(month)
	month_end = get_last_day(month)
	rows = _read_rows(
		"WPS Employee Exclusion",
		filters={
			"company": company,
			"employee": ["in", unique],
			"docstatus": 1,
			"from_date": ["<=", month_end],
			"to_date": [">=", month_start],
		},
		fields=["employee", "reason", "from_date"],
		order_by="from_date desc",
	)
	chosen: dict[str, str] = {}
	for row in rows:
		chosen.setdefault(row.employee, row.reason)
	return chosen


def _omission_reason(slip, employee, exclusions: dict[str, str]) -> str:
	if not cint(employee.wps_eligible):
		return "Not WPS eligible"
	if exclusions.get(employee.name):
		return exclusions[employee.name]
	if slip.salary_withholding:
		released = 0
		if slip.salary_withholding_cycle:
			released = cint(
				frappe.db.get_value(
					"Salary Withholding Cycle", slip.salary_withholding_cycle, "is_salary_released"
				)
			)
		if not released:
			return "Salary Withholding"
	return ""


def _category_for_detail(detail, explicit: str) -> tuple[str, str]:
	"""Use the WPS category when it is set. Otherwise follow the salary slip row.

	HRMS gross pay is the earnings, after payment-day proration, excluding rows
	marked out of the total. Additional Salary and earnings that are not prorated
	are the variable part. An explicit category replaces that reading.
	"""
	if explicit in COMPONENT_CATEGORIES:
		return explicit, ""
	if cint(detail.get("do_not_include_in_total")) or cint(detail.get("statistical_component")):
		return "Excluded", _("the salary slip keeps it out of the total")
	if detail.parentfield != CREDIT_PARENTFIELD:
		return "Excluded", _("it is not an earning")
	if detail.get("additional_salary"):
		return "Variable", _("it comes from an Additional Salary")
	if cint(detail.get("depends_on_payment_days")):
		return "Fixed", _("the salary slip prorates it by payment days")
	return "Variable", _("the salary slip does not prorate it by payment days")


def _pay_period(slip, employee) -> tuple:
	"""Match SalarySlip.actual_start_date / actual_end_date using Employee dates."""
	start = getdate(slip.start_date)
	end = getdate(slip.end_date)
	joining = getdate(employee.date_of_joining) if employee.date_of_joining else None
	relieving = getdate(employee.relieving_date) if employee.relieving_date else None
	if joining and start < joining <= end:
		start = joining
	if relieving and start <= relieving < end:
		end = relieving
	return start, end


def _reduce_to_net(fixed: float, variable: float, net_pay: float, components: list[dict]) -> tuple[float, float, list[dict], str]:
	"""Reduce variable first, then fixed, until fixed + variable equals net pay."""
	target = flt(net_pay, 2)
	current = flt(fixed + variable, 2)
	if current == target:
		return fixed, variable, components, ""
	if current < target:
		return fixed, variable, components, ""

	reduction = flt(current - target, 2)
	new_variable = variable
	new_fixed = fixed
	take_variable = min(new_variable, reduction)
	new_variable = flt(new_variable - take_variable, 2)
	reduction = flt(reduction - take_variable, 2)
	if reduction:
		take_fixed = min(new_fixed, reduction)
		new_fixed = flt(new_fixed - take_fixed, 2)
		reduction = flt(reduction - take_fixed, 2)

	components = _scale_credit_components(components, fixed, variable, new_fixed, new_variable)
	note = _(
		"Fixed plus Variable was reduced from {0} to Net Pay {1} so the SIF credit matches the salary slip."
	).format(current, target)
	return new_fixed, new_variable, components, note


def _scale_credit_components(
	components: list[dict], old_fixed: float, old_variable: float, new_fixed: float, new_variable: float
) -> list[dict]:
	"""Keep EVP buckets in proportion when fixed or variable credit is reduced."""
	scaled = []
	for component in components:
		amount = flt(component.get("amount"), 2)
		component_type = component.get("component_type")
		if component_type == "Fixed" and old_fixed:
			amount = flt(amount * (new_fixed / old_fixed), 2)
		elif component_type == "Variable" and old_variable:
			amount = flt(amount * (new_variable / old_variable), 2)
		row = dict(component)
		row["amount"] = amount
		scaled.append(row)

	# Fix rounding drift on the last Fixed / Variable row so totals still match.
	scaled = _fix_rounding(scaled, "Fixed", new_fixed)
	scaled = _fix_rounding(scaled, "Variable", new_variable)
	return scaled


def _fix_rounding(components: list[dict], component_type: str, target: float) -> list[dict]:
	indexes = [idx for idx, row in enumerate(components) if row.get("component_type") == component_type]
	if not indexes:
		return components
	current = flt(sum(flt(components[idx]["amount"]) for idx in indexes), 2)
	drift = flt(target - current, 2)
	if not drift:
		return components
	last = indexes[-1]
	components[last]["amount"] = flt(flt(components[last]["amount"]) + drift, 2)
	return components


def _employee_snapshot(
	slip,
	employee,
	account,
	routing_codes,
	detail_rows,
	categories,
	pay_earnings_total: int = 0,
) -> tuple[dict, list[str]]:
	notes: list[str] = []
	label = employee.employee_name or employee.name
	agent = employee.wps_agent or (account.wps_agent if account else None)
	iban = employee.iban or (account.iban if account else None)
	account_no = employee.bank_ac_no or (account.bank_account_no if account else None)

	if slip.start_date and slip.end_date and getdate(slip.end_date) < getdate(slip.start_date):
		frappe.throw(_("{0}: Salary Slip {1} ends before it starts.").format(label, slip.name))

	pay_start, pay_end = _pay_period(slip, employee)
	days = date_diff(pay_end, pay_start) + 1

	fixed = 0.0
	variable = 0.0
	components = []
	for detail in detail_rows:
		info = categories.get(detail.salary_component)
		explicit = (info.wps_salary_category if info else "") or ""
		category, reason = _category_for_detail(detail, explicit)
		if reason:
			notes.append(
				_("{0}: {1} has no WPS Salary Category. Counted as {2} because {3}.").format(
					label, detail.salary_component, category, reason
				)
			)

		amount = flt(detail.amount, 2)
		components.append(
			{
				"salary_component": detail.salary_component,
				"component_type": category,
				"evp_component": (info.wps_evp_component or "") if info and category == "Variable" else "",
				"amount": amount,
			}
		)
		if detail.parentfield == CREDIT_PARENTFIELD and category == "Fixed":
			fixed += amount
		elif detail.parentfield == CREDIT_PARENTFIELD and category == "Variable":
			variable += amount
		elif detail.parentfield != CREDIT_PARENTFIELD and category in {"Fixed", "Variable"}:
			notes.append(
				_(
					"{0}: {1} is a {2} classified as {3}. It is stored on the snapshot and left out of the credit totals."
				).format(label, detail.salary_component, detail.parentfield, category)
			)

	fixed = flt(fixed, 2)
	variable = flt(variable, 2)
	net_pay = flt(slip.net_pay, 2)
	entitled = flt(slip.gross_pay, 2)

	if pay_earnings_total:
		if flt(fixed + variable, 2) != net_pay:
			notes.append(
				_(
					"{0}: Salary Slip Net Pay is {1}. Fixed plus Variable on this file is {2}. "
					"Pay Earnings Total is set, so the gap stays on the file."
				).format(label, net_pay, flt(fixed + variable, 2))
			)
	else:
		fixed, variable, components, reduce_note = _reduce_to_net(fixed, variable, net_pay, components)
		if reduce_note:
			notes.append(_("{0}: {1}").format(label, reduce_note))
		elif flt(fixed + variable, 2) != net_pay:
			notes.append(
				_(
					"{0}: Salary Slip Net Pay is {1}. Fixed plus Variable on this file is {2}."
				).format(label, net_pay, flt(fixed + variable, 2))
			)

	credit = flt(fixed + variable, 2)
	return (
		{
			"employee": employee.name,
			"employee_name": employee.employee_name,
			"salary_slip": slip.name,
			"currency": slip.currency,
			"mohre_person_id": employee.mohre_person_id,
			"agent_routing_code": routing_codes.get(agent),
			"employee_account_no": account_no,
			"employee_iban": iban,
			"pay_start_date": pay_start,
			"pay_end_date": pay_end,
			"days_in_period": days,
			"unpaid_leave_days": flt(slip.leave_without_pay),
			"fixed_amount": fixed,
			"variable_amount": variable,
			"net_pay": net_pay,
			"entitled_amount": entitled,
			"credit_amount": credit,
			"salary_components": components,
		},
		notes,
	)
