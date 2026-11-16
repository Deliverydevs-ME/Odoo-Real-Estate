import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .document import normalize_reference

# Legal-form tokens ignored when comparing company names ("ABC Real Estate L.L.C" == "abc real estate llc")
LEGAL_TOKENS = {
    'llc', 'l', 'c', 'soc', 's', 'o', 'fz', 'fze', 'fzco', 'fzc', 'dmcc', 'ltd', 'limited', 'co',
    'company', 'est', 'establishment', 'single', 'owner', 'sole', 'proprietorship', 'br', 'branch',
}
# Additional generic words ignored for the softer "possible duplicate" warning
GENERIC_TOKENS = {'real', 'estate', 'realestate', 'properties', 'property', 'brokers', 'broker',
                  'brokerage', 'realty', 'homes', 'group', 'the', 'and'}

REFERENCE_FIELDS = {
    're_trade_license_no': 'TL',
    're_orn_no': 'ORN',
    're_brn_no': 'BRN',
}


def normalize_company_name(name, loose=False):
    tokens = re.sub(r'[^0-9a-z]+', ' ', (name or '').lower()).split()
    ignored = LEGAL_TOKENS | GENERIC_TOKENS if loose else LEGAL_TOKENS
    return ' '.join(t for t in tokens if t not in ignored)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    re_partner_type = fields.Selection(
        [('agency', "Agency"), ('broker', "Broker"), ('investor', "Investor"), ('customer', "Customer")],
        string="Real Estate Role", index=True, tracking=True)
    re_territory_id = fields.Many2one('re.territory', string="Territory", tracking=True, index=True)
    re_rm_id = fields.Many2one(
        'res.users', string="Relationship Manager", domain=[('share', '=', False)], tracking=True,
        help="Leads coming from this agency are automatically assigned to this user.")
    re_agency_id = fields.Many2one(
        'res.partner', string="Agency", compute='_compute_re_agency_id', store=True, index=True)

    re_trade_license_no = fields.Char(string="Trade License No.", tracking=True, copy=False)
    re_orn_no = fields.Char(string="ORN", tracking=True, copy=False,
                               help="RERA Office Registration Number")
    re_brn_no = fields.Char(string="BRN", tracking=True, copy=False,
                               help="RERA Broker Registration Number")
    re_normalized_name = fields.Char(compute='_compute_re_normalized_name', store=True, index=True)

    re_registration_state = fields.Selection(
        [('draft', "Draft"), ('submitted', "Submitted"), ('active', "Active"),
         ('suspended', "Suspended"), ('cancelled', "Cancelled")],
        string="Registration Status", default='draft', tracking=True, copy=False)
    re_registration_date = fields.Date(string="Registration Date", copy=False)
    re_auto_suspended = fields.Boolean(copy=False,
                                          help="Suspended automatically because a required document expired.")
    re_status_reason = fields.Char(string="Status Reason", copy=False)

    re_document_ids = fields.One2many('re.partner.document', 'partner_id', string="Documents")
    re_missing_document_ids = fields.Many2many(
        're.document.type', compute='_compute_re_document_status', string="Missing Documents")
    re_document_status = fields.Selection(
        [('ok', "Compliant"), ('expiring', "Expiring Soon"), ('expired', "Expired"), ('missing', "Incomplete")],
        compute='_compute_re_document_status', string="Documents Status")
    re_next_expiry_date = fields.Date(compute='_compute_re_document_status', string="Next Expiry")
    re_broker_ids = fields.One2many(
        'res.partner', 'parent_id', string="Brokers", domain=[('re_partner_type', '=', 'broker')])
    re_commission_agreement_ids = fields.One2many(
        're.commission.agreement', 'agency_id', string="Commission Agreements")
    re_commission_count = fields.Integer(compute='_compute_re_commission_count')
    re_possible_duplicate_ids = fields.Many2many(
        'res.partner', compute='_compute_re_possible_duplicates', string="Possible Duplicates")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends('re_partner_type', 'parent_id', 'parent_id.re_partner_type')
    def _compute_re_agency_id(self):
        for partner in self:
            if partner.re_partner_type == 'agency':
                partner.re_agency_id = partner
            elif partner.parent_id.re_partner_type == 'agency':
                partner.re_agency_id = partner.parent_id
            else:
                partner.re_agency_id = False

    re_document_count = fields.Integer(compute='_compute_re_document_count')
    re_document_alert_count = fields.Integer(compute='_compute_re_document_count')

    def _re_document_domain(self):
        self.ensure_one()
        partners = self | self.re_broker_ids if self.re_partner_type == 'agency' else self
        return [('partner_id', 'in', partners.ids)]

    def _compute_re_document_count(self):
        Document = self.env['re.partner.document']
        for partner in self:
            if not partner.id or partner.re_partner_type not in ('agency', 'broker'):
                partner.re_document_count = partner.re_document_alert_count = 0
                continue
            docs = Document.search(partner._re_document_domain())
            partner.re_document_count = len(docs)
            partner.re_document_alert_count = len(docs.filtered(lambda d: d.state in ('expiring', 'expired')))

    def action_re_view_documents(self):
        self.ensure_one()
        is_agency = self.re_partner_type == 'agency'
        return {
            'type': 'ir.actions.act_window',
            'name': _("Documents - %s", self.name),
            'res_model': 're.partner.document',
            'view_mode': 'list,form',
            'views': [(self.env.ref('odoo_dd_real_estate.view_re_partner_document_list_partner').id, 'list'),
                      (False, 'form')],
            'domain': self._re_document_domain(),
            'context': {
                'default_partner_id': self.id,
                'group_by': ['partner_id'] if is_agency and self.re_broker_ids else [],
            },
        }

    def _compute_re_commission_count(self):
        data = dict(self.env['re.commission']._read_group(
            [('agency_id', 'in', self.ids)], ['agency_id'], ['__count']))
        for partner in self:
            partner.re_commission_count = data.get(partner, 0)

    def action_re_view_commissions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Commissions"),
            'res_model': 're.commission',
            'view_mode': 'list,form',
            'domain': [('agency_id', '=', self.id)],
        }

    @api.depends('name')
    def _compute_re_normalized_name(self):
        for partner in self:
            partner.re_normalized_name = normalize_company_name(partner.name)

    @api.depends('re_partner_type', 're_document_ids.state', 're_document_ids.expiry_date',
                 're_document_ids.attachment_ids')
    def _compute_re_document_status(self):
        doc_types = self.env['re.document.type'].search([('is_required', '=', True)])
        for partner in self:
            if partner.re_partner_type not in ('agency', 'broker'):
                partner.re_missing_document_ids = False
                partner.re_document_status = False
                partner.re_next_expiry_date = False
                continue
            required = doc_types.filtered(lambda t: t.applies_to in (partner.re_partner_type, 'both'))
            docs = partner.re_document_ids
            present = docs.filtered(lambda d: d.attachment_ids or d.document_number).type_id
            partner.re_missing_document_ids = required - present
            states = set(docs.filtered(lambda d: d.type_id in required).mapped('state'))
            if 'expired' in states:
                status = 'expired'
            elif partner.re_missing_document_ids or 'missing_date' in states:
                status = 'missing'
            elif 'expiring' in states:
                status = 'expiring'
            else:
                status = 'ok'
            partner.re_document_status = status
            expiries = docs.filtered('expiry_date').mapped('expiry_date')
            partner.re_next_expiry_date = min(expiries) if expiries else False

    @api.depends('name', 're_trade_license_no', 're_orn_no', 'email', 'phone')
    def _compute_re_possible_duplicates(self):
        for partner in self:
            partner.re_possible_duplicate_ids = partner._re_find_duplicates() if partner.is_company else False

    def _re_find_duplicates(self):
        """Return companies that look like the same agency (name, licence, ORN, email, phone)."""
        self.ensure_one()
        record_id = self._origin.id or 0
        domain_or = []
        loose = normalize_company_name(self.name, loose=True)
        if loose:
            first_token = loose.split()[0]
            domain_or.append([('re_normalized_name', 'ilike', first_token)])
        for fname in ('re_trade_license_no', 're_orn_no', 'email', 'phone'):
            if self[fname]:
                domain_or.append([(fname, '=ilike', self[fname].strip())])
        if not domain_or:
            return self.browse()
        candidates = self.with_context(active_test=False).search(
            [('id', '!=', record_id), ('is_company', '=', True)]
            + ['|'] * (len(domain_or) - 1) + [leaf for dom in domain_or for leaf in dom],
            limit=50)

        loose_tokens = set(loose.split())

        def same(partner):
            other_tokens = set(normalize_company_name(partner.name, loose=True).split())
            # "Four Points Properties" ~ "Four Points Infra Real Estate LLC": one name contains the other
            if loose_tokens and other_tokens and (loose_tokens <= other_tokens or other_tokens <= loose_tokens):
                return True
            for fname in ('re_trade_license_no', 're_orn_no'):
                if self[fname] and normalize_reference(partner[fname]) == normalize_reference(self[fname]):
                    return True
            return (self.email and partner.email and partner.email.strip().lower() == self.email.strip().lower()) \
                or (self.phone and partner.phone and re.sub(r'\D', '', partner.phone) == re.sub(r'\D', '', self.phone))

        return candidates.filtered(same)

    # ------------------------------------------------------------------
    # Duplicate prevention
    # ------------------------------------------------------------------

    @api.constrains('name', 'is_company', 're_partner_type', 're_trade_license_no', 're_orn_no',
                    're_brn_no')
    def _check_re_duplicates(self):
        if self.env.context.get('re_skip_duplicate_check'):
            return
        Partner = self.with_context(active_test=False)
        for partner in self:
            for fname, label in (('re_trade_license_no', _("Trade License")), ('re_orn_no', _("ORN")),
                                 ('re_brn_no', _("BRN"))):
                ref = normalize_reference(partner[fname])
                if not ref:
                    continue
                others = Partner.search([(fname, '!=', False), ('id', '!=', partner.id),
                                         ('id', 'not in', partner.child_ids.ids)])
                clash = others.filtered(lambda p: normalize_reference(p[fname]) == ref
                                        and p.commercial_partner_id != partner.commercial_partner_id)
                if clash:
                    raise ValidationError(_(
                        "%(label)s %(ref)s is already registered for %(partner)s. "
                        "Open the existing profile (or merge the records) instead of creating a duplicate.",
                        label=label, ref=partner[fname], partner=clash[0].display_name))
            if partner.is_company and partner.re_partner_type == 'agency' and partner.re_normalized_name:
                clash = Partner.search([('id', '!=', partner.id), ('is_company', '=', True),
                                        ('re_partner_type', '=', 'agency'),
                                        ('re_normalized_name', '=', partner.re_normalized_name)], limit=1)
                if clash:
                    raise ValidationError(_(
                        "An agency named %(name)s already exists. Duplicate agencies are not allowed.",
                        name=clash.display_name))

    @api.onchange('name', 're_trade_license_no', 're_orn_no', 'email', 'phone')
    def _onchange_re_duplicate_warning(self):
        if not self.is_company or not self.name:
            return
        duplicates = self._re_find_duplicates()
        if duplicates:
            return {'warning': {
                'title': _("Possible duplicate"),
                'message': _("Similar records already exist:\n%s\n\nPlease check before saving.",
                             '\n'.join(duplicates[:5].mapped('display_name'))),
            }}

    @api.onchange('re_territory_id')
    def _onchange_re_territory_id(self):
        if self.re_territory_id.manager_id and not self.re_rm_id:
            self.re_rm_id = self.re_territory_id.manager_id

    @api.onchange('parent_id')
    def _onchange_re_parent(self):
        if self.parent_id.re_partner_type == 'agency' and not self.re_partner_type:
            self.re_partner_type = 'broker'
            self.re_territory_id = self.parent_id.re_territory_id
            self.re_rm_id = self.parent_id.re_rm_id

    # ------------------------------------------------------------------
    # References <-> documents
    # ------------------------------------------------------------------

    def _re_reference_for_code(self, code):
        self.ensure_one()
        for fname, ref_code in REFERENCE_FIELDS.items():
            if ref_code == code:
                return self[fname]
        return False

    def _re_sync_references_from_documents(self):
        """Copy the numbers of TL / ORN / BRN documents onto the profile when it has none."""
        for partner in self:
            vals = {}
            for fname, code in REFERENCE_FIELDS.items():
                doc = partner.re_document_ids.filtered(lambda d: d.type_id.code == code and d.document_number)
                if doc and not partner[fname]:
                    vals[fname] = doc[0].document_number
            if vals:
                partner.write(vals)

    # ------------------------------------------------------------------
    # Registration workflow
    # ------------------------------------------------------------------

    def action_re_submit(self):
        self.write({'re_registration_state': 'submitted'})

    def action_re_activate(self):
        for partner in self:
            if partner.re_missing_document_ids:
                raise UserError(_("Missing documents for %(partner)s: %(docs)s",
                                  partner=partner.name,
                                  docs=', '.join(partner.re_missing_document_ids.mapped('name'))))
            if partner.re_document_status == 'expired':
                raise UserError(_("%s has expired documents. Renew them before activating.", partner.name))
            partner.write({
                're_registration_state': 'active',
                're_registration_date': partner.re_registration_date or fields.Date.context_today(self),
                're_auto_suspended': False,
                're_status_reason': False,
            })

    def action_re_suspend(self):
        self.write({'re_registration_state': 'suspended', 're_auto_suspended': False})

    def action_re_cancel(self):
        self.write({'re_registration_state': 'cancelled', 're_auto_suspended': False})

    def action_re_reset(self):
        self.write({'re_registration_state': 'draft', 're_auto_suspended': False,
                    're_status_reason': False})

    def action_re_merge_duplicates(self):
        self.ensure_one()
        partners = self | self.re_possible_duplicate_ids
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'base.partner.merge.automatic.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_model': 'res.partner', 'active_ids': partners.ids},
        }

    def _re_apply_expiry_rule(self):
        """Suspend / cancel active registrations whose required documents expired."""
        for partner in self.filtered(lambda p: p.re_registration_state in ('active', 'submitted')):
            action = partner.company_id.re_expiry_action or self.env.company.re_expiry_action
            expired = partner.re_document_ids.filtered(
                lambda d: d.type_id.is_required and d.state == 'expired').type_id
            reason = _("Expired: %s", ', '.join(expired.mapped('name')))
            partner.write({
                're_registration_state': 'cancelled' if action == 'cancel' else 'suspended',
                're_auto_suspended': action == 'suspend',
                're_status_reason': reason,
            })
            partner.message_post(body=_("Registration automatically %(state)s. %(reason)s",
                                        state=_("cancelled") if action == 'cancel' else _("suspended"),
                                        reason=reason))
            # Brokers of a suspended agency cannot work either
            if partner.re_partner_type == 'agency':
                brokers = partner.re_broker_ids.filtered(lambda b: b.re_registration_state == 'active')
                brokers.write({'re_registration_state': 'suspended', 're_auto_suspended': True,
                               're_status_reason': _("Agency registration %s", reason)})

    def _re_reactivate_if_compliant(self):
        """Re-activate registrations that were suspended automatically once documents are renewed."""
        for partner in self.filtered(lambda p: p.re_auto_suspended and p.re_registration_state == 'suspended'):
            if partner.re_document_status in ('ok', 'expiring'):
                partner.write({'re_registration_state': 'active', 're_auto_suspended': False,
                               're_status_reason': False})
                partner.message_post(body=_("Registration re-activated: documents renewed."))
                if partner.re_partner_type == 'agency':
                    partner.re_broker_ids._re_reactivate_if_compliant()
