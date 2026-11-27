# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import json

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "UAE WPS"


def create_wps_custom_fields():
	"""Add WPS fields to HRMS and ERPNext DocTypes that already hold the source data."""
	create_custom_fields(get_custom_fields(), ignore_validate=True)


def get_custom_fields():
	company_bank_filters = json.dumps(
		[
			["Bank Account", "is_company_account", "=", 1],
			["Bank Account", "company", "=", "eval:doc.name"],
		]
	)
	active_agent_filters = json.dumps([["WPS Agent", "is_active", "=", 1]])

	return {
		"Company": [
			{
				"fieldname": "wps_section",
				"fieldtype": "Section Break",
				"label": "Wage Protection System",
				"insert_after": "default_payroll_payable_account",
				"module": MODULE,
			},
			{
				"fieldname": "wps_enabled",
				"fieldtype": "Check",
				"label": "WPS Enabled",
				"insert_after": "wps_section",
				"module": MODULE,
			},
			{
				"fieldname": "mohre_establishment_id",
				"fieldtype": "Data",
				"label": "MOHRE Establishment ID",
				"insert_after": "wps_enabled",
				"description": "Establishment ID assigned by MOHRE.",
				"translatable": 0,
				"module": MODULE,
			},
			{
				"fieldname": "default_wps_bank_account",
				"fieldtype": "Link",
				"label": "Default WPS Bank Account",
				"options": "Bank Account",
				"insert_after": "mohre_establishment_id",
				"link_filters": company_bank_filters,
				"module": MODULE,
			},
		],
		"Employee": [
			{
				"fieldname": "wps_section",
				"fieldtype": "Section Break",
				"label": "Wage Protection",
				"insert_after": "iban",
				"module": MODULE,
			},
			{
				"fieldname": "mohre_person_id",
				"fieldtype": "Data",
				"label": "MOHRE Person ID",
				"insert_after": "wps_section",
				"description": "Person ID on the MOHRE labour card.",
				"translatable": 0,
				"module": MODULE,
			},
			{
				"fieldname": "wps_eligible",
				"fieldtype": "Check",
				"label": "WPS Eligible",
				"insert_after": "mohre_person_id",
				"module": MODULE,
			},
			{
				"fieldname": "wps_agent",
				"fieldtype": "Link",
				"label": "WPS Agent",
				"options": "WPS Agent",
				"insert_after": "wps_eligible",
				"link_filters": active_agent_filters,
				"module": MODULE,
			},
		],
		"Salary Component": [
			{
				"fieldname": "wps_salary_category",
				"fieldtype": "Select",
				"label": "WPS Salary Category",
				"options": "\nFixed\nVariable\nExcluded",
				"insert_after": "type",
				"description": "Leave blank to follow the salary slip: payment-day earnings are Fixed, other earnings and Additional Salary are Variable, and rows kept out of the slip total are Excluded. A value here replaces that.",
				"module": MODULE,
			},
			{
				"fieldname": "wps_evp_component",
				"fieldtype": "Select",
				"label": "WPS EVP Component",
				"options": "\nHousing Allowance\nConveyance Allowance\nMedical Allowance\nAnnual Passage Allowance\nOvertime Allowance\nAll Other Allowances\nLeave Encashment",
				"insert_after": "wps_salary_category",
				"depends_on": "eval:doc.wps_salary_category=='Variable'",
				"description": "Allowance column on the EVP record when the WPS File Format supports EVP. The EDR variable amount remains the total.",
				"module": MODULE,
			},
		],
		"Bank Account": [
			{
				"fieldname": "wps_agent",
				"fieldtype": "Link",
				"label": "WPS Agent",
				"options": "WPS Agent",
				"insert_after": "iban",
				"link_filters": active_agent_filters,
				"description": "Bank or exchange house that submits this account's wages to WPS. IBAN and account number stay on this Bank Account.",
				"module": MODULE,
			},
		],
	}
