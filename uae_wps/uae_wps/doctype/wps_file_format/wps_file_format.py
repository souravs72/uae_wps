# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WPSFileFormat(Document):
	def validate(self):
		pairs = (
			("employee_id_min_length", "employee_id_max_length", _("Employee ID")),
			("employee_account_min_length", "employee_account_max_length", _("Employee Account")),
		)
		for minimum_field, maximum_field, label in pairs:
			minimum = self.get(minimum_field)
			maximum = self.get(maximum_field)
			if minimum is not None and maximum is not None and minimum > maximum:
				frappe.throw(_("{0} minimum length cannot be greater than the maximum.").format(label))
