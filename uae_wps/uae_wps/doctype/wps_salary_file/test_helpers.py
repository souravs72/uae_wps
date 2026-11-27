# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

"""Shared fixtures for WPS salary file and related DocType tests.

Behaviour under test: helpers that build Company/Employee/Payroll/SIF fixtures for
WPS Salary File, WPS Bank Response, WPS Employee Exclusion, and WPS Refund Request.
Existing tests: test_wps_salary_file.py (inline helpers today); no separate helper module yet.
User instruction: "Extract helpers from test_wps_salary_file.py into test_helpers.py"
from the harden plan ("Implement the plan as specified").
"""

import time

import frappe
from frappe.utils import getdate, now_datetime

IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Company",
	"Payroll Entry",
	"Currency",
	"Bank Account",
	"User",
	"Employee",
	"Salary Slip",
	"Salary Component",
	"WPS Bank Response",
	"WPS Employee Exclusion",
	"WPS Refund Request",
	"Item",
]


def company() -> str:
	name = frappe.db.get_value("Company", {}, "name")
	if not name:
		if not frappe.db.exists("Currency", "AED"):
			frappe.get_doc({"doctype": "Currency", "currency_name": "AED", "enabled": 1}).insert(
				ignore_permissions=True
			)
		name = (
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": "WPS Test Company",
					"abbr": "WTC",
					"default_currency": "AED",
					"country": "United Arab Emirates",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	frappe.db.set_value("Company", name, "wps_enabled", 1)
	return name


def agent() -> str:
	name = "WPS Fetch Test Agent"
	if frappe.db.exists("WPS Agent", name):
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "WPS Agent",
				"agent_name": name,
				"agent_type": "Bank",
				"routing_code": "302620122",
			}
		)
		.insert()
		.name
	)


