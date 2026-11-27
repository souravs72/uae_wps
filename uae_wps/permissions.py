# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe


def has_app_permission() -> bool:
	"""Show UAE WPS on the desk to anyone who can read a salary file."""
	return bool(frappe.has_permission("WPS Salary File", "read"))
