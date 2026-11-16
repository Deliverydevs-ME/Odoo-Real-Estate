from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def reconcile(self):
        # Every payment path (register payment, bank reconciliation, manual matching) ends here
        res = super().reconcile()
        invoices = self.move_id.filtered(lambda m: m.move_type == 'out_invoice')
        if invoices:
            orders = self.env['re.installment'].search([('invoice_id', 'in', invoices.ids)]).order_id
            orders |= invoices.invoice_line_ids.sale_line_ids.order_id
            orders.filtered('re_unit_id')._re_update_unit_sold()
        return res