def employee(company_name: str, agent_name: str | None, eligible: int) -> str:
	gender = frappe.db.get_value("Gender", {}, "name")
	if not gender:
		gender = frappe.get_doc({"doctype": "Gender", "gender": "Male"}).insert(ignore_permissions=True).name
	doc = frappe.get_doc(
		{
			"doctype": "Employee",
			"naming_series": "EMP-",
			"first_name": "WPS",
			"last_name": frappe.generate_hash(length=6),
			"company": company_name,
			"status": "Active",
			"gender": gender,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"wps_eligible": eligible,
			"wps_agent": agent_name,
			"mohre_person_id": "12345678901234",
			"iban": "AE070331234567890123456",
			"bank_ac_no": "1234567890123456",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def prepare_sif(wps_file):
	frappe.db.set_value("Company", wps_file.company, "mohre_establishment_id", "1234567890123")
	agent_name = agent()
	wps_file.wps_agent = agent_name
	wps_file.wps_file_format = file_format(agent_name)
	wps_file.currency = "AED"
	wps_file.save()
	frappe.db.set_value("WPS Salary File", wps_file.name, "currency", "AED")
	wps_file.reload()
	return wps_file


def generate_sif(wps_file):
	"""SIF names are second-precision; wait so parallel test generates do not collide."""
	time.sleep(1.1)
	return wps_file.generate_sif()


def file_format(agent_name: str, refund_codes: list[dict] | None = None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "WPS File Format",
			"format_name": f"WPS Test {frappe.generate_hash(length=6)}",
			"wps_agent": agent_name,
			"is_active": 1,
			"refund_reason_codes": refund_codes or [],
		}
	)
	return doc.insert().name


def bank_account(company_name: str, agent_name: str) -> str:
	bank_name = "WPS Test Bank"
	if not frappe.db.exists("Bank", bank_name):
		frappe.get_doc({"doctype": "Bank", "bank_name": bank_name}).insert(ignore_permissions=True)
	gl_account = frappe.db.get_value("Account", {"company": company_name, "account_type": "Bank"}, "name")
	if not gl_account:
		gl_account = frappe.db.get_value("Account", {"company": company_name}, "name")
	account = frappe.get_doc(
		{
			"doctype": "Bank Account",
			"account_name": f"WPS Employer {frappe.generate_hash(length=6)}",
			"bank": bank_name,
			"company": company_name,
			"is_company_account": 1,
			"account": gl_account,
			"iban": "AE070331234567890123456",
			"bank_account_no": "1234567890123456",
			"wps_agent": agent_name,
		}
	)
	account.flags.ignore_mandatory = True
	account.flags.ignore_validate = True
	account.insert(ignore_permissions=True, ignore_mandatory=True)
	return account.name


def component(component_type: str, category: str | None) -> str:
	suffix = frappe.generate_hash(length=6)
	name = f"WPS {component_type} {suffix}"
	return (
		frappe.get_doc(
			{
				"doctype": "Salary Component",
				"salary_component": name,
				"salary_component_abbr": suffix,
				"type": component_type,
				"wps_salary_category": category,
			}
		)
		.insert()
		.name
	)


def empty_file(company_name: str, payroll: str):
	return frappe.get_doc(
		{
			"doctype": "WPS Salary File",
			"company": company_name,
			"payroll_entry": payroll,
			"salary_month": "2026-04-01",
			"currency": "AED",
		}
	).insert()


def draft_payroll(company_name: str) -> str:
	payroll = frappe.get_doc(
		{
			"doctype": "Payroll Entry",
			"company": company_name,
			"posting_date": "2026-04-30",
			"start_date": "2026-04-01",
			"end_date": "2026-04-30",
			"currency": "AED",
			"exchange_rate": 1,
		}
	)
	payroll.flags.ignore_validate = True
	payroll.flags.ignore_mandatory = True
	payroll.insert(ignore_permissions=True, ignore_mandatory=True)
	return payroll.name


def fetched_file():
	company_name = company()
	emp = employee(company_name, agent(), eligible=1)
	basic = component("Earning", "Fixed")
	payroll = mark_submitted(
		"Payroll Entry",
		{
			"doctype": "Payroll Entry",
			"company": company_name,
			"posting_date": "2026-05-31",
			"start_date": "2026-05-01",
			"end_date": "2026-05-31",
			"currency": "AED",
			"exchange_rate": 1,
		},
	)
	mark_submitted(
		"Salary Slip",
		{
			"doctype": "Salary Slip",
			"employee": emp,
			"employee_name": frappe.db.get_value("Employee", emp, "employee_name"),
			"company": company_name,
			"payroll_entry": payroll,
			"posting_date": "2026-05-31",
			"start_date": "2026-05-01",
			"end_date": "2026-05-31",
			"currency": "AED",
			"exchange_rate": 1,
			"payroll_frequency": "Monthly",
			"total_working_days": 31,
			"payment_days": 31,
			"gross_pay": 1000,
			"net_pay": 1000,
			"earnings": [{"salary_component": basic, "amount": 1000}],
		},
	)
	wps_file = frappe.get_doc(
		{
			"doctype": "WPS Salary File",
			"company": company_name,
			"payroll_entry": payroll,
			"salary_month": "2026-05-01",
			"currency": "AED",
		}
	).insert()
	wps_file.fetch_salary_slips()
	wps_file.reload()
	return wps_file


def mark_submitted(doctype: str, values: dict) -> str:
	doc = frappe.get_doc(values)
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	frappe.db.set_value(doctype, doc.name, "docstatus", 1, update_modified=False)
	return doc.name


def backdate_generated_at(wps_file_name: str, when: str = "2026-05-31 12:00:00") -> None:
	"""Allow ACK received_on dates that match May/June payment-status scenarios."""
	frappe.db.set_value("WPS Salary File", wps_file_name, "generated_at", when)


def submit_ack(wps_file_name: str, ack_file_name: str = "ACK123.ACK", received_on: str = "2026-06-02"):
	backdate_generated_at(wps_file_name)
	response = frappe.get_doc(
		{
			"doctype": "WPS Bank Response",
			"wps_salary_file": wps_file_name,
			"response": "ACK",
			"ack_file_name": ack_file_name,
			"received_on": received_on,
		}
	).insert()
	response.submit()
	return response


def today_str() -> str:
	return str(getdate(now_datetime()))
