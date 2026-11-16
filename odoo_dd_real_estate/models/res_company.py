from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    re_broker_team_id = fields.Many2one('crm.team', string="Broker Leads Team")
    re_investor_team_id = fields.Many2one('crm.team', string="Investor Leads Team")
    re_expiry_action = fields.Selection(
        [('suspend', "Suspend registration"), ('cancel', "Cancel registration")],
        string="On Required Document Expiry", default='suspend', required=True)
    re_invoice_days_before = fields.Integer(
        string="Invoice Installments (days before due)", default=7)
    re_reminder_days_before = fields.Integer(
        string="Customer Reminder (days before due)", default=3)
    re_overdue_reminder_interval = fields.Integer(
        string="Overdue Reminder Every (days)", default=7)
    re_auto_post_invoices = fields.Boolean(
        string="Auto-confirm Installment Invoices", default=True)
