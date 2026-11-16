from odoo import api, fields, models


class RealEstateTerritory(models.Model):
    _name = 're.territory'
    _description = "Sales Territory"
    _order = 'name'

    name = fields.Char(required=True)
    code = fields.Char()
    manager_id = fields.Many2one(
        'res.users', string="Territory Manager", domain=[('share', '=', False)],
        help="Default Relationship Manager for agencies of this territory without their own RM.")
    team_id = fields.Many2one('crm.team', string="Sales Team")
    active = fields.Boolean(default=True)
    agency_ids = fields.One2many(
        'res.partner', 're_territory_id', string="Agencies",
        domain=[('re_partner_type', '=', 'agency')])
    agency_count = fields.Integer(compute='_compute_agency_count')

    _sql_constraints = [
        ('code_uniq', 'UNIQUE(code)', "The territory code must be unique."),
    ]

    @api.depends('agency_ids')
    def _compute_agency_count(self):
        for territory in self:
            territory.agency_count = len(territory.agency_ids)

    def action_view_agencies(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'domain': [('re_territory_id', '=', self.id), ('re_partner_type', '=', 'agency')],
            'context': {'default_re_territory_id': self.id, 'default_re_partner_type': 'agency',
                        'default_is_company': True},
        }
