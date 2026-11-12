from odoo import _, api, fields, models

# Email domains that never identify an agency
PUBLIC_EMAIL_DOMAINS = {
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'live.com', 'icloud.com', 'aol.com',
    'msn.com', 'protonmail.com', 'yandex.com', 'mail.com', 'gmx.com', 'yahoo.co.uk', 'hotmail.co.uk',
}


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    re_lead_type = fields.Selection(
        [('customer', "End Customer"), ('agency', "Agency"), ('broker', "Broker"), ('investor', "Investor")],
        string="Lead Category", default='customer', tracking=True)
    re_agency_id = fields.Many2one(
        'res.partner', string="Agency", tracking=True, index=True,
        domain=[('re_partner_type', '=', 'agency')])
    re_broker_id = fields.Many2one(
        'res.partner', string="Broker", domain="[('re_partner_type', '=', 'broker')]")
    re_territory_id = fields.Many2one(
        're.territory', string="Territory", related='re_agency_id.re_territory_id', store=True)
    re_agency_state = fields.Selection(related='re_agency_id.re_registration_state',
                                          string="Agency Registration")
    re_project_ids = fields.Many2many('re.project', string="Interested Projects")
    re_admin_reviewed = fields.Boolean(string="Reviewed by Sales Admin", tracking=True, copy=False)
    re_reviewed_by_id = fields.Many2one('res.users', string="Reviewed By", readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        if not self.env.context.get('re_no_auto_assign'):
            leads._re_identify_agency()
            leads._re_auto_assign()
        return leads

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('re_no_auto_assign'):
            if 'partner_id' in vals or 'email_from' in vals:
                self.filtered(lambda l: not l.re_agency_id)._re_identify_agency()
            if 're_agency_id' in vals or 're_lead_type' in vals:
                self._re_auto_assign()
        return res

    def _re_identify_agency(self):
        """Find the agency of the lead from its contact or from its email domain."""
        Partner = self.env['res.partner']
        for lead in self.filtered(lambda l: not l.re_agency_id):
            agency = lead.partner_id.re_agency_id
            if not agency and lead.email_from and '@' in lead.email_from:
                domain = lead.email_from.split('@')[-1].strip().strip('>').lower()
                if domain and domain not in PUBLIC_EMAIL_DOMAINS:
                    agency = Partner.search([
                        ('re_partner_type', '=', 'agency'),
                        '|', ('email', '=ilike', f'%@{domain}'), ('website', 'ilike', domain),
                    ], limit=1)
            if agency:
                lead.with_context(re_no_auto_assign=True).re_agency_id = agency
                if lead.re_lead_type == 'customer' and lead.partner_id.re_partner_type in ('agency', 'broker'):
                    lead.with_context(re_no_auto_assign=True).re_lead_type = lead.partner_id.re_partner_type

    def _re_auto_assign(self):
        """Assign the lead to the agency RM, the territory manager or the broker/investor team."""
        for lead in self:
            vals = {}
            agency = lead.re_agency_id
            company = lead.company_id or self.env.company
            if agency:
                user = agency.re_rm_id or agency.re_territory_id.manager_id
                team = agency.re_territory_id.team_id
                if user:
                    vals['user_id'] = user.id
                if team:
                    vals['team_id'] = team.id
            elif lead.re_lead_type in ('broker', 'investor'):
                team = company.re_broker_team_id if lead.re_lead_type == 'broker' \
                    else company.re_investor_team_id
                if team:
                    vals['team_id'] = team.id
                    if team.user_id:
                        vals['user_id'] = team.user_id.id
            vals = {k: v for k, v in vals.items() if lead[k].id != v}
            if vals:
                lead.with_context(re_no_auto_assign=True).write(vals)
                if 'user_id' in vals:
                    lead.message_post(body=_("Automatically assigned to %s", lead.user_id.name))

    def action_re_mark_reviewed(self):
        self.with_context(re_no_auto_assign=True).write({
            're_admin_reviewed': True,
            're_reviewed_by_id': self.env.user.id,
        })
        self._re_identify_agency()
        self._re_auto_assign()

    def _prepare_opportunity_quotation_context(self):
        ctx = super()._prepare_opportunity_quotation_context()
        ctx['default_re_agency_id'] = self.re_agency_id.id
        ctx['default_re_broker_id'] = self.re_broker_id.id
        if len(self.re_project_ids) == 1:
            ctx['default_re_project_id'] = self.re_project_ids.id
        return ctx
