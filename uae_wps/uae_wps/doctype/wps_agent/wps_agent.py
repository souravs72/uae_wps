# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WPSAgent(Document):
	def validate(self):
		if self.routing_code and (len(self.routing_code) != 9 or not str(self.routing_code).isdigit()):
			frappe.throw(_("Routing Code must be the 9 digit code assigned by the Central Bank."))
