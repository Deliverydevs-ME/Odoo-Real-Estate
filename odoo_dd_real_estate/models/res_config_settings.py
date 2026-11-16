from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    re_broker_team_id = fields.Many2one(
        related='company_id.re_broker_team_id', readonly=False)
    re_investor_team_id = fields.Many2one(
        related='company_id.re_investor_team_id', readonly=False)
    re_expiry_action = fields.Selection(
        related='company_id.re_expiry_action', readonly=False)
    re_invoice_days_before = fields.Integer(
        related='company_id.re_invoice_days_before', readonly=False)
    re_reminder_days_before = fields.Integer(
        related='company_id.re_reminder_days_before', readonly=False)
    re_overdue_reminder_interval = fields.Integer(
        related='company_id.re_overdue_reminder_interval', readonly=False)
    re_auto_post_invoices = fields.Boolean(
        related='company_id.re_auto_post_invoices', readonly=False)
    re_ai_provider = fields.Selection(
        [('anthropic', "Anthropic Claude"), ('openai', "OpenAI"), ('google', "Google Gemini")],
        string="AI Provider", default='anthropic', config_parameter='odoo_dd_real_estate.ai_provider')
    re_ai_model = fields.Char(
        string="AI Model", config_parameter='odoo_dd_real_estate.ai_model',
        help="Leave empty for the provider default (claude-opus-5-5 / gpt-4.1 / gemini-2.5-pro).")
    re_ai_api_key = fields.Char(string="AI API Key", config_parameter='odoo_dd_real_estate.ai_api_key')